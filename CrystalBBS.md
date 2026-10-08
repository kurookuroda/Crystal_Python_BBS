反映しました。変更点は以下の4箇所です。

| # | 箇所 | 内容 |
|---|---|---|
| ① | 本編 Step 5 | `get /thread/:id` / `post /thread/:id/post` の `:id` を `to_i32?` + `halt` に堅牢化（コメント付き） |
| ② | 本編 Step 5 学習ポイント | 「パスセグメントも外部入力」を追加（`?` による nil セーフとの流れで） |
| ③ | 応用編 Step 4 | 書き換え版ルートを本編に同期（応用編のコードをコピペした読者が 500 に戻らないように） |
| ④ | 応用編ディレクトリ構成 | `routes.cr` の変更コメントから `halt統一` を削除。応用編での実際の変更は page/cursor 対応であり、`:id` の堅牢化は本編側の実装になるため、コメントを実態に合わせました |

「次のステップ」のバリデーション項目は `valid?` のみのままです（`to_i32?` 化を本編で実施したため、堅牢化の文言は不要になりました）。以下、完成版全文です。

---

# 【Crystal入門】Rubyのように書いてCのように動く！Kemalで作る「シンプル掲示板」チュートリアル

## はじめに：なぜCrystalを学ぶのか？

Crystalは **「Rubyのような読みやすい構文」** と **「LLVMでネイティブコンパイルされる実行速度・静的型付けの安全性」** を兼ね備えた言語です。

本チュートリアルでは、軽量Webフレームワーク **Kemal** と **SQLite** を使い、「シンプル掲示板（BBS）」を作りながら、Crystalの言語仕様とWeb開発の基本を体系的に学びます。

単なるコピペ用のコード集ではなく、**動的言語では気づきにくく、静的型付けでも防ぎきれない「実行時の罠」** まで踏み込むのがこの記事の特徴です。実際に開発中にハマる箇所を、その理由とともに解説します。

### 対象読者

- Ruby, Python, JavaScriptなどの動的言語でのWeb開発経験がある方
- 静的型付き言語のメリットや、コンパイル言語でのWebアプリ開発に興味がある方

### 完成するプロジェクト構成（本編時点）

```text
crystal-bbs/
├── shard.yml          # 依存パッケージ管理
├── README.md          # 手順書
└── src/
    ├── main.cr        # エントリポイント
    ├── database.cr    # DB接続の一元管理
    ├── routes.cr      # URLルーティング
    ├── models/        # データモデル（ビジネスロジック）
    │   ├── bbs_thread.cr
    │   └── post.cr
    └── views/         # HTMLテンプレート
        ├── index.ecr
        └── thread.ecr
```

---

## Step 1: プロジェクトの初期化と依存管理

Crystalでは `shards` というパッケージマネージャを使います。プロジェクトのルートに `shard.yml` を作成し、Webフレームワーク「Kemal」とDBドライバ「sqlite3」を宣言します。

```yaml
name: crystal-bbs
version: 0.1.0

dependencies:
  kemal:
    github: kemalcr/kemal
  sqlite3:
    github: crystal-lang/crystal-sqlite3

targets:
  crystal-bbs:
    main: src/main.cr

crystal: 1.21.0
```

> 💡 **学習ポイント**
> `dependencies` を記述後、ターミナルで `shards install` を実行すると、`lib/` にライブラリがダウンロードされ、バージョンを固定する `shard.lock` が自動生成されます。
> なお `sqlite3` を入れると、依存として `crystal-db`（DB共通インターフェース）も同時に入ります。後述の `require "db"` はこれ由来です。

---

## Step 2: データベース接続の一元管理（シングルトンパターン）

Webアプリでは、リクエストごとにDB接続を開閉するのはコストがかかります。そこで、アプリ全体で**1つのコネクションプールを共有する**「シングルトンパターン」を実装します。

> このプールを共有する設計は、後の **Step 4 の「idの取り方の罠」** で重要な伏線になります。頭の片隅に置いておいてください。

```crystal
# src/database.cr
require "db"
require "sqlite3"

class Database
  @@db : DB::Database? = nil

  # どこから呼んでも同じDBインスタンス（プール）を返す
  def self.instance : DB::Database
    @@db ||= DB.open("sqlite3:./bbs.db")
  end

  def self.setup
    db = instance
    db.exec "PRAGMA journal_mode = WAL" # 同時実行性能を上げるWALモード

    db.exec <<-SQL
      CREATE TABLE IF NOT EXISTS bbs_threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
      );
    SQL

    db.exec <<-SQL
      CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
        FOREIGN KEY (thread_id) REFERENCES bbs_threads(id)
      );
    SQL
  end

  def self.close
    @@db.try &.close
    @@db = nil
  end
end
```

> 💡 **学習ポイント**
> - **クラス変数 `@@db`**: クラス全体で共有される変数です。
> - **`||=` 演算子**: 「左辺が `nil` のときだけ右辺を代入する」イディオム。初回だけ `DB.open` が実行され、以降はキャッシュされたプールが使い回されます。
> - **`DB.open` が返すのはプール**: 単一の物理接続ではなく、複数接続を束ねる**コネクションプールの入口**です。ここが後で効いてきます。
> - **`<<-SQL`**: ヒアドキュメント。複数行のSQLを見やすく書けます。

> ⚠️ **なぜ `created_at` の DEFAULT が `strftime(...'%f')` なのか？**
> ここには先回りした罠回避が仕込まれています。詳しくは **Step 4** で解説しますが、ポイントは **`%f`（ミリ秒つき）** であること。素朴に `DEFAULT CURRENT_TIMESTAMP` と書くと、後で `Time` を読むときに例外で落ちます。

