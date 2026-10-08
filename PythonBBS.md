Crystal版に完全に対応するPython移植版チュートリアルを作成しました。本編と応用編の両方を含み、各StepでCrystalの設計思想をPythonの慣習でどう再現するかを対比しながら解説しています。

---

# 【Python入門】Crystal掲示板の移植版！FastAPIとSQLiteで作る「シンプル掲示板」

## はじめに：なぜこのチュートリアルか？

Crystalのチュートリアルで学んだ「型安全性」「罠の回避」「キーセットページネーション」は、言語を変えても普遍的な設計思想です。本記事では、Crystalで構築した「シンプル掲示板」をPythonに移植し、**FastAPI**（高速でモダンなWebフレームワーク）と標準ライブラリの**sqlite3**、テンプレートエンジン**Jinja2**を使って再構成します。

Pythonは動的型付け言語ですが、**型ヒント (`typing`)**、**データクラス (`@dataclass`)**、**FastAPIの厳格な例外処理**を組み合わせることで、Crystalのチュートリアルで目指した「実行時の罠を設計段階で防ぐ」という思想を十分に再現できます。

### 対象読者

- Crystalチュートリアル（本編・応用編）を読了済みの方
- PythonでのWeb開発に興味がある方
- 「動的言語でも型の思想を活かす方法」を知りたい方

### 完成するプロジェクト構成（本編時点）

```text
python-bbs/
├── main.py            # FastAPIアプリケーションとルーティング
├── database.py        # DB接続の一元管理
├── models.py          # データモデル（ビジネスロジック）
├── requirements.txt   # 依存パッケージ
└── templates/         # HTMLテンプレート（Jinja2）
    ├── index.html
    └── thread.html
```

---

## Step 1: プロジェクトの初期化と依存管理

Pythonでは `pip` と仮想環境で依存を管理します。

```bash
# 仮想環境の作成と有効化（推奨）
python -m venv venv
source venv/bin/activate  # Windowsの場合は venv\Scripts\activate

# 依存パッケージのインストール
pip install fastapi uvicorn jinja2 python-multipart
```

`requirements.txt` を作成しておくと、再現性が高まります。

```text
# requirements.txt
fastapi
uvicorn
jinja2
python-multipart
```

> 💡 **Crystalとの対比**
> Crystalの `shard.yml` に相当するのが `requirements.txt`（または `pyproject.toml`）です。`shards install` に相当するのが `pip install -r requirements.txt` です。

---

## Step 2: データベース接続の一元管理（シングルトンパターン）

Crystalの `@@db` に相当する、スレッドセーフなDB接続管理を実装します。FastAPIはリクエストをスレッドプールで処理するため、接続を一元管理します。

```python
# database.py
import sqlite3
import threading
from typing import Optional


class Database:
    _instance: Optional[sqlite3.Connection] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> sqlite3.Connection:
        if cls._instance is None:
            with cls._lock:
                # 二重チェックロック
                if cls._instance is None:
                    cls._instance = sqlite3.connect(
                        "bbs.db",
                        check_same_thread=False,
                        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
                    )
                    cls._instance.row_factory = sqlite3.Row
                    cls._setup(cls._instance)
        return cls._instance

    @classmethod
    def _setup(cls, db: sqlite3.Connection) -> None:
        db.execute("PRAGMA journal_mode = WAL")

        db.execute("""
            CREATE TABLE IF NOT EXISTS bbs_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                FOREIGN KEY (thread_id) REFERENCES bbs_threads(id)
            )
        """)
        db.commit()

    @classmethod
    def close(cls) -> None:
        if cls._instance:
            cls._instance.close()
            cls._instance = None
```

> 💡 **Crystalとの対比**
> - Pythonにはコンパイル時の型チェックはありませんが、**型ヒント (`-> sqlite3.Connection`)** を付与することで、IDEの補完と静的解析ツール（mypy）の恩恵を受けられます。
> - `sqlite3.Row` を使うと `row["title"]` のようにカラム名でアクセスでき、Crystalの `rs.read(String)` よりも安全で可読性が高くなります。
> - `check_same_thread=False` は、スレッドプールから同一接続を共有するための設定です。`sqlite3` モジュールはネイティブレベルでロックを持つためクラッシュはしませんが、同時トランザクションが競合する可能性があります。本番規模では `aiosqlite` などの非同期ドライバへの移行を検討してください。

> ⚠️ **なぜ `created_at` の DEFAULT が `strftime(...'%f')` なのか？**
> ここには先回りした罠回避が仕込まれています。詳しくは **Step 4** で解説しますが、ポイントは **`%f`（ミリ秒つき）** であること。素朴に `DEFAULT CURRENT_TIMESTAMP` と書くと、後で日時を読むときにフォーマット不一致で問題が起きます。

---

## Step 3: モデルの設計（`dataclass` の活用）

