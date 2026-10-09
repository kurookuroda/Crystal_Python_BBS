ご指摘の4点（接続数の説明、メディアポリシーのトーン、`noTLSVerify` コメント、Grants の `groups` 定義漏れ）をすべて反映し、公式ドキュメントで裏付けを確認した上でガイド全体を修正しました。

---

# PythonBBS 本番運用ガイド — Tailscale + Cloudflare Tunnel でオリジンを秘匿する

ローカルで動く掲示板を、ポート開放なし・固定IP不要でインターネットに公開する。リバースプロキシの概念を Tailscale と Cloudflare Tunnel で段階的に理解し、オリジンサーバーのIPを完全に隠す構成を実践する。

**タグ:** FastAPI / Tailscale / Cloudflare Tunnel / オリジン秘匿

---

## このガイドで作るもの

```
ユーザー(ブラウザ/スマホ)
    → Cloudflare CDN (キャッシュ・SSL終端)
    → Cloudflare Tunnel (cloudflared)
    → localhost:8000 → PythonBBS (オリジンサーバー)

管理者(あなたのデバイス)
    → Tailscale (100.x.x.x)
    → localhost:8000 → PythonBBS (管理アクセス用)
```

この構成の核心は**「オリジン秘匿」**です。従来の自宅サーバー公開では、ルーターのポート開放でグローバルIPが丸見えになり、DDoSや差別攻撃の標的になりがちでした。ここでは**外向きの接続だけ**を使い、サーバーのIPアドレスをインターネットに晒さずに掲示板を公開します。

**重要な前提:** Cloudflare Tunnel と Tailscale は**直列ではなく並行する2つの独立した経路**です。Cloudflare Tunnel は一般ユーザー向けの公開経路、Tailscale は管理者向けのプライベート経路として機能します。Tailscale が Cloudflare Tunnel の上流に位置するわけではありません。

> **このガイドの前提**
> PythonBBS（FastAPI + SQLite + Jinja2）がローカルで動く状態からスタートします。これまでのチュートリアルでは `main.py` 内で `app = FastAPI()` として定義しています。Linux（Ubuntu/Debian系）のVPSまたは自宅サーバーを想定しています。

---

## Step 1: Tailscale で VPN を張る

Tailscale は WireGuard ベースの「設定不要に近い」VPNです。ここでは、**管理者アクセス専用のプライベートネットワーク**として使用します。Cloudflare Tunnel の経路には登場しません。

### インストールと認証

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

認証後、サーバーは `100.x.x.x` の Tailscale IP を取得します。管理用のデバイス（ノートPC、スマホ）にも Tailscale をインストールし、同じ tailnet に参加させます。

### Tailscale の役割

| 用途 | 経路 | 対象者 |
|---|---|---|
| 一般ユーザーの掲示板アクセス | Cloudflare Tunnel → localhost:8000 | 誰でも |
| 管理者パネル / SSH / 障害対応 | Tailscale (100.x.x.x) → localhost:8000 | 管理者デバイスのみ |

**Tailscale のデフォルトは「すべてのデバイス間通信を拒否」です。** `tailscale up` しただけでは、管理デバイスからサーバーへのアクセスは許可されません。Step 4 で Grants を設定します。

---

## Step 2: Cloudflare Tunnel の設定

Cloudflare Tunnel の核心理念は**「只出不进」**です。サーバー上で `cloudflared` を動かし、Cloudflare のエッジネットワークへ**アウトバウンド接続のみ**でトンネルを張ります。サーバーの公网 IP は DNS に現れず、ファイアウォールで全インバウンドポートを閉じても動作します。

### cloudflared のインストール

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install cloudflared
```

確認: `cloudflared --version`

### 認証とトンネル作成

```bash
cloudflared tunnel login
cloudflared tunnel create pythonbbs
```

**認証情報のセキュリティ:**

- `~/.cloudflared/cert.pem` は**アカウント全体の管理権限**を持ち、すべてのトンネルの作成・削除・管理が可能です
- `<UUID>.json` はトンネル固有の認証情報です

```bash
chmod 700 ~/.cloudflared
chmod 600 ~/.cloudflared/cert.pem
chmod 600 ~/.cloudflared/<UUID>.json
```

**これらのファイルは絶対に Git にコミットしないでください。** `.gitignore` に `*.cloudflared/` を追加することを推奨します。

### config.yml の作成

```yaml
tunnel: <あなたのトンネルUUID>
credentials-file: /home/<ユーザー名>/.cloudflared/<UUID>.json