---

## Step 3: モデルの設計（`class` と `struct` の使い分け）

Crystalにはオブジェクト指向の `class`（**参照型**）に加え、**`struct`（値型）** があります。DBから取得した「1件のデータ」のような固まりを表現するのには `struct` が向いています。

```crystal
# src/models/bbs_thread.cr
require "../database"

struct BbsThread
  # 読み取り専用データなので getter のみにする
  getter id : Int32
  getter title : String
  getter created_at : Time
  getter post_count : Int32

  def initialize(@id, @title, @created_at, @post_count = 0)
  end

  # スレッド一覧とレス件数を同時に取得
  def self.all : Array(BbsThread)
    threads = [] of BbsThread
    Database.instance.query("
      SELECT t.id, t.title, t.created_at, COUNT(p.id)
      FROM bbs_threads t LEFT JOIN posts p ON t.id = p.thread_id
      GROUP BY t.id ORDER BY t.created_at DESC, t.id DESC
    ") do |rs|
      rs.each do
        threads << BbsThread.new(
          rs.read(Int32),
          rs.read(String),
          rs.read(Time),
          rs.read(Int64).to_i32
        )
      end
    end
    threads
  end

  def self.find(id : Int32) : BbsThread?
    Database.instance.query(
      "SELECT id, title, created_at FROM bbs_threads WHERE id = ?", id
    ) do |rs|
      rs.each do
        return BbsThread.new(rs.read(Int32), rs.read(String), rs.read(Time))
      end
    end
    nil
  end
end
```

> 💡 **学習ポイント：`class`（参照型）と `struct`（値型）の違い**
> - `struct` の本質は **値型（代入・引数渡しでコピーされる）** であること。`class`（参照型：同じ実体を指す）との対比がポイントです。
> - **「`struct` = イミュータブル」ではありません。** `property`（setter）を付ければ `struct` でも値は書き換えられます。ただし値型なので「**コピーを書き換えて元に反映されない**」という罠になりやすく、読み取り専用データでは `getter` のみにするのが安全です。この掲示板の `BbsThread` / `Post` を `getter` にしているのはこの理由です。
> - **メモリ観点**: `struct` はラッパー分のヒープ確保・GC負荷を減らせます。ただし `title : String` の `String` は Crystal では**参照型（ヒープ確保）**なので、文字列本体の割り当てまで消えるわけではありません。減るのは「入れ物」の分です。
> - 実は標準ライブラリの **`Time` 自体も `struct`（値型）** です。「軽量な値の固まりは struct」という設計が言語レベルで実践されている好例です。

> 💡 **学習ポイント：静的型付けならではの書き方**
> - **`Array(BbsThread)`**: 空配列 `[] of BbsThread` のように、「何を入れる配列か」をコンパイラに教えます。
> - **`self.find` の戻り値 `BbsThread?`**: 「見つからなければ `nil`」を型で表現。呼び出し側は nil チェックを**強制される**ため、存在しないデータへのアクセスをコンパイル時に検出できます。
> - **`COUNT(p.id)` は `Int64`**: SQLiteの集計は64bitで返るため `rs.read(Int64).to_i32` で受けています。`read` する型と順番は SELECT の並びと一致させる必要があります。

---

## Step 4: 安全なデータ作成（プレースホルダと `Time.utc`）

データを**書き込む**メソッドを見ていきます。ここには2つの重要な実践が詰まっています。

```crystal
# src/models/bbs_thread.cr（続き）
struct BbsThread
  # ...

  def self.create(title : String) : Int32
    result = Database.instance.exec(
      "INSERT INTO bbs_threads (title, created_at) VALUES (?, ?)",
      title, Time.utc
    )
    result.last_insert_id.to_i32
  end
end
```

```crystal
# src/models/post.cr
require "../database"

struct Post
  getter id : Int32
  getter thread_id : Int32
  getter name : String
  getter body : String
  getter created_at : Time

  def initialize(@id, @thread_id, @name, @body, @created_at)
  end

  def self.where(thread_id : Int32) : Array(Post)
    posts = [] of Post
    Database.instance.query(
      "SELECT id, thread_id, name, body, created_at " \
      "FROM posts WHERE thread_id = ? ORDER BY created_at ASC, id ASC",
      thread_id
    ) do |rs|
      rs.each do
        posts << Post.new(
          rs.read(Int32), rs.read(Int32),
          rs.read(String), rs.read(String), rs.read(Time)
        )
      end
    end
    posts
  end

  def self.create(thread_id : Int32, name : String, body : String)
    Database.instance.exec(
      "INSERT INTO posts (thread_id, name, body, created_at) VALUES (?, ?, ?, ?)",
      thread_id, name, body, Time.utc
    )
  end
end
```

> 💡 **学習ポイント：プレースホルダ `?` でSQLインジェクションを防ぐ**
> `VALUES (?, ?)` の `?` に値をバインドすると、ユーザー入力がSQL構文として解釈されなくなります。文字列連結でSQLを組み立ててはいけません。

### ⚠️ 罠その1：`Time` の読み取りで実行時例外になる

`created_at` を素朴に `DEFAULT CURRENT_TIMESTAMP` にすると、**コンパイルは通るのに実行時に落ちる**という、静的型付けでも防げない罠にハマります。