Crystalの `struct` に最も近い概念は、Python標準の `@dataclass` です。不変性を保証したい場合は `frozen=True` を指定します。

```python
# models.py
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List
from database import Database


@dataclass(frozen=True)
class BbsThread:
    id: int
    title: str
    created_at: str  # SQLiteのTEXTとして保持
    post_count: int = 0

    @classmethod
    def all(cls) -> List["BbsThread"]:
        db = Database.get_instance()
        cursor = db.execute("""
            SELECT t.id, t.title, t.created_at, COUNT(p.id) as post_count
            FROM bbs_threads t LEFT JOIN posts p ON t.id = p.thread_id
            GROUP BY t.id ORDER BY t.created_at DESC, t.id DESC
        """)
        return [cls(**row) for row in cursor.fetchall()]

    @classmethod
    def find(cls, thread_id: int) -> Optional["BbsThread"]:
        db = Database.get_instance()
        cursor = db.execute(
            "SELECT id, title, created_at FROM bbs_threads WHERE id = ?",
            (thread_id,),
        )
        row = cursor.fetchone()
        return cls(**row) if row else None

    @classmethod
    def create(cls, title: str) -> int:
        db = Database.get_instance()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        cursor = db.execute(
            "INSERT INTO bbs_threads (title, created_at) VALUES (?, ?)",
            (title, now),
        )
        db.commit()
        return cursor.lastrowid  # ⭕ 同一接続から安全に取得
```

```python
# models.py（続き）
@dataclass(frozen=True)
class Post:
    id: int
    thread_id: int
    name: str
    body: str
    created_at: str

    @classmethod
    def where(cls, thread_id: int) -> List["Post"]:
        db = Database.get_instance()
        cursor = db.execute(
            """SELECT id, thread_id, name, body, created_at
               FROM posts WHERE thread_id = ?
               ORDER BY created_at ASC, id ASC""",
            (thread_id,),
        )
        return [cls(**row) for row in cursor.fetchall()]

    @classmethod
    def create(cls, thread_id: int, name: str, body: str) -> None:
        db = Database.get_instance()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        db.execute(
            "INSERT INTO posts (thread_id, name, body, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, name, body, now),
        )
        db.commit()
```

> 💡 **Crystalとの対比**
> - **`@dataclass(frozen=True)`**: Crystalの `struct`（値型）に相当するイミュータブルなデータ入れ物です。`frozen=True` により、インスタンス生成後の書き換えが物理的に禁止されます。
> - **`cursor.lastrowid`**: Crystalの `result.last_insert_id` と同様、**直前の `execute` と同じ接続**から取得するため、コネクションプール環境でも安全です。
> - **`Optional["BbsThread"]`**: Crystalの `BbsThread?` に相当します。型ヒントにより「見つからなければ `None`」であることが明示され、mypyなどの静的解析ツールは呼び出し側に `None` チェックを強制できます。
> - **`COUNT(p.id)` は `int`**: Pythonの `sqlite3` では SQLite の整数はPythonの `int` として返るため、Crystalのような `Int64` からの変換は不要です。

---

## Step 4: 安全なデータ作成（プレースホルダと `datetime` の罠）

データを**書き込む**メソッドには、2つの重要な実践が詰まっています。

### プレースホルダでSQLインジェクションを防ぐ

```python
db.execute(
    "INSERT INTO posts (thread_id, name, body, created_at) VALUES (?, ?, ?, ?)",
    (thread_id, name, body, now),
)
```

`?` に値をバインドすると、ユーザー入力がSQL構文として解釈されなくなります。文字列連結（f-stringや `+` によるSQL組み立て）は絶対に避けてください。

### ⚠️ 罠その1：日時のフォーマット不一致

Crystal版と同様、Python側で生成した日時文字列と、SQLiteの `DEFAULT` で生成される日時文字列のフォーマットを**寸分違わず一致させる**必要があります。

Pythonの `datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]` はミリ秒3桁を生成します。これをStep 2の `strftime('%Y-%m-%d %H:%M:%f', 'now')`（`%f` は秒以下の小数部分、SQLiteではミリ秒3桁＋マイクロ秒3桁…とやや複雑）と合わせるため、**Python側で明示的に挿入時の日時を渡す**ことで、読み書きのフォーマットを原理的に一致させています。

### ⚠️ 罠その2：`lastrowid` はどう取るか

```python
cursor = db.execute("INSERT INTO bbs_threads (title, created_at) VALUES (?, ?)", (title, now))
return cursor.lastrowid
```

新規作成した行のidを取りたくなりますが、やりがちな失敗はINSERTの直後に別クエリで `SELECT last_insert_rowid()` を発行することです。`Database.get_instance()` が返す接続は共有されますが、**別のリクエストスレッドが同じタイミングでINSERTを実行していた場合**、`last_insert_rowid()` は予期しない値を返す可能性があります。