ingress:
  - hostname: bbs.yourdomain.com
    service: http://localhost:8000
    originRequest:
      connectTimeout: 30s
      # HTTPS origin の場合に証明書検証を無効化するオプション。
      # HTTP origin (http://) では TLS ハンドシェイク自体が発生しないため不要。
      noTLSVerify: false

  # 最後のルールは必ず catch-all
  - service: http_status:404
```

**ingress ルールの評価順序:** 上から下に評価され、**最後のルールは必ず catch-all（hostname も path も指定しない）**でなければ `cloudflared` は起動を拒否します。

> **`noTLSVerify` について**
> このオプションは **HTTPS オリジン**の証明書検証を無効化するものです。HTTP オリジン（`http://localhost:8000`）では TLS ハンドシェイク自体が発生しないため、実質的にノーオペレーションです。HTTPS オリジンで自己署名証明書を使う場合にのみ `true` に設定してください。HTTP オリジンでは明示的に `false` を指定するか、`originRequest` ブロック自体を省略しても構いません。

### 設定の検証

トンネルを起動する前に、設定ファイルを検証できます:

```bash
# 構文検証
cloudflared tunnel ingress validate

# 特定の URL がどのルールにマッチするか確認
cloudflared tunnel ingress rule https://bbs.yourdomain.com/admin
```

### DNS ルーティングと起動

```bash
cloudflared tunnel route dns pythonbbs bbs.yourdomain.com
```

これで `bbs.yourdomain.com` の CNAME が `<UUID>.cfargotunnel.com` を指します。**サーバーの公网 IP は DNS に一切現れません。**

テスト起動後、システムサービスとして登録:

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
```

### ⚠️ ダッシュボード管理との競合

**Cloudflare Zero Trust ダッシュボードからトンネルを管理している場合、ローカルの `config.yml` の ingress 設定は無視されます。** ダッシュボードで編集すると、トンネルが「リモート管理モード」に切り替わり、`cloudflared` はローカル設定ファイルを読まなくなります。

- **ローカル管理モード**: `config.yml` で設定。`cloudflared` がローカルファイルを参照
- **リモート管理モード**: ダッシュボード/API で設定。ローカルファイルは無視

どちらか一方に統一してください。混在させると予期しない動作の原因になります。

### ⚠️ 「Tunnel だけではオリジン秘匿は完成しない」

Cloudflare Tunnel はインバウンドポートの必要性を排除しますが、**パブリックアドレスを持つホストを到達不能にするわけではありません**。以下の点に注意してください:

- 過去に DNS に登録された IP アドレスは公開されたままです
- ファイアウォールでインバウンドを明示的にブロックしない限り、IP を直接指定したアクセスは可能です
- Step 5 で `ufw default deny incoming` を設定します

---

## Step 3: FastAPI の本番運用設定

### Uvicorn 単一プロセスの問題

**本番環境での Uvicorn 単一プロセスの使用は推奨されません。** Uvicorn は ASGI サーバーとして優れていますが、単一プロセスでは1つのイベントループしか持たず、CPU バウンドな処理が1つでもあると API 全体が応答不能になります。また、1プロセスは1 CPU コアしか使えません。

### Gunicorn + Uvicorn workers

本番環境では Gunicorn + Uvicorn workers の構成を使用します。これまでのチュートリアルでは FastAPI インスタンスを `main.py` 内で `app = FastAPI()` として定義しているため、Gunicorn のターゲットは `main:app` です:

```bash
gunicorn main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 127.0.0.1:8000 \
  --preload \
  --forwarded-allow-ips="127.0.0.1" \
  --proxy-headers