crystal-sqlite3 は `Time` を `"%F %H:%M:%S.%L"`（**ミリ秒つき**）の形式で読み書きします。ところが SQLite の `CURRENT_TIMESTAMP` が返すのは `"2026-09-03 12:34:56"` の形で、**ミリ秒がありません**。この値を `rs.read(Time)` で読もうとすると `Time::Format::Error` で例外になり、一覧表示の瞬間にクラッシュします。

対策は2つあり、この記事では**両方**を採用しています。

1. **挿入時に `Time.utc` を明示的に渡す**（上のコード）。書き込みも読み戻しも同じ shard 経由になるため、フォーマットが原理的にズレません。
2. **DEFAULT を `strftime('%Y-%m-%d %H:%M:%f', 'now')` にする**（Step 2）。`%f` がミリ秒3桁を出すので、万一 `created_at` 抜きで INSERT されても安全です。

### ⚠️ 罠その2：`last_insert_id` はどう取るか

新規作成した行のidを取りたくなりますが、ここで **Step 2 の「`DB.open` はプール」** という伏線が効いてきます。

やりがちな失敗は、INSERT の直後に別クエリで id を取りに行くことです。

```crystal
# ❌ 危険なパターン
Database.instance.exec("INSERT INTO bbs_threads (title) VALUES (?)", title)
Database.instance.scalar("SELECT last_insert_rowid()").as(Int64) # 別の接続かもしれない！
```

`last_insert_rowid()` は**接続ごとの状態**です。プールでは `exec` と次の `scalar` が別々の接続に割り当てられる可能性があり、その場合 `0` や無関係の値が返ります。

正解は、`exec` の戻り値 `DB::ExecResult` から取ることです。

```crystal
# ⭕ 同一実行の結果から確実に取る
result = Database.instance.exec("INSERT ...", ...)
result.last_insert_id.to_i32
```

これなら「今 INSERT した実行そのもの」の結果なので、プールでも安全です。

---

## Step 5: ルーティングとHTTPハンドラ（Kemal）

Webフレームワーク「Kemal」で、URLと処理を紐付けます。

```crystal
# src/routes.cr
require "kemal"
require "html"
require "uri" # URIエンコード用
require "./database"
require "./models/*"

# トップページ：スレッド一覧
get "/" do
  threads = BbsThread.all
  render "src/views/index.ecr"
end

# スレッド作成
post "/thread" do |env|
  title = env.params.body["title"]?.to_s.strip
  BbsThread.create(title) unless title.empty?
  env.redirect "/" # PRGパターン
end

# スレッド表示
get "/thread/:id" do |env|
  # :id は数値とは限らない（例: /thread/abc）。
  # to_i32 は例外を投げるため、? 版で nil を受け取り halt に繋ぐ
  id = env.params.url["id"].to_i32?
  if id.nil?
    halt env, status_code: 404, response: "スレッドが見つかりません"
  end

  thread = BbsThread.find(id)

  # halt は NoReturn 型を返すため、このブロックを抜ければ
  # 以降の処理で `thread` は nil でないことが型システムにより保証される
  if thread.nil?
    halt env, status_code: 404, response: "スレッドが見つかりません"
  end

  posts = Post.where(id)
  render "src/views/thread.ecr"
end

# レス投稿
post "/thread/:id/post" do |env|
  # :id は数値とは限らない。to_i32? で nil を受け取り halt に繋ぐ
  id = env.params.url["id"].to_i32?
  if id.nil?
    halt env, status_code: 404, response: "スレッドが見つかりません"
  end

  # ここでも明示的なnilチェックとhaltで早期リターン
  if BbsThread.find(id).nil?
    halt env, status_code: 404, response: "スレッドが見つかりません"
  end

  name = env.params.body["name"]?.to_s.strip
  body = env.params.body["body"]?.to_s.strip
  name = "名無しさん" if name.empty?

  Post.create(id, name, body) unless body.empty?
  env.redirect "/thread/#{id}"
end
```

> 💡 **学習ポイント**
> - **`env` オブジェクト**: HTTPリクエスト／レスポンスの情報を持つコンテキスト。
> - **`?` による nil セーフ**: `env.params.body["title"]?` は、キーが無ければ例外ではなく `nil` を返します。`.to_s.strip` と続けて安全に整形できます。
> - **パスセグメントも外部入力**: `env.params.url["id"].to_i32` は `/thread/abc` のような値で `ArgumentError` を投げ、Kemalは500を返してしまいます。`to_i32?` + `halt` にすれば `id` が `Int32` に絞り込まれ、以降は安全です。URLのパス部分とはいえユーザーが自由に書き換えられる点は、応用編の「罠その3」（`cursor_time`）と同じ前提です。
> - **`halt` による型絞り込み**: Kemalの `halt` は `NoReturn` 型を返します。`if thread.nil?` の中で `halt` することで、コンパイラは「この条件分岐以降 `thread` は `nil` ではない」と推論でき、後続の `thread.title` などのアクセスがコンパイルエラーにならなくなります。
> - **PRGパターン**: POST処理の最後に `redirect` することで、ブラウザ更新（F5）による二重登録を防ぎます。

---

## Step 6: テンプレートエンジン「ECR」とXSS対策

Crystal標準のテンプレートエンジン **ECR**（Embedded Crystal）を使います。RubyのERBとほぼ同じ構文です。

```html
<!-- src/views/index.ecr（抜粋） -->
<div class="new-thread">
  <form action="/thread" method="post">
    <input type="text" name="title" placeholder="スレッドタイトル" required>
    <input type="submit" value="スレッド作成">
  </form>
</div>

<ul class="thread-list">
  <% threads.each do |thread| %>
    <li>
      <a href="/thread/<%= thread.id %>"><%= HTML.escape(thread.title) %></a>
      <span>(<%= thread.post_count %>レス)</span>
    </li>
  <% end %>
</ul>
```