正解は、`execute` が返す `cursor` から直接 `lastrowid` を読むことです。これなら「今この `execute` でINSERTした行」のIDが確実に得られます。

---

## Step 5: ルーティングとHTTPハンドラ（FastAPI）

Crystalの `halt` による早期リターンと型絞り込みは、Pythonでは **`HTTPException` を `raise` する**ことで実現します。`raise` 以降のコードは実行されないため、型チェッカー（mypy）も「以降はその変数が `None` でない」と正しく推論します。

```python
# main.py
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from database import Database
from models import BbsThread, Post

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# アプリ起動時にDB初期化
Database.get_instance()


@app.get("/")
def index(request: Request, page: Optional[int] = 1):
    page = max(1, page or 1)
    per_page = 20

    # 本編では簡易的に全件取得後スライス（応用編でLIMIT/OFFSETに置き換え）
    threads = BbsThread.all()
    total = len(threads)
    total_pages = max(1, (total + per_page - 1) // per_page)

    start = (page - 1) * per_page
    end = start + per_page
    paged_threads = threads[start:end]

    return templates.TemplateResponse("index.html", {
        "request": request,
        "threads": paged_threads,
        "page": page,
        "total_pages": total_pages,
    })


@app.post("/thread")
def create_thread(title: str = Form(...)):
    title = title.strip()
    if title:
        BbsThread.create(title)
    return RedirectResponse(url="/", status_code=303)


@app.get("/thread/{id}")
def show_thread(
    request: Request,
    id: str,  # あえてstrで受け取り、手動で検証する（Crystal版と同じ設計思想）
    cursor_time: Optional[str] = None,
    cursor_id: Optional[int] = None,
):
    # ⚠️ 罠対策: パスセグメントも外部入力。int変換に失敗したら404
    try:
        thread_id = int(id)
    except ValueError:
        raise HTTPException(status_code=404, detail="スレッドが見つかりません")

    thread = BbsThread.find(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="スレッドが見つかりません")

    # ここ以降、mypyは thread が None でないことを保証する

    posts = Post.where(thread_id)  # 本編では全件取得（応用編でキーセットに置き換え）

    return templates.TemplateResponse("thread.html", {
        "request": request,
        "thread": thread,
        "posts": posts,
    })


@app.post("/thread/{id}/post")
def create_post(
    id: str,
    name: Optional[str] = Form(default=""),
    body: str = Form(...),
):
    try:
        thread_id = int(id)
    except ValueError:
        raise HTTPException(status_code=404, detail="スレッドが見つかりません")

    if BbsThread.find(thread_id) is None:
        raise HTTPException(status_code=404, detail="スレッドが見つかりません")

    name = (name or "").strip() or "名無しさん"
    body = body.strip()

    if body:
        Post.create(thread_id, name, body)

    return RedirectResponse(url=f"/thread/{id}", status_code=303)
```

> 💡 **Crystalとの対比**
> - **`int(id)` と `ValueError`**: Crystalの `to_i32?` に相当します。Pythonには `?` 付きメソッドがないため、`try...except` で例外をキャッチして404を返すのが定石です。
> - **`raise HTTPException`**: Crystalの `halt` に相当します。制御フローがそこで終了するため、その後のコードで `thread` が `None` でないことが静的解析ツールに正しく伝わります。
> - **パスセグメントも外部入力**: `/thread/abc` のようなリクエストで `int()` が失敗する可能性を考慮し、500ではなく404を返します。URLのパス部分とはいえ、ユーザーが自由に書き換えられる点はCrystal版と同じ前提です。
> - **PRGパターン**: POST処理の最後に `RedirectResponse`（303）を返すことで、ブラウザ更新（F5）による二重登録を防ぎます。

---

## Step 6: テンプレートエンジン「Jinja2」とXSS対策

Python標準的なテンプレートエンジン **Jinja2** を使います。RubyのERBとほぼ同じ構文です。

```html
<!-- templates/index.html -->
<!DOCTYPE html>
<html>
<head>
    <title>シンプル掲示板</title>
</head>
<body>
    <h1>スレッド一覧</h1>
    <form action="/thread" method="post">
        <input type="text" name="title" placeholder="スレッドタイトル" required>
        <input type="submit" value="スレッド作成">
    </form>

    <ul>
    {% for thread in threads %}
        <li>
            <a href="/thread/{{ thread.id }}">{{ thread.title }}</a>
            <span>({{ thread.post_count }}レス)</span>
        </li>
    {% endfor %}
    </ul>

    <nav>
        {% if page > 1 %}
            <a href="/?page={{ page - 1 }}">← 前へ</a>
        {% endif %}
        <span>ページ {{ page }} / {{ total_pages }}</span>
        {% if page < total_pages %}
            <a href="/?page={{ page + 1 }}">次へ →</a>
        {% endif %}
    </nav>
</body>
</html>
```