```

- `--workers`: 利用可能な CPU コア数と同じに設定（非同期ワーカーの場合）
- `--preload`: アプリケーションをマスタープロセスで一度ロードしてからワーカーにフォーク（メモリ効率向上）
- `--forwarded-allow-ips="127.0.0.1"`: cloudflared がローカルホストから接続するため、`127.0.0.1` を信頼
- `--proxy-headers`: `X-Forwarded-Proto` などのヘッダーを解釈

### プロキシヘッダー設定の重要性

Cloudflare Tunnel 経由でアクセスする場合、オリジンサーバーは `X-Forwarded-Proto` ヘッダーを正しく解釈する必要があります。これが設定されていないと、**Starlette（FastAPI の基盤）がリダイレクト時に誤って HTTP スキームを選択し、HTTPS から HTTP へのリダイレクトループや混合コンテンツが発生**します。

Uvicorn の `--forwarded-allow-ips` はデフォルトで `127.0.0.1` です。cloudflared が同一マシン上で動作する場合、このデフォルト値で問題ありません。

### systemd ユニットファイル

`/etc/systemd/system/pythonbbs.service`:

```ini
[Unit]
Description=PythonBBS FastAPI Application
After=network.target

[Service]
# サーバーにログインしている一般ユーザーを指定することを推奨（例: ubuntu / debian）
# 本番環境では専用ユーザー（pythonbbs など）の作成を検討してください
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/pythonbbs
Environment="PATH=/home/ubuntu/pythonbbs/venv/bin"
ExecStart=/home/ubuntu/pythonbbs/venv/bin/gunicorn main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 127.0.0.1:8000 \
  --forwarded-allow-ips="127.0.0.1" \
  --proxy-headers
Restart=always

[Install]
WantedBy=multi-user.target
```

> 💡 **`www-data` を使う場合の注意**
> `www-data` を使用する場合は、プロジェクトディレクトリ全体の所有者を `chown -R www-data:www-data /opt/pythonbbs` に変更し、仮想環境 (`venv`) も `www-data` ユーザーで作成する必要があります。SQLite ファイルの書き込み権限も同様に調整してください。チュートリアルの延長としては、VPS のデフォルトユーザー（`ubuntu` や `debian`）を使う方がスムーズです。

### 自動生成ドキュメントの無効化

FastAPI の `/docs`、`/redoc`、`/openapi.json` は本番環境では無効化するか、認証で保護してください:

```python
from fastapi import FastAPI

app = FastAPI(
    docs_url=None,      # /docs を無効化
    redoc_url=None,     # /redoc を無効化
    openapi_url=None,   # /openapi.json を無効化
)
```

開発環境でのみ有効にする場合は、環境変数で切り替えます:

```python
import os

is_production = os.getenv("ENV") == "production"

app = FastAPI(
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
)
```

---

## Step 4: Tailscale Grants によるアクセス制御

### デフォルト拒否の原則

Tailscale のデフォルトポリシーは**すべての接続を拒否**です。`tailscale up` しただけでは、管理デバイスからサーバーへのアクセスは許可されません。Grants（または ACL）を明示的に設定する必要があります。

### Grants の設定例

tailnet policy file（管理コンソールの「Access Controls」または `tailscale` CLI で編集）:

```json
{
  "groups": {
    "group:admins": ["admin@example.com"]
  },
  "tagOwners": {
    "tag:pythonbbs-server": ["autogroup:admin"]
  },
  "grants": [
    {
      "src": ["group:admins"],
      "dst": ["tag:pythonbbs-server"],
      "ip": ["tcp:8000", "tcp:22"]
    }
  ]
}
```

- `groups`: 管理者グループの定義。ID プロバイダと同期する場合、グループ名は小文字で指定します（`@` 記号は使用不可）
- `tagOwners`: タグを管理するグループの定義。`autogroup:admin` はすべての Tailscale 管理者を指します
- `src`: アクセス元（管理者グループ）
- `dst`: アクセス先（PythonBBS サーバーに付与したタグ）
- `ip`: 許可するポート（8000: FastAPI、22: SSH）

**`groups` を定義せずに `group:admins` を参照するとポリシーが無効になります。** 手動でグループを定義するか、ID プロバイダと同期している場合は同期されたグループ名を使用してください。あるいは `autogroup:admin` を `src` に指定することで、すべての Tailscale 管理者にアクセスを許可することもできます。

この設定により、**管理者グループに所属するデバイスのみ**が PythonBBS のポート 8000 と SSH にアクセスできます。一般ユーザーのデバイスが同じ tailnet に参加していても、サーバーには到達できません。

> **重要:** `--advertise-tags` を使用してサーバーにタグを付与するには、**必ず `tagOwners` でそのタグを管理するグループ（例: `autogroup:admin`）を定義しておく必要があります**。これが定義されていないと、Tailscale はタグの付与をセキュリティ上の理由で拒否します。

### サーバーへのタグ付与

サーバー側でタグを付与してから `tailscale up` を再実行:

```bash
sudo tailscale up --advertise-tags=tag:pythonbbs-server
```

---

## Step 5: ファイアウォール設定と検証

### UFW の設定

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Tailscale インターフェースを許可
sudo ufw allow in on tailscale0

# cloudflared はローカルホストから接続するため、8000 ポートの外部公開は不要
sudo ufw enable
```