```html
<!-- src/views/thread.ecr（抜粋） -->
<% posts.each_with_index do |post, i| %>
  <div class="post">
    <div class="post-header">
      <span class="num"><%= i + 1 %></span>
      <span class="name">名前：<%= HTML.escape(post.name) %></span>
      <span class="date"><%= post.created_at.to_s("%Y-%m-%d %H:%M:%S") %></span>
    </div>
    <div class="post-body"><%= HTML.escape(post.body) %></div>
  </div>
<% end %>
```

> 💡 **学習ポイント**
> - **`<% ... %>`**: コードを実行する（ループ・条件分岐）。**`<%= ... %>`**: 実行結果をHTMLとして出力する。
> - **`render` とローカル変数**: ECRはハンドラのメソッド内に展開されるため、`threads` や `posts` といったローカル変数がそのままテンプレートから見えます。
> - **`HTML.escape` は必須**: 掲示板は誰でも書き込める前提です。`<script>` などが埋め込まれても無害化するよう、**ユーザー入力を出力する箇所はすべてエスケープ**するのが鉄則です。

---

## Step 7: 起動と動作確認

エントリポイントは非常にシンプルです。

```crystal
# src/main.cr
require "./database"
require "./routes"

Database.setup # テーブル作成 + WALモード（受付開始より前に必ず実行）
Kemal.run      # http://localhost:3000 で待ち受け
```

```bash
# 1. 依存パッケージのインストール
shards install

# 2. アプリケーションの起動（プロジェクトルート直下で実行）
crystal run src/main.cr
```

> ⚠️ **実行ディレクトリに注意**
> `render "src/views/index.ecr"` は**カレントディレクトリ基準の相対パス**です。必ず `crystal-bbs/` 直下で `crystal run` してください。別の場所から起動するとテンプレートが見つからず実行時エラーになります。

ブラウザで `http://localhost:3000` を開き、**一覧表示 → スレッド作成 → レス投稿**の一連が動くことを確認しましょう。ついでに `http://localhost:3000/thread/abc` のような存在しないURLを叩いても、500ではなく404が返ることも確認できます。

---

## 本編まとめ：Crystalで得られるもの

この小さな掲示板を通じて、モダンなWeb開発・言語の概念を学びました。

1. **静的型付けによる安心感**: 型の不整合や nil の扱い漏れがコンパイル時に見つかり、実行時エラーが激減します。
2. **値型 (`struct`) の活用**: 参照型 (`class`) との違いを理解し、`getter` で安全に読み取り専用データを表現しました。
3. **セキュリティの基本**: プレースホルダによるSQLインジェクション対策、`HTML.escape` によるXSS対策。
4. **「型でも防げない実行時の罠」への対処**: `Time` のフォーマット不一致と、コネクションプール下での `last_insert_id` の取り方。**ここが動的言語出身者にとって最大の学びどころ**です。

*(※この記事は Crystal 1.21.0 および Kemal の最新バージョンをベースに構成しています。ライブラリの仕様は更新される場合があるため、公式ドキュメントも併せてご確認ください。)*

---
---

# 【Crystal応用編】掲示板にページネーションを実装する ― OFFSETからキーセット方式へ

## 記事の狙い

前回は「型でも防げない罠」がテーマでした。今回は **「動くだけのコードから、スケールするコードへ」** がテーマです。素朴な`LIMIT`/`OFFSET`を出発点に、なぜそれが破綻するのかを実演し、実務で使われるキーセット(カーソル)方式へ書き換えます。

### 想定読者・前提

- 前回記事(本編のBBSチュートリアル)を実装済みであること
- `BbsThread.all` / `Post.where` が全件取得になっている状態からスタート

---

## Step 1: シードデータ投入スクリプト(問題を体感するための準備)

```crystal
# scripts/seed.cr
require "../src/database"

Database.setup

puts "シードデータを投入中..."

# スレッドを50件作成
thread_ids = [] of Int32
50.times do |i|
  result = Database.instance.exec(
    "INSERT INTO bbs_threads (title, created_at) VALUES (?, ?)",
    "テストスレッド #{i + 1}", Time.utc - (50 - i).hours
  )
  thread_ids << result.last_insert_id.to_i32
end

# 各スレッドに10〜200件のレスをランダムに投入(合計 約5000件)
thread_ids.each do |tid|
  rand(10..200).times do |j|
    Database.instance.exec(
      "INSERT INTO posts (thread_id, name, body, created_at) VALUES (?, ?, ?, ?)",
      tid, "名無しさん#{j}", "テスト投稿本文 #{j} 番目です。" * rand(1..5),
      Time.utc - rand(0..49).hours
    )
  end
end

count = Database.instance.scalar("SELECT COUNT(*) FROM posts").as(Int64)
puts "投入完了。posts総数: #{count}"
```

```bash
crystal run scripts/seed.cr
```

> 💡 **学習ポイント**
> - `Time.utc - N.hours`で過去日時を作り、`created_at`にばらつきを持たせています(全件同時刻だとソート挙動の確認がしづらいため)。
> - 意図的に投稿数を偏らせて(10〜200件)、大きいスレッドと小さいスレッドの両方をページネーション対象にしています。

---

## Step 2: 素朴な`LIMIT`/`OFFSET`実装

### モデル層の拡張