```html
<!-- templates/thread.html -->
<!DOCTYPE html>
<html>
<head>
    <title>{{ thread.title }}</title>
</head>
<body>
    <h1>{{ thread.title }}</h1>
    <a href="/">← 一覧に戻る</a>

    <div class="posts">
    {% for post in posts %}
        <div class="post">
            <strong>{{ post.name }}</strong>
            <small>{{ post.created_at }}</small>
            <p>{{ post.body }}</p>
        </div>
    {% endfor %}
    </div>

    <form action="/thread/{{ thread.id }}/post" method="post">
        <input type="text" name="name" placeholder="名前">
        <textarea name="body" placeholder="本文" required></textarea>
        <input type="submit" value="投稿">
    </form>
</body>
</html>
```

> 💡 **Crystalとの対比**
> - **`{% ... %}`**: コードを実行する（ループ・条件分岐）。**`{{ ... }}`**: 実行結果をHTMLとして出力する。
> - **`HTML.escape` は不要**: Jinja2はデフォルトで**自動エスケープ**が有効です。Crystalで手動で `HTML.escape` を呼んでいた箇所は、テンプレート側で自動的に処理されるため、コードが cleaner になります。

---

## Step 7: 起動と動作確認

エントリポイントは非常にシンプルです。

```bash
# アプリケーションの起動
uvicorn main:app --reload
```

ブラウザで `http://localhost:8000` を開き、**一覧表示 → スレッド作成 → レス投稿**の一連が動くことを確認しましょう。

> ⚠️ **動作確認ポイント**
> `http://localhost:8000/thread/abc` のような無効なIDにアクセスすると、`ValueError` がキャッチされ、**500エラーではなく404エラー**が返ることを確認してください。

---

## 本編まとめ：Pythonで得られるもの

この小さな掲示板を通じて、Pythonの生態系と型の思想を学びました。

1. **型ヒントによる安心感**: 実行時ではなく「書いている時」にIDEやmypyが不整合を指摘でき、意図しない `None` アクセスを減らせます。
2. **`@dataclass(frozen=True)` の活用**: 参照型しかないPythonの中で、イミュータブルなデータ固まりを表現する方法を学びました。
3. **セキュリティの基本**: プレースホルダによるSQLインジェクション対策、Jinja2の自動エスケープによるXSS対策。
4. **「動的言語でも防げない実行時の罠」への対処**: 日時フォーマットの不一致と、共有接続下での `lastrowid` の取り方。**ここがCrystal版からの一貫したテーマ**です。

---

---

# 【Python応用編】掲示板にページネーションを実装する ― OFFSETからキーセット方式へ

## 記事の狙い

前回は「型でも防げない罠」がテーマでした。今回は **「動くだけのコードから、スケールするコードへ」** がテーマです。素朴な `LIMIT`/`OFFSET` を出発点に、なぜそれが破綻するのかを実演し、実務で使われるキーセット（カーソル）方式へ書き換えます。

### 想定読者・前提

- 前回記事（本編のBBSチュートリアル）を実装済みであること
- `BbsThread.all` / `Post.where` が全件取得になっている状態からスタート

---

## Step 1: シードデータ投入スクリプト（問題を体感するための準備）

```python
# scripts/seed.py
import sys
import random
from datetime import datetime, timezone, timedelta

sys.path.append("..")
from database import Database


def main():
    Database.setup()
    db = Database.get_instance()
    print("シードデータを投入中...")

    # スレッドを50件作成
    thread_ids = []
    for i in range(50):
        now = (datetime.now(timezone.utc) - timedelta(hours=(50 - i))).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
        cursor = db.execute(
            "INSERT INTO bbs_threads (title, created_at) VALUES (?, ?)",
            (f"テストスレッド {i + 1}", now),
        )
        thread_ids.append(cursor.lastrowid)

    # 各スレッドに10〜200件のレスをランダムに投入（合計 約5000件）
    for tid in thread_ids:
        for j in range(random.randint(10, 200)):
            now = (datetime.now(timezone.utc) - timedelta(hours=random.randint(0, 49))).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            )[:-3]
            db.execute(
                "INSERT INTO posts (thread_id, name, body, created_at) VALUES (?, ?, ?, ?)",
                (tid, f"名無しさん{j}", f"テスト投稿本文 {j} 番目です。" * random.randint(1, 5), now),
            )

    db.commit()
    count = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    print(f"投入完了。posts総数: {count}")


if __name__ == "__main__":
    main()
```

```bash
python scripts/seed.py
```

> 💡 **学習ポイント**
> - `datetime.now(timezone.utc) - timedelta(hours=N)` で過去日時を作り、`created_at` にばらつきを持たせています（全件同時刻だとソート挙動の確認がしづらいため）。
> - 意図的に投稿数を偏らせて（10〜200件）、大きいスレッドと小さいスレッドの両方をページネーション対象にしています。