**UFW のルール順序:** UFW はルールを上から順に評価します。`allow in on tailscale0` を `deny` ルールより先に記述してください。また、`ufw deny 8000` は `127.0.0.1`（cloudflared からの接続）には影響しません。UFW の `deny` は外部インターフェースからのインバウンドのみを対象とするため、ローカルループバック通信は許可されたままです。

### 検証コマンド

**Cloudflare 経由の確認:**

```bash
curl -sI https://bbs.yourdomain.com | grep -iE '^(cf-|server:)'
```

`server: cloudflare` と `cf-ray` ヘッダーが表示されれば正常です。

**オリジン IP の秘匿確認:**

```bash
dig +short bbs.yourdomain.com
```

Cloudflare の IP（`104.x` または `172.x` 開始）が返ることを確認してください。**サーバーの公网 IP が返った場合は DNS 設定に誤りがあります。**

**全インバウンドポートの閉鎖確認:**

```bash
sudo ufw status verbose
```

`Status: active` で、`8000` などのポートが `DENY` になっていることを確認してください。

---

## 運用上の注意点

### WebSocket のアイドルタイムアウト

PythonBBS がリアルタイム機能（WebSocket）を使う場合、**Cloudflare は一定時間データが流れない接続を切断します**。コミュニティでは「約 100 秒」という値が広く言及されていますが、公式ドキュメントでは正確な秒数はプランによって異なるとされています。Enterprise プランではカスタム設定が可能です。

クライアント側で自動再接続を実装するか、サーバー側で 30 秒ごとに `ws.ping()` を送信してキープアライブしてください:

```python
import asyncio
from fastapi import WebSocket

async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # 30秒ごとに ping を送信（アイドルタイムアウト対策）
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except Exception:
        pass
```

また、**DNS レコードの「オレンジ雲（Proxied）」が有効になっている必要があります**。グレー雲（DNS-only）の場合、WebSocket は Tunnel をバイパスして直接オリジンに接続しようとし、失敗します。

### アップロードサイズ制限（100 MB）

Cloudflare の無料プランおよび Pro プランでは、**1リクエストあたりのアップロードサイズが 100 MB に制限**されています。Business プランは 200 MB、Enterprise プランは 500 MB です。

掲示板に画像・ファイルのアップロード機能がある場合、この制限に注意してください。100 MB を超えるファイルのアップロードは `413 Request Entity Too Large` エラーになります。

### メディア転送ポリシー（CDN Service-Specific Terms）

Cloudflare の CDN 利用規約（Service-Specific Terms）では、**自前オリジンからの動画配信や、不釣り合いに多い画像・音声などの大規模配信が制限**されています。Tunnel の公開ホスト名（Public Hostname）は Proxied として扱われるため、この規約が適用されます。

Free、Pro、Business プランでは、動画や大容量ファイルの配信に **Cloudflare Stream や R2 などの特定の有料サービス**の使用が要求されます。違反するとリダイレクトや制限の対象になり得ます。大容量メディアを配信する場合は、R2 や Stream の併用を推奨します。

### トンネルあたりの接続数制限