```crystal
# src/models/bbs_thread.cr(paginateメソッドを追加)
struct BbsThread
  # ...(既存のall, find, createはそのまま)

  # ページネーション用: 指定ページのスレッド一覧を返す
  def self.paginate(page : Int32, per_page : Int32 = 20) : Array(BbsThread)
    offset = (page - 1) * per_page
    threads = [] of BbsThread
    Database.instance.query("
      SELECT t.id, t.title, t.created_at, COUNT(p.id)
      FROM bbs_threads t LEFT JOIN posts p ON t.id = p.thread_id
      GROUP BY t.id
      ORDER BY t.created_at DESC, t.id DESC
      LIMIT ? OFFSET ?
    ", per_page, offset) do |rs|
      rs.each do
        threads << BbsThread.new(
          rs.read(Int32), rs.read(String), rs.read(Time), rs.read(Int64).to_i32
        )
      end
    end
    threads
  end

  # 総件数(ページ数計算に必要)
  def self.count : Int32
    Database.instance.scalar("SELECT COUNT(*) FROM bbs_threads").as(Int64).to_i32
  end
end
```

```crystal
# src/models/post.cr(paginateメソッドを追加)
struct Post
  # ...(既存)

  def self.paginate(thread_id : Int32, page : Int32, per_page : Int32 = 30) : Array(Post)
    offset = (page - 1) * per_page
    posts = [] of Post
    Database.instance.query(
      "SELECT id, thread_id, name, body, created_at
       FROM posts WHERE thread_id = ?
       ORDER BY created_at ASC, id ASC
       LIMIT ? OFFSET ?",
      thread_id, per_page, offset
    ) do |rs|
      rs.each do
        posts << Post.new(
          rs.read(Int32), rs.read(Int32), rs.read(String), rs.read(String), rs.read(Time)
        )
      end
    end
    posts
  end

  def self.count(thread_id : Int32) : Int32
    Database.instance.scalar(
      "SELECT COUNT(*) FROM posts WHERE thread_id = ?", thread_id
    ).as(Int64).to_i32
  end
end
```

### ルーティング側の対応

```crystal
# src/routes.cr(該当箇所を書き換え)
get "/" do |env|
  # to_i32? を使い、不正な値（例: ?page=abc）が来た場合は nil を返して 1 にフォールバック
  page = env.params.query["page"]?.try(&.to_i32?) || 1
  page = 1 if page < 1

  per_page = 20
  threads = BbsThread.paginate(page, per_page)
  total = BbsThread.count
  total_pages = (total / per_page.to_f).ceil.to_i

  render "src/views/index.ecr"
end
```

### ECR側:ページネーションUI

```html
<!-- src/views/index.ecr(末尾に追加) -->
<nav class="pagination">
  <% if page > 1 %>
    <a href="/?page=<%= page - 1 %>">← 前へ</a>
  <% end %>

  <span>ページ <%= page %> / <%= total_pages %></span>

  <% if page < total_pages %>
    <a href="/?page=<%= page + 1 %>">次へ →</a>
  <% end %>
</nav>
```

> 💡 **学習ポイント**
> - **`(page - 1) * per_page`でoffsetを計算**。page=1のときoffset=0、page=2のときoffset=20…という単純な仕組みです。
> - **`total_pages`の計算に`.to_f`**: 整数同士の割り算だと切り捨てになるため、`Float`にキャストしてから`.ceil`(切り上げ)しています。例えば45件÷20件=2.25→切り上げで3ページ。
> - **クエリパラメータの取得**: `env.params.query["page"]?.try(&.to_i32?)` で「nilでなければInt32に変換、失敗したらnil」というチェーンになっています。失敗しうる変換には `?` 版のメソッドを使うのが、Crystalの慣習であり安全な書き方です。

---

## Step 3: OFFSETの限界を体感する

### まずは`EXPLAIN QUERY PLAN`で内部動作を見る

```sql
-- sqlite3 bbs.db で実行
EXPLAIN QUERY PLAN
SELECT id, thread_id, name, body, created_at
FROM posts WHERE thread_id = 5
ORDER BY created_at ASC, id ASC
LIMIT 30 OFFSET 3000;
```

実行結果はおおよそこうなります(インデックス未設定の場合):

```text
QUERY PLAN
`--SCAN posts
   `--USE TEMP B-TREE FOR ORDER BY
```

`SCAN posts`とある通り、SQLiteは**該当スレッドの全レスをスキャンし、ソートしてから、先頭3000件を読み捨てて**やっと31件目〜60件目を返しています。`OFFSET`はDB側にとって「読んでから捨てる」処理であって、「読まずに飛ばす」処理ではない、というのがこの罠の本質です。

> 💡 **学習ポイント**
> - `OFFSET`が大きいほど「捨てる行」が増える → 計算量はほぼ`O(offset)`で増加します。
> - `LIMIT`だけなら早期に打ち切れますが、`OFFSET`込みだとその手前まで律儀に処理する必要があります。

### インデックスを張っても本質は変わらない

```sql
CREATE INDEX idx_posts_thread_created ON posts(thread_id, created_at, id);
```

インデックスを張ると`SCAN`は`SEARCH`に変わり定数倍は速くなりますが、**「offset分だけインデックスを辿って読み捨てる」処理自体は残ります**。根本的な非対称性(offsetが大きいほど遅い)は解消しません。

### ベンチマークで実際に体感する

