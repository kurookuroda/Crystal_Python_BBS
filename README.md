# python-bbs　python-bbs-2.zip ( tailwind css 無しは python-bbs.zip )

FastAPI + SQLite + Jinja2 で作る「シンプル掲示板」。
Crystal版チュートリアルの設計思想（型ヒント・外部入力の検証・キーセットページネーション）をPythonに移植したものです。

- スレッド一覧: `LIMIT/OFFSET` 方式（ページ番号ジャンプ）
- レス一覧: キーセット（カーソル）方式（深いページでも速度が一定）
- XSS対策（Jinja2の自動エスケープ）、SQLインジェクション対策（プレースホルダ）、入力長の上限、不正カーソルの無害化

## 必要なもの

- Python 3.10 以上
- SQLite 3.15 以上（行値比較 `(a, b) > (?, ?)` を使うため。通常のPythonに同梱のもので問題ありません）

## セットアップと起動

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

uvicorn main:app --reload
```

ブラウザで http://localhost:8000 を開きます。DBファイル `bbs.db` は初回起動時に自動で作られます。

| 環境変数 | 既定値 | 説明 |
|---|---|---|
| `BBS_DB_PATH` | `bbs.db` | SQLiteファイルのパス |
| `BBS_THEME` | `classic` | デザインテーマ（`classic` / `tailwind`） |
| `BBS_TZ` | `Asia/Tokyo` | 画面に表示する時刻のタイムゾーン（DBにはUTCで保存） |

## デザイン（掲示板風CSS）

見た目は `static/style.css`（素のCSS、外部ライブラリ・JavaScript不要）で決まります。5ちゃんねる風の配色で、レス番号・名前（緑）・日時を1行目に並べ、ダークモードとスマホ幅にも対応しています。色は `style.css` 先頭の `:root` の変数を変えるだけで差し替えられます。

テンプレートは `templates/base.html`（共通レイアウト）を継承しており、どの要素にも `post-head` `thread-item` のような意味のあるクラス名を付けています。そのため、CSSを丸ごと入れ替えるだけで別のデザインにできます。

### Tailwind CSS を使う

```bash
BBS_THEME=tailwind uvicorn main:app --reload
```

`templates/_theme_tailwind.html` が、Tailwind v4 のブラウザ版（CDN）を読み込み、上と同じクラス名に `@apply` でスタイルを割り当てます。`BBS_THEME` を指定しなければ、これまでどおり `style.css` が使われます。

- CDN版は **開発・試作向け** です（実行時にブラウザ内でCSSを生成するため、通信とわずかな描画遅延が発生します）。
- 本番で使うなら、CSSを事前にビルドして配信するのがおすすめです。

```bash
npm i -D tailwindcss @tailwindcss/cli
npx @tailwindcss/cli -i tailwind/input.css -o static/tailwind.css --minify
```

ビルド後は、`templates/base.html` の `<link rel="stylesheet" href="/static/style.css">` を `/static/tailwind.css` に書き換えてください。`tailwind/input.css` は `_theme_tailwind.html` の `<style>` と同じ内容です（片方を変えたら両方そろえます）。

## 開発用コマンド

```bash
python scripts/seed.py               # テストデータ投入（スレッド50件・レス約5000件）。プロジェクトルートで実行
python scripts/benchmark_offset.py   # OFFSET方式とキーセット方式を20万件で比較（bench.dbを生成）

pip install -r requirements-dev.txt
pytest                               # テスト実行（専用の一時DBを使うので bbs.db は変更されません）
```

## 構成

```text
python-bbs/
├── main.py                  # ルーティング、カーソル検証、テーマ切り替え
├── database.py              # SQLite接続の一元管理、テーブル/インデックス作成
├── models.py                # BbsThread / Post（SQLはすべてプレースホルダ）
├── formatting.py            # 日時表示フィルタ（UTC → JST）
├── templates/
│   ├── base.html            # 共通レイアウト
│   ├── index.html / thread.html
│   └── _theme_tailwind.html # Tailwindテーマ（BBS_THEME=tailwind）
├── static/style.css         # 掲示板風CSS（既定）
├── tailwind/input.css       # Tailwindビルド用の入力
├── scripts/
│   ├── seed.py              # シードデータ投入
│   └── benchmark_offset.py  # ベンチマーク
├── tests/                   # pytest
└── .github/workflows/ci.yml # GitHub Actions（Python 3.10〜3.13）
```

## 設計メモ

- **日時**: DBにはUTCで保存し、画面では `BBS_TZ`（既定は日本時間）に変換して表示します。Python側で生成する日時とSQLiteの `DEFAULT` を同じ形式（`YYYY-MM-DD HH:MM:SS.mmm`）に揃えています。
- **キーセット**: `(created_at, id)` の複合比較で並べるので、同一ミリ秒の投稿があっても漏れ・重複が出ません。`posts(thread_id, created_at, id)` のインデックスが前提です。
- **URLパラメータ**: `cursor_id` / `cursor_time` が不正でも422や500にせず、先頭ページにフォールバックします。`/thread/abc` は404です。
- **レス番号**: キーセット方式では「何件目か」をDBから出せないため、URLの `start` で持ち回ります（表示専用なので、改ざんされても検索結果には影響しません）。
- **CSSのURL**: `/static/style.css` というルート相対パスで書いています。`url_for` の絶対URLだと、トンネル経由（https）で `http://` になって読み込めなくなる恐れがあるためです。
- **書き込み**: `execute` + `commit` + `lastrowid` を `Database.write_lock` でひとまとめにしています。

## 既知の制限（本番に出す前に）

- 接続は単一の共有接続です。読み取りは並行できますが、規模が大きくなる場合はリクエスト単位の接続（FastAPIの `Depends`）や `aiosqlite` への移行を検討してください。
- 認証・CSRF対策・投稿の連投制限（レートリミット）はありません。公開する場合はリバースプロキシ側での制限などを追加してください。
- 「前へ」ボタンはありません（キーセット方式は「次へ」向きです）。
- ページネーションの `?page=` は上限を設けていますが、クローラー対策としてのキャッシュ等は入れていません。

## テストについて

テスト実行時に `StarletteDeprecationWarning`（httpx関連）が表示される場合があります。動作には影響しません。