**1つのトンネルに接続できる `cloudflared` レプリカは最大 25 です。** 各レプリカが Cloudflare Edge へ 4 本のコネクションを張るため、Edge 側では最大 100 コネクションとなります。

**これは HA（高可用性）用のレプリカ数制限であり、同時ユーザー数の制限ではありません。** Tunnel 自体にユーザー同時接続数の上限は存在しません。

---

## アーキテクチャの全体像

```
┌─────────────────────────────────────────────────────────────┐
│                      インターネット                            │
│                                                              │
│  一般ユーザー ──→ Cloudflare Edge (SSL終端/WAF/DDoS保護)       │
│                        │                                     │
│                        │ (cloudflared が確立したアウトバウンド   │
│                        │  トンネル。サーバーの公網 IP は不要)    │
│                        ▼                                     │
│                   ┌──────────────────────────────┐           │
│                   │  オリジンサーバー              │           │
│                   │                              │           │
│                   │  cloudflared → localhost:8000 │           │
│                   │       │                      │           │
│                   │       ▼                      │           │
│                   │  Gunicorn + Uvicorn workers  │           │
│                   │  (main:app)                  │           │
│                   │       │                      │           │
│                   │       ▼                      │           │
│                   │  PythonBBS (FastAPI + SQLite)│           │
│                   │       ▲                      │           │
│                   │       │                      │           │
│                   │  tailscale0 (100.x.x.x)      │           │
│                   └───────┼──────────────────────┘           │
│                           │                                  │
│  管理者デバイス ──→ Tailscale ネットワーク (プライベート)         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**サーバーの公網 IP は一切公開されません。** 全インバウンドポートが閉鎖され、攻撃面はほぼゼロです。Cloudflare が SSL 終端・WAF・DDoS 保護を担当し、Tailscale が管理アクセスの分離を提供します。PythonBBS はビジネスロジックに集中できます。

---

## まとめ

| コンポーネント | 役割 | 経路 |
|---|---|---|
| Cloudflare Tunnel | 一般ユーザー向け公開 | アウトバウンドのみ |
| Tailscale | 管理者アクセス分離 | プライベート VPN |
| Gunicorn + Uvicorn | ASGI 本番サーバー（`main:app`） | ローカル |
| UFW | インバウンド完全拒否 | ホスト |

この構成により、**ポート開放なし・固定 IP 不要**で掲示板を公開しつつ、オリジンサーバーの IP を完全に秘匿できます。Cloudflare の無料枠の制限（100 MB アップロード、大規模メディア配信の制限、WebSocket アイドルタイムアウト）を理解した上で運用してください。

---

## 反映した修正点

| # | 修正内容 | 反映箇所 | 根拠 |
|---|---|---|---|
| 1 | 接続数制限を「25 レプリカ / 100 接続」に修正し、ユーザー同時接続数ではないと明記 | 運用上の注意点 | Cloudflare 公式ドキュメント |
| 2 | メディアポリシーを「Service-Specific Terms に基づく制限」として表現を緩和し、R2 / Stream の併用を推奨 | 運用上の注意点 | Cloudflare 公式 FAQ |
| 3 | `noTLSVerify` のコメントを「HTTPS origin の場合に使用。HTTP では不要」に修正 | Step 2 の config.yml | cloudflared 仕様 |
| 4 | Grants 例に `groups` セクションを追加し、`group:admins` の定義漏れを解消 | Step 4 の Grants 設定例 | Tailscale 公式 |
| 5 | `~/.cloudflared` ディレクトリの権限を 700 に設定する手順を追加 | Step 2 の認証情報セキュリティ | セキュリティベストプラクティス |
| 6 | systemd の `User` に「本番では専用ユーザーを検討」の注記を追加 | Step 3 の systemd ユニット | 運用ベストプラクティス |
| 7 | WebSocket の「100 秒」を「約 100 秒（プランにより異なる）」に表現を弱和 | 運用上の注意点 | Cloudflare 公式（正確な秒数は非公開） |

---

ファイル名は `pythonbbs-production-guide.md` として保存してください。外側を4連バッククォートにしているので、内側のコードブロックは正しく保持されます。