```crystal
# scripts/benchmark_offset.cr
require "../src/database"
require "../src/models/*"

Database.setup

thread_id = 1 # シードで200件近く投稿がある想定のスレッドを選ぶ

[1, 2, 3, 5, 10, 50, 100].each do |page|
  start = Time.monotonic
  Post.paginate(thread_id, page, 30)
  elapsed = Time.monotonic - start
  puts "page=#{page.to_s.rjust(3)}  offset=#{((page - 1) * 30).to_s.rjust(5)}  #{elapsed.total_milliseconds.round(3)}ms"
end
```

```bash
crystal run scripts/benchmark_offset.cr
```

実行結果のイメージ(件数や環境によって変わりますが、傾向として):

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
> - この記事のデータ量(1スレッドあたり最大200件)では数ミリ秒の差にとどまりますが、**offsetと処理時間がほぼ線形に伸びている**のがわかります。データが数十万件規模になると、この傾きがそのまま体感できる遅さに直結します。
> - 掲示板のように「みんな最初の数ページしか見ない」UIでは実害が小さく見えがちですが、**クローラーやbotが`?page=9999`のようなURLを機械的に叩くケース**では、OFFSETの重さがサーバー負荷に直結します。ここが「動いてるから大丈夫」で見過ごされやすい落とし穴です。

### この章のまとめ

- `OFFSET`は「スキップ」ではなく「読み捨て」
- インデックスは軽減できても解決はできない
- 「後ろのページほど遅くなる」という**非対称な性能特性**がキーセット方式へ移行する動機になる

---

## Step 4: キーセットページネーション(カーソル方式)への書き換え

### 考え方の転換

OFFSET方式は「何件目から」という**位置**で指定していましたが、キーセット方式は「どの行より後ろ」という**基準点**で指定します。「前のページの最後の行」を覚えておき、次のクエリでは「それより後ろ」だけを条件で絞り込む、という発想です。

```sql
-- ❌ OFFSET方式:全部読んでから3000件捨てる
SELECT ... FROM posts WHERE thread_id = ?
ORDER BY created_at ASC, id ASC LIMIT 30 OFFSET 3000;

-- ⭕ キーセット方式:インデックスで一発に絞り込める
SELECT ... FROM posts WHERE thread_id = ?
  AND (created_at, id) > (?, ?)  -- 前ページ最後の行の(created_at, id)
ORDER BY created_at ASC, id ASC LIMIT 30;
```

> 💡 **なぜ`id`もタイブレークに使うのか**
> 本編でcreated_atのDEFAULTを`strftime('%Y-%m-%d %H:%M:%f', 'now')`にしてミリ秒まで持たせたのを覚えているでしょうか。それでも**同一ミリ秒に複数投稿が発生する可能性はゼロではありません**。`created_at`だけで比較すると、同時刻の行の順序が不定になったり、取りこぼしたりする危険があります。`(created_at, id)`の複合比較にすることで、created_atが同値でもidの昇順で一意に順序が決まり、ページの境界がブレなくなります。これが本編の設計判断がここで回収される部分です。

### SQLiteでのタプル比較の注意点

Crystalの`sqlite3`シャード経由では、`(created_at, id) > (?, ?)`という**行値式(row value)比較**がそのまま使えます(SQLite自体が対応しているため)。ただし可読性と移植性を考え、この記事では展開した形で書きます。

```sql
-- 実質的に同じ意味だが、他のDBでも動く書き方
WHERE (created_at > ?) OR (created_at = ? AND id > ?)
```

### モデル層:カーソルベースの`after`

```crystal
# src/models/post.cr(キーセット版を追加)
struct Post
  # ...(既存のwhere, create, paginateはそのまま残す)

  # カーソル(前ページ最後の行のcreated_at + id)より後ろをper_page件取得
  def self.after(thread_id : Int32, cursor_time : Time?, cursor_id : Int32?, per_page : Int32 = 30) : Array(Post)
    posts = [] of Post

    if cursor_time && cursor_id
      Database.instance.query(
        "SELECT id, thread_id, name, body, created_at FROM posts
         WHERE thread_id = ?
           AND (created_at > ? OR (created_at = ? AND id > ?))
         ORDER BY created_at ASC, id ASC
         LIMIT ?",
        thread_id, cursor_time, cursor_time, cursor_id, per_page
      ) do |rs|
        rs.each do
          posts << Post.new(
            rs.read(Int32), rs.read(Int32), rs.read(String), rs.read(String), rs.read(Time)
          )
        end
      end
    else
      # カーソルなし = 先頭ページ
      Database.instance.query(
        "SELECT id, thread_id, name, body, created_at FROM posts
         WHERE thread_id = ?
         ORDER BY created_at ASC, id ASC
         LIMIT ?",
        thread_id, per_page
      ) do |rs|
        rs.each do
          posts << Post.new(
            rs.read(Int32), rs.read(Int32), rs.read(String), rs.read(String), rs.read(Time)
          )
        end
      end
    end

    posts
  end

  # 「次ページがあるか」を判定するため、条件を満たす行が1件でもあるかをEXISTSで確認
  def self.has_more?(thread_id : Int32, last_created_at : Time, last_id : Int32) : Bool
    Database.instance.scalar(
      "SELECT EXISTS(
         SELECT 1 FROM posts
         WHERE thread_id = ?
           AND (created_at > ? OR (created_at = ? AND id > ?))
       )",
      thread_id, last_created_at, last_created_at, last_id
    ).as(Int64) == 1
  end
end
```