---

## Step 2: 素朴な `LIMIT`/`OFFSET` 実装

### モデル層の拡張

```python
# models.py（paginateメソッドを追加）
@dataclass(frozen=True)
class BbsThread:
    # ...（既存のall, find, createはそのまま）

    @classmethod
    def paginate(cls, page: int, per_page: int = 20) -> List["BbsThread"]:
        offset = (page - 1) * per_page
        db = Database.get_instance()
        cursor = db.execute("""
            SELECT t.id, t.title, t.created_at, COUNT(p.id) as post_count
            FROM bbs_threads t LEFT JOIN posts p ON t.id = p.thread_id
            GROUP BY t.id
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT ? OFFSET ?
        """, (per_page, offset))
        return [cls(**row) for row in cursor.fetchall()]

    @classmethod
    def count(cls) -> int:
        db = Database.get_instance()
        cursor = db.execute("SELECT COUNT(*) FROM bbs_threads")
        return cursor.fetchone()[0]


@dataclass(frozen=True)
class Post:
    # ...（既存）

    @classmethod
    def paginate(cls, thread_id: int, page: int, per_page: int = 30) -> List["Post"]:
        offset = (page - 1) * per_page
        db = Database.get_instance()
        cursor = db.execute(
            """SELECT id, thread_id, name, body, created_at
               FROM posts WHERE thread_id = ?
               ORDER BY created_at ASC, id ASC
               LIMIT ? OFFSET ?""",
            (thread_id, per_page, offset),
        )
        return [cls(**row) for row in cursor.fetchall()]

    @classmethod
    def count(cls, thread_id: int) -> int:
        db = Database.get_instance()
        cursor = db.execute(
            "SELECT COUNT(*) FROM posts WHERE thread_id = ?", (thread_id,)
        )
        return cursor.fetchone()[0]
```

### ルーティング側の対応

```python
# main.py（該当箇所を書き換え）
@app.get("/")
def index(request: Request, page: Optional[int] = 1):
    page = max(1, page or 1)
    per_page = 20

    threads = BbsThread.paginate(page, per_page)
    total = BbsThread.count()
    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "threads": threads,
        "page": page,
        "total_pages": total_pages,
    })
```

### テンプレート側：ページネーションUI

```html
<!-- templates/index.html（末尾のnavはそのまま利用可能） -->
```

> 💡 **学習ポイント**
> - `(page - 1) * per_page` で `offset` を計算。page=1のときoffset=0、page=2のときoffset=20…という単純な仕組みです。
> - `total_pages` の計算に整数の切り上げを使っています。`(total + per_page - 1) // per_page` は、Pythonの整数演算で切り上げ除算を行うイディオムです。

---

## Step 3: OFFSETの限界を体感する

### `EXPLAIN QUERY PLAN` で内部動作を見る

```bash
sqlite3 bbs.db
```

```sql
EXPLAIN QUERY PLAN
SELECT id, thread_id, name, body, created_at
FROM posts WHERE thread_id = 5
ORDER BY created_at ASC, id ASC
LIMIT 30 OFFSET 3000;
```

実行結果はおおよそこうなります（インデックス未設定の場合）:

```text
QUERY PLAN
`--SCAN posts
   `--USE TEMP B-TREE FOR ORDER BY
```

`SCAN posts` とある通り、SQLiteは**該当スレッドの全レスをスキャンし、ソートしてから、先頭3000件を読み捨てて**やっと31件目〜60件目を返しています。`OFFSET` はDB側にとって「読んでから捨てる」処理であって、「読まずに飛ばす」処理ではない、というのがこの罠の本質です。

> 💡 **学習ポイント**
> - `OFFSET` が大きいほど「捨てる行」が増える → 計算量はほぼ `O(offset)` で増加します。
> - `LIMIT` だけなら早期に打ち切れますが、`OFFSET` 込みだとその手前まで律儀に処理する必要があります。

### インデックスを張っても本質は変わらない

```sql
CREATE INDEX idx_posts_thread_created ON posts(thread_id, created_at, id);
```

インデックスを張ると `SCAN` は `SEARCH` に変わり定数倍は速くなりますが、**「offset分だけインデックスを辿って読み捨てる」処理自体は残ります**。根本的な非対称性（offsetが大きいほど遅い）は解消しません。

### ベンチマークで実際に体感する

```python
# scripts/benchmark_offset.py
import sys
import time

sys.path.append("..")
from database import Database
from models import Post

Database.setup()

thread_id = 1  # シードで200件近く投稿がある想定のスレッドを選ぶ

for page in [1, 2, 3, 5, 10, 50, 100]:
    start = time.perf_counter()
    Post.paginate(thread_id, page, 30)
    elapsed = (time.perf_counter() - start) * 1000
    offset = (page - 1) * 30
    print(f"page={str(page).rjust(3)}  offset={str(offset).rjust(5)}  {elapsed:.3f}ms")
```