> 💡 **学習ポイント**
> - `cursor_time : Time?` `cursor_id : Int32?`:カーソルが「まだ無い(先頭ページ)」状態を`nil`で表現しています。Crystalの型システムのおかげで、呼び出し側は「カーソルがあるかもしれない/ないかもしれない」を無視できません。
> - `has_more?`は別クエリになりますが、`EXISTS`は該当行が1件見つかった時点で走査を打ち切るため、`COUNT(*)`より軽量です。総ページ数は原理的に出せない(それがOFFSET方式との大きな違い)ので、「次へ」ボタンの出し分けだけに使います。

### ルーティング層:カーソルをURLパラメータで受け渡す

「◯ページ目」という概念がなくなるため、URLは`?page=3`ではなく`?cursor_time=...&cursor_id=...`という形になります。

```crystal
# src/routes.cr(スレッド表示ルートを書き換え)
get "/thread/:id" do |env|
  # 本編と同じく、:id が数値でなければ to_i32? + halt で早期リターン
  id = env.params.url["id"].to_i32?
  if id.nil?
    halt env, status_code: 404, response: "スレッドが見つかりません"
  end

  thread = BbsThread.find(id)
  if thread.nil?
    halt env, status_code: 404, response: "スレッドが見つかりません"
  end

  cursor_time_str = env.params.query["cursor_time"]?
  cursor_id_str = env.params.query["cursor_id"]?

  # Time.parse には ? 版がないため、不正値は rescue で nil に握りつぶす
  cursor_time = cursor_time_str.try do |s|
    Time.parse(s, "%F %H:%M:%S.%L", Time::Location::UTC) rescue nil
  end
  # to_i32? は失敗時に nil を返すので rescue は不要
  cursor_id = cursor_id_str.try(&.to_i32?)

  per_page = 30
  posts = Post.after(id, cursor_time, cursor_id, per_page)

  has_next = false
  if last_post = posts.last?
    has_next = Post.has_more?(id, last_post.created_at, last_post.id)
  end

  render "src/views/thread.ecr"
end
```

> ⚠️ **`Time.parse`のフォーマット文字列に注意**
> ここでも本編の`Time`の罠が顔を出します。URLに埋め込んだ`created_at`を文字列として受け取り、再び`Time`に戻す際、DBが書き込んだフォーマット(`%F %H:%M:%S.%L`、ミリ秒つき)と**寸分違わず一致させる**必要があります。フォーマットがズレると`Time::Format::Error`で例外になり、しかも本番投入後にしか気づきにくい種類のバグです。

> ⚠️ **罠その3:URLパラメータは「ユーザーが自由に改ざんできる」**
> `cursor_time`はURLクエリパラメータです。つまりブラウザだけでなく、クローラーや悪意あるアクセスが`?cursor_time=invalid_string`のような**不正な値**を直接送ってくる可能性を常に考慮する必要があります。対策せずに`Time.parse`をそのまま呼ぶと`Time::Format::Error`が例外として飛び、Kemalは**500 Internal Server Error**を返してしまいます。
>
> 上のコードでは`rescue nil`を挟み、パースに失敗した場合は`cursor_time`を`nil`として扱っています。`Post.after`は`cursor_time`が`nil`なら「カーソルなし=先頭ページ」として動作する設計にしてあるため、**不正なカーソルが送られてきた場合は黙って先頭ページにフォールバックする**という挙動になります。「外部から来た値は常に汚染されている可能性がある」というWeb開発の鉄則を、Crystalの例外処理と型システム(`Time?`)の両方で受け止めている形です。
>
> なお `cursor_id` 側は `to_i32?`(`?`版)を使っているため、失敗時に例外ではなく`nil`が返り、`rescue`は不要です。**「`?`版があるものは`?`で、無いものだけ`rescue`」** という使い分けが、この2行の対比にそのまま表れています。

### ECR側:カーソルリンクの生成

```html
<!-- src/views/thread.ecr(末尾に追加、page変数は使わなくなる) -->
<nav class="pagination">
  <% if last_post = posts.last? %>
    <% if has_next %>
      <!-- URI.encode_www_form により、スペースは + にエンコードされる -->
      <a href="/thread/<%= thread.id %>?cursor_time=<%= URI.encode_www_form(last_post.created_at.to_s("%Y-%m-%d %H:%M:%S.%L")) %>&cursor_id=<%= last_post.id %>">
        次へ →
      </a>
    <% end %>
  <% end %>
</nav>
```

> 💡 **学習ポイント:カーソル値はURLエンコードする**
> `%Y-%m-%d %H:%M:%S.%L`でフォーマットした文字列には**スペース**が含まれます(例: `2026-09-03 12:34:56.789`)。URL内にスペースを裸のまま含めるのはHTTPの作法として厳密には正しくありません。`URI.encode_www_form`でエンコードしてからリンクに埋め込むのが正攻法です。
> ※ `encode_www_form` はスペースを `%20` ではなく `+` に変換しますが、Kemal（およびHTTPのクエリパース）は `+` をスペースとして復元するため、ラウンドトリップは正しく成立します。

> 💡 **学習ポイント:トレードオフの明示**
> このUIには「前へ」ボタンがありません。キーセット方式は本質的に**片方向(次へ)が得意**で、「前へ」を実装するには「逆順にLIMIT件取得して、結果をひっくり返す」という別クエリが必要になります(発展課題として次回以降に回せます)。同様に「12ページ目へジャンプ」もできません。**「無限スクロール」や「もっと見る」ボタンのUIとは非常に相性が良い一方、ページ番号ナビゲーションが必要な場面には向かない**、という使い分けがここではっきり見えてきます。

---

## Step 5: OFFSET方式とキーセット方式の使い分け