```bash
python scripts/benchmark_offset.py
```

実行結果のイメージ（件数や環境によって変わりますが、傾向として）:

```text
page=  1  offset=    0  0.412ms
page=  2  offset=   30  0.398ms
page=  3  offset=   60  0.455ms
page=  5  offset=  120  0.601ms
page= 10  offset=  270  0.892ms
page= 50  offset= 1470  3.214ms
page=100  offset= 2970  6.887ms
```

> 💡 **学習ポイント**
> - この記事のデータ量（1スレッドあたり最大200件）では数ミリ秒の差にとどまりますが、**offsetと処理時間がほぼ線形に伸びている**のがわかります。データが数十万件規模になると、この傾きがそのまま体感できる遅さに直結します。
> - 掲示板のように「みんな最初の数ページしか見ない」UIでは実害が小さく見えがちですが、**クローラーやbotが `?page=9999` のようなURLを機械的に叩くケース**では、OFFSETの重さがサーバー負荷に直結します。ここが「動いてるから大丈夫」で見過ごされやすい落とし穴です。

### この章のまとめ

- `OFFSET` は「スキップ」ではなく「読み捨て」
- インデックスは軽減できても解決はできない
- 「後ろのページほど遅くなる」という**非対称な性能特性**がキーセット方式へ移行する動機になる

---

## Step 4: キーセットページネーション（カーソル方式）への書き換え

### 考え方の転換

OFFSET方式は「何件目から」という**位置**で指定していましたが、キーセット方式は「どの行より後ろ」という**基準点**で指定します。「前のページの最後の行」を覚えておき、次のクエリでは「それより後ろ」だけを条件で絞り込む、という発想です。

```sql
-- ❌ OFFSET方式: 全部読んでから3000件捨てる
SELECT ... FROM posts WHERE thread_id = ?
ORDER BY created_at ASC, id ASC LIMIT 30 OFFSET 3000;

-- ⭕ キーセット方式: インデックスで一発に絞り込める
SELECT ... FROM posts WHERE thread_id = ?
  AND (created_at > ? OR (created_at = ? AND id > ?))  -- 前ページ最後の行の(created_at, id)
ORDER BY created_at ASC, id ASC LIMIT 30;
```

> 💡 **なぜ `id` もタイブレークに使うのか**
> 本編で `created_at` の DEFAULT を `strftime('%Y-%m-%d %H:%M:%f', 'now')` にしてミリ秒まで持たせたのを覚えているでしょうか。それでも**同一ミリ秒に複数投稿が発生する可能性はゼロではありません**。`created_at` だけで比較すると、同時刻の行の順序が不定になったり、取りこぼしたりする危険があります。`(created_at, id)` の複合比較にすることで、`created_at` が同値でも `id` の昇順で一意に順序が決まり、ページの境界がブレなくなります。これが本編の設計判断がここで回収される部分です。

### モデル層：カーソルベースの `after`

```python
# models.py（キーセット版を追加）
@dataclass(frozen=True)
class Post:
    # ...（既存のwhere, create, paginateはそのまま残す）

    @classmethod
    def after(
        cls,
        thread_id: int,
        cursor_time: Optional[str],
        cursor_id: Optional[int],
        per_page: int = 30,
    ) -> List["Post"]:
        db = Database.get_instance()

        if cursor_time is not None and cursor_id is not None:
            cursor = db.execute(
                """SELECT id, thread_id, name, body, created_at FROM posts
                   WHERE thread_id = ?
                     AND (created_at > ? OR (created_at = ? AND id > ?))
                   ORDER BY created_at ASC, id ASC
                   LIMIT ?""",
                (thread_id, cursor_time, cursor_time, cursor_id, per_page),
            )
        else:
            # カーソルなし = 先頭ページ
            cursor = db.execute(
                """SELECT id, thread_id, name, body, created_at FROM posts
                   WHERE thread_id = ?
                   ORDER BY created_at ASC, id ASC
                   LIMIT ?""",
                (thread_id, per_page),
            )

        return [cls(**row) for row in cursor.fetchall()]

    @classmethod
    def has_more(cls, thread_id: int, last_created_at: str, last_id: int) -> bool:
        db = Database.get_instance()
        cursor = db.execute(
            """SELECT EXISTS(
                 SELECT 1 FROM posts
                 WHERE thread_id = ?
                   AND (created_at > ? OR (created_at = ? AND id > ?))
               )""",
            (thread_id, last_created_at, last_created_at, last_id),
        )
        return cursor.fetchone()[0] == 1
```