| 観点 | OFFSET方式 | キーセット方式 |
|---|---|---|
| ページ番号ジャンプ(3ページ目へ) | ◯ できる | ✕ できない(次へ/前へのみ) |
| 総ページ数の表示 | ◯ `COUNT`で出せる | △ 別クエリが必要、頻繁な表示には不向き |
| 後方ページの速度 | ✕ offsetに比例して劣化 | ◯ 常に一定 |
| 実装の複雑さ | ◯ シンプル | △ カーソルの受け渡し・パース処理が増える |
| 途中でデータが増減した場合の一貫性 | ✕ ページがズレる(新規投稿で1件目が繰り上がる等) | ◯ カーソル基準なので影響を受けにくい |

### 使い分けの指針

- **管理画面の検索結果、件数が数百件程度に収まる一覧** → OFFSET方式で十分。ページ番号ジャンプの利便性を優先。
- **掲示板のスレッド一覧・レス一覧、SNSのタイムライン** → キーセット方式。件数が青天井に増える上、ユーザーは基本的に「次へ」しか使わない。
- **検索結果に近いが件数が多い場合(数万件規模)** → ハイブリッド案。先頭数ページ(例:1〜10ページ)はOFFSETで許容し、それ以降は「もっと見る」ボタンでキーセット方式に切り替える、という設計も現実的。

> 💡 **学習ポイント:「一貫性の崩れ」も実は罠の一種**
> OFFSET方式には性能面だけでなく、**表示内容の一貫性が崩れる**という見落とされがちな問題があります。1ページ目を見ている間に新しいレスが投稿されると、2ページ目に進んだときに「1ページ目で見たはずの投稿」が再度表示されたり、逆に1件飛ばされたりします(offsetは「順位」で切っているため、順位そのものがズレる)。キーセット方式は「前回見た行より後ろ」という相対条件なので、この種のズレが原理的に起きません。これも本編からの一貫したテーマである「動的言語でも静的型付けでも防げない、設計に起因する罠」の一例です。

---

## Step 6: 両モデルへの適用・動作確認・まとめ

### スレッド一覧側もキーセット方式に統一するか?

スレッド一覧(`BbsThread`)側は`GROUP BY`+`COUNT`が絡むため、単純な複合条件比較では書けません(集計後の値でカーソルを組む必要がある)。この記事では:

- **スレッド一覧** → OFFSET方式のまま(Step 2の実装を維持。ページ番号ジャンプの利便性を優先する判断)
- **レス一覧** → キーセット方式(Step 4の実装。件数が青天井に増えるため)

という**使い分けそのものを最終形**として採用します。両方を無理に同じ方式に揃えないのが、Step 5で整理した指針の実践例になっています。

### 最終的なディレクトリ構成(応用編で追加されたファイル)

```text
crystal-bbs/
├── scripts/
│   ├── seed.cr              # 追加: シードデータ投入
│   └── benchmark_offset.cr  # 追加: OFFSETベンチマーク
├── src/
│   ├── models/
│   │   ├── bbs_thread.cr    # 変更: paginate, count 追加
│   │   └── post.cr          # 変更: paginate, after, has_more? 追加
│   ├── routes.cr            # 変更: page/cursorパラメータ対応
│   └── views/
│       ├── index.ecr        # 変更: OFFSET版ページネーションUI追加
│       └── thread.ecr       # 変更: キーセット版「次へ」リンク追加
```

### 通し動作確認の手順

```bash
crystal run scripts/seed.cr          # シード投入(スレッド50件、レス計約5000件)
crystal run src/main.cr              # サーバー起動
```

1. `http://localhost:3000` でスレッド一覧を開き、`?page=2`などでOFFSETページネーションが動くことを確認
2. 投稿数の多いスレッドを開き、「次へ」を連打してキーセットページネーションが最後まで正しく進むことを確認
3. `crystal run scripts/benchmark_offset.cr` で、OFFSET方式の速度劣化を再確認

### まとめ:応用編で得られたもの

1. **`EXPLAIN QUERY PLAN`でDB内部の動作を可視化する習慣** — 「動いている」と「効率的に動いている」は別物だと体で理解する
2. **OFFSETは読み捨て、キーセットは基準点からの絞り込み** — この違いが性能特性の非対称性を生む
3. **本編の設計判断(ミリ秒つきTime、last_insert_idの取り方)が応用編で回収される構成** — 一見小さな設計判断が後の機能追加を左右する好例
4. **「どちらか一方が正解」ではなく、用途に応じた使い分けが実務的な結論** — スレッド一覧はOFFSET、レス一覧はキーセットという非対称な採用がそれを体現している

### 次のステップ(さらなる応用へ)

- **「前へ」ボタンの実装**: 逆順クエリ+結果の反転
- **バリデーション**: モデル層に`valid?`メソッドを実装する
- **`DB::Serializable`**: クエリ結果を`struct`へ自動マッピングし、`rs.read`の並び管理から解放される
- **設定の外部化**: DBパスやポートを環境変数から読む

---

*(※この記事は Crystal 1.21.0 および Kemal の最新バージョンをベースに構成しています。ライブラリの仕様は更新される場合があるため、公式ドキュメントも併せてご確認ください。)*

---

以上で完成です。Step 7 の動作確認に `/thread/abc` で404が返る旨を一文足しました（実装した堅牢化の動作確認ポイントとして自然なので）。この環境ではCrystalを実行できないため、`crystal build` での最終確認（特に `halt` の型絞り込みが本編・応用編の両ルートで通るか）だけ手元でお願いします。HTML版が必要になったら一声ください。