> 💡 **学習ポイント**
> - `cursor_time: Optional[str]`, `cursor_id: Optional[int]`: カーソルが「まだ無い（先頭ページ）」状態を `None` で表現しています。Pythonの型ヒントのおかげで、呼び出し側は「カーソルがあるかもしれない/ないかもしれない」を無視できません。
> - `has_more` は別クエリになりますが、`EXISTS` は該当行が1件見つかった時点で走査を打ち切るため、`COUNT(*)` より軽量です。総ページ数は原理的に出せない（それがOFFSET方式との大きな違い）ので、「次へ」ボタンの出し分けだけに使います。

### ルーティング層：カーソルをURLパラメータで受け渡す

「◯ページ目」という概念がなくなるため、URLは `?page=3` ではなく `?cursor_time=...&cursor_id=...` という形になります。

```python
# main.py（スレッド表示ルートを書き換え）
@app.get("/thread/{id}")
def show_thread(
    request: Request,
    id: str,
    cursor_time: Optional[str] = None,
    cursor_id: Optional[int] = None,
):
    # 本編と同じく、:id が数値でなければ try/except + HTTPException で早期リターン
    try:
        thread_id = int(id)
    except ValueError:
        raise HTTPException(status_code=404, detail="スレッドが見つかりません")

    thread = BbsThread.find(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="スレッドが見つかりません")

    # ここ以降、thread は None でないことが保証される

    per_page = 30
    posts = Post.after(thread_id, cursor_time, cursor_id, per_page)

    has_next = False
    if posts:
        last_post = posts[-1]
        has_next = Post.has_more(thread_id, last_post.created_at, last_post.id)

    return templates.TemplateResponse("thread.html", {
        "request": request,
        "thread": thread,
        "posts": posts,
        "has_next": has_next,
        "last_post": posts[-1] if posts else None,
    })
```

> ⚠️ **罠その3：URLパラメータは「ユーザーが自由に改ざんできる」**
> `cursor_time` はURLクエリパラメータです。つまりブラウザだけでなく、クローラーや悪意あるアクセスが `?cursor_time=invalid_string` のような**不正な値**を直接送ってくる可能性を常に考慮する必要があります。
>
> 上のコードでは `cursor_time` を `str` として受け取り、モデル層の `Post.after` でSQLの比較に渡しています。SQLiteはTEXTの比較を行うため、不正な文字列が入ってもSQLエラーにはなりませんが、意図しない順序で結果が返る可能性があります。
>
> より厳密には、`cursor_time` が期待する日時フォーマット（`%Y-%m-%d %H:%M:%S.%f`）にマッチするかを正規表現や `datetime.strptime` で検証し、不正な場合は `None` として「先頭ページにフォールバック」するのが安全です。**「外部から来た値は常に汚染されている可能性がある」** というWeb開発の鉄則を、Pythonの `Optional` 型と例外処理の両方で受け止めている形です。

### テンプレート側：カーソルリンクの生成

```html
<!-- templates/thread.html（末尾に追加） -->
<nav>
  {% if has_next and last_post %}
    <!-- URLエンコードはJinja2の urlencode フィルタが自動で行う -->
    <a href="/thread/{{ thread.id }}?cursor_time={{ last_post.created_at | urlencode }}&cursor_id={{ last_post.id }}">
      次へ →
    </a>
  {% endif %}
</nav>
```

> 💡 **学習ポイント：カーソル値はURLエンコードする**
> `%Y-%m-%d %H:%M:%S.%L` でフォーマットした文字列には**スペース**が含まれます（例: `2026-09-03 12:34:56.789`）。URL内にスペースを裸のまま含めるのはHTTPの作法として厳密には正しくありません。Jinja2の `| urlencode` フィルタでエンコードしてからリンクに埋め込むのが正攻法です。
> ※ `urlencode` はスペースを `%20` または `+` に変換しますが、FastAPI（およびHTTPのクエリパース）は `+` をスペースとして復元するため、ラウンドトリップは正しく成立します。

> 💡 **学習ポイント：トレードオフの明示**
> このUIには「前へ」ボタンがありません。キーセット方式は本質的に**片方向（次へ）が得意**で、「前へ」を実装するには「逆順にLIMIT件取得して、結果をひっくり返す」という別クエリが必要になります（発展課題として次回以降に回せます）。同様に「12ページ目へジャンプ」もできません。**「無限スクロール」や「もっと見る」ボタンのUIとは非常に相性が良い一方、ページ番号ナビゲーションが必要な場面には向かない**、という使い分けがここではっきり見えてきます。

---

## Step 5: OFFSET方式とキーセット方式の使い分け

| 観点 | OFFSET方式 | キーセット方式 |
|---|---|---|
| ページ番号ジャンプ（3ページ目へ） | ◯ できる | ✕ できない（次へ/前へのみ） |
| 総ページ数の表示 | ◯ `COUNT` で出せる | △ 別クエリが必要、頻繁な表示には不向き |
| 後方ページの速度 | ✕ offsetに比例して劣化 | ◯ 常に一定 |
| 実装の複雑さ | ◯ シンプル | △ カーソルの受け渡し・パース処理が増える |
| 途中でデータが増減した場合の一貫性 | ✕ ページがズレる（新規投稿で1件目が繰り上がる等） | ◯ カーソル基準なので影響を受けにくい |

### 使い分けの指針

- **管理画面の検索結果、件数が数百件程度に収まる一覧** → OFFSET方式で十分。ページ番号ジャンプの利便性を優先。
- **掲示板のスレッド一覧・レス一覧、SNSのタイムライン** → キーセット方式。件数が青天井に増える上、ユーザーは基本的に「次へ」しか使わない。
- **検索結果に近いが件数が多い場合（数万件規模）** → ハイブリッド案。先頭数ページ（例: 1〜10ページ）はOFFSETで許容し、それ以降は「もっと見る」ボタンでキーセット方式に切り替える、という設計も現実的。

> 💡 **学習ポイント：「一貫性の崩れ」も実は罠の一種**
> OFFSET方式には性能面だけでなく、**表示内容の一貫性が崩れる**という見落とされがちな問題があります。1ページ目を見ている間に新しいレスが投稿されると、2ページ目に進んだときに「1ページ目で見たはずの投稿」が再度表示されたり、逆に1件飛ばされたりします（offsetは「順位」で切っているため、順位そのものがズレる）。キーセット方式は「前回見た行より後ろ」という相対条件なので、この種のズレが原理的に起きません。これも本編からの一貫したテーマである「動的言語でも静的型付けでも防げない、設計に起因する罠」の一例です。

---

## Step 6: 両モデルへの適用・動作確認・まとめ

### スレッド一覧側もキーセット方式に統一するか？

スレッド一覧（`BbsThread`）側は `GROUP BY` + `COUNT` が絡むため、単純な複合条件比較では書けません（集計後の値でカーソルを組む必要がある）。この記事では:

- **スレッド一覧** → OFFSET方式のまま（Step 2の実装を維持。ページ番号ジャンプの利便性を優先する判断）
- **レス一覧** → キーセット方式（Step 4の実装。件数が青天井に増えるため）

という**使い分けそのものを最終形**として採用します。両方を無理に同じ方式に揃えないのが、Step 5で整理した指針の実践例になっています。

### 最終的なディレクトリ構成（応用編で追加されたファイル）

```text
python-bbs/
├── scripts/
│   ├── seed.py              # 追加: シードデータ投入
│   └── benchmark_offset.py  # 追加: OFFSETベンチマーク
├── templates/
│   ├── index.html
│   └── thread.html          # 変更: キーセット版「次へ」リンク追加
├── database.py
├── models.py                # 変更: paginate, after, has_more 追加
├── main.py                  # 変更: page/cursorパラメータ対応
└── requirements.txt
```

### 通し動作確認の手順

```bash
python scripts/seed.py   # シード投入（スレッド50件、レス計約5000件）
uvicorn main:app --reload # サーバー起動
```

1. `http://localhost:8000` でスレッド一覧を開き、`?page=2` などでOFFSETページネーションが動くことを確認
2. 投稿数の多いスレッドを開き、「次へ」を連打してキーセットページネーションが最後まで正しく進むことを確認
3. `python scripts/benchmark_offset.py` で、OFFSET方式の速度劣化を再確認

### まとめ：応用編で得られたもの

1. **`EXPLAIN QUERY PLAN` でDB内部の動作を可視化する習慣** — 「動いている」と「効率的に動いている」は別物だと体で理解する
2. **OFFSETは読み捨て、キーセットは基準点からの絞り込み** — この違いが性能特性の非対称性を生む
3. **本編の設計判断（ミリ秒つき日時、`lastrowid` の取り方）が応用編で回収される構成** — 一見小さな設計判断が後の機能追加を左右する好例
4. **「どちらか一方が正解」ではなく、用途に応じた使い分けが実務的な結論** — スレッド一覧はOFFSET、レス一覧はキーセットという非対称な採用がそれを体現している

### 次のステップ（さらなる応用へ）

- **「前へ」ボタンの実装**: 逆順クエリ + 結果の反転
- **バリデーション**: Pydantic を使ったリクエストスキーマの厳格化
- **非同期化**: `aiosqlite` を使った非同期DBアクセス
- **設定の外部化**: DBパスやポートを環境変数（`pydantic-settings` など）から読む

---

*(※この記事は Python 3.10+ および FastAPI の最新バージョンをベースに構成しています。ライブラリの仕様は更新される場合があるため、公式ドキュメントも併せてご確認ください。)*

---

以上が、Crystal版に完全対応したPython移植版チュートリアルです。FastAPIの型ヒントと例外処理、Python標準の `sqlite3` と `dataclass` を使うことで、Crystalのチュートリアルで培った設計思想を損なわずに再現できています。

もしこの記事をHTMLファイルやMarkdownファイルとして書き出したい場合は、お知らせください。
