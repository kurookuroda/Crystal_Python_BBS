#!/usr/bin/env python3
"""FastAPI アプリ (uvicorn) を起動し、トンネル経由で外部公開するランチャー。

使い方:
    python setup_expose.py        # 対話でトンネル方法を選択
    python setup_expose.py 0      # 0=localtunnel / 1=bore / 2=cloudflared を直接指定
                                  # (Colabの `!python` など input() が使えない環境向け)

環境変数:
    APP_MODULE  起動するASGIアプリ (既定: main:app)
    APP_PORT    アプリのポート番号 (既定: 8000)
"""
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import requests

APP_MODULE = os.environ.get("APP_MODULE", "main:app")
APP_PORT = int(os.environ.get("APP_PORT", "8000"))
TUNNEL_TIMEOUT = 180  # トンネルURLの取得を待つ秒数

exposed_url = ""

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# プロセス管理の共通処理
# ---------------------------------------------------------------------------
def cleanup_old_processes():
    """以前の古いプロセス（uvicornやトンネル）が残っている場合は強制終了してポートを解放"""
    print("🧹 古いプロセスをクリーンアップしています...")
    # シェルを介さず引数リストで呼ぶ（`sh -c "pkill -f ..."` だと、
    # シェル自身のコマンドラインにパターンが含まれて自分が巻き添えになるため）
    for pattern in (
        "uvicorn " + APP_MODULE,
        "bore local",
        "cloudflared tunnel",
        "localtunnel --port",
    ):
        subprocess.run(["pkill", "-f", pattern], stderr=subprocess.DEVNULL)
    time.sleep(1)


def spawn(cmd, **kwargs):
    """標準出力/標準エラーをパイプで受け取る形でプロセスを起動する。

    start_new_session=True で独立したプロセスグループにするため、
    npx や sudo が作る子プロセスもまとめて終了できる。
    """
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
        **kwargs,
    )


def stop_process(proc):
    """プロセス（とその子プロセス）を穏やかに停止し、止まらなければ強制終了する。"""
    if not proc or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()


# ログ読み取り関数
def read_process_output(process, stream, prefix="", line_callback=None):
    """プロセスの出力をリアルタイムで読み取り、表示する関数"""
    try:
        for line in iter(stream.readline, ""):
            print(f"{prefix}{line.strip()}")
            if line_callback:
                try:
                    line_callback(line)
                except Exception:
                    pass
    except (ValueError, OSError):
        pass
    try:
        stream.close()
    except Exception as e:
        logger.debug(f"Error closing stream in read_process_output: {e}")


def attach_readers(process, out_prefix, err_prefix, line_callback=None):
    """stdout/stderr の読み取りスレッドを起動し、スレッドのリストを返す。"""
    threads = [
        threading.Thread(
            target=read_process_output,
            args=(process, process.stdout, out_prefix, line_callback),
            daemon=True,
        ),
        threading.Thread(
            target=read_process_output,
            args=(process, process.stderr, err_prefix, line_callback),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()
    return threads


# ---------------------------------------------------------------------------
# FastAPI (uvicorn) の起動
# ---------------------------------------------------------------------------
def wait_for_app_server(process=None, port=APP_PORT, timeout=60):
    """アプリサーバーが起動し、リクエストに応答するのを待機します。"""
    url = f"http://127.0.0.1:{port}"
    print(f"Waiting for the app server to start at {url}...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        if process and process.poll() is not None:
            print(f"❌ アプリのプロセスが予期せず終了しました (終了コード: {process.poll()})。")
            return False
        try:
            response = requests.get(url, timeout=2, allow_redirects=True)
            if response.status_code < 500:
                print(f"✅ App server is up and running! Status code: {response.status_code}")
                return True
        except requests.exceptions.RequestException:
            pass
        except Exception as e:
            print(f"Error during app server health check: {e}")
        time.sleep(1)
    print(f"❌ App server did not respond within {timeout} seconds.")
    return False


def launch_app():
    """uvicorn で FastAPI アプリを起動する。失敗したら None を返す。

    --reload は監視用の子プロセスを増やして終了処理が複雑になるため付けない。
    トンネルは同一マシン内から接続するので、待ち受けは 127.0.0.1 で十分。
    """
    print(f"🚀 FastAPI アプリケーションを起動しています (uvicorn {APP_MODULE})...")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    app_process = spawn(
        [sys.executable, "-m", "uvicorn", APP_MODULE, "--host", "127.0.0.1", "--port", str(APP_PORT)],
        cwd=CURRENT_DIR,
        env=env,
    )
    threads = attach_readers(app_process, "[APP_OUT] ", "[APP_ERR] ")

    if not wait_for_app_server(process=app_process, port=APP_PORT, timeout=60):
        print("❌ FastAPIアプリケーションが指定時間内に起動しませんでした。")
        stop_process(app_process)
        for t in threads:
            t.join(timeout=5)
        return None
    return app_process


# ---------------------------------------------------------------------------
# トンネル共通処理
# ---------------------------------------------------------------------------
def start_tunnel(cmd, url_pattern, tag, label):
    """トンネルのコマンドを起動し、出力からURLを抜き出して (プロセス, URL) を返す。

    URLが取れなかった場合は URL が "" になる。起動自体に失敗した場合は (None, "")。
    """
    print(f"🌐 {label} トンネルを開始しています...")
    try:
        tunnel_process = spawn(cmd)
    except OSError as e:
        print(f"❌ {label} を起動できませんでした: {e}")
        return None, ""

    url_event = threading.Event()
    found = []

    def on_line(line):
        if not url_event.is_set():
            match = re.search(url_pattern, line)
            if match:
                found.append(match.group(0).strip())
                url_event.set()

    # URLは stdout/stderr のどちらに出るか実装依存なので、両方を見る
    attach_readers(tunnel_process, f"[{tag}_OUT] ", f"[{tag}_ERR] ", on_line)

    print("🔍 トンネルURLを待機しています...")
    start_time = time.time()
    while time.time() - start_time < TUNNEL_TIMEOUT:
        if url_event.wait(timeout=0.5):
            break
        if tunnel_process.poll() is not None:
            print(f"{label} プロセスが予期せず終了しました。")
            break

    if found:
        print(f"✅ {label} トンネルが開始されました: {found[0]}")
        return tunnel_process, found[0]
    print(f"⚠️ {label} のトンネルURLの取得に失敗しました。")
    return tunnel_process, ""


# ---------------------------------------------------------------------------
# 各トンネル方式
# ---------------------------------------------------------------------------
def ensure_node():
    """npx (Node.js) が使えることを確認し、無ければインストールを試みる。"""
    if shutil.which("npx"):
        return True
    print("📦 Node.js が見つかりません。インストールを試みます...")
    os.system("sudo apt-get update -qq && sudo apt-get install -y -qq nodejs npm")
    return shutil.which("npx") is not None


def setup_localtunnel():
    """Node.js製 localtunnel の設定"""
    print("🟩 localtunnel をセットアップしています...")
    if not ensure_node():
        print("❌ Node.js (npx) を用意できませんでした。localtunnel は使えません。")
        return None, None, ""

    app_process = launch_app()
    if app_process is None:
        return None, None, ""

    # --yes: 初回の「インストールしますか？」確認を自動で承諾する
    tunnel_process, url = start_tunnel(
        ["npx", "--yes", "localtunnel", "--port", str(APP_PORT)],
        r"https://[a-zA-Z0-9-]+\.loca\.lt",
        "LT",
        "localtunnel",
    )
    return app_process, tunnel_process, url


def setup_bore_tunnel():
    """Rust製のboreトンネルの設定"""
    print("🦀 Bore トンネルをセットアップしています...")

    # boreのダウンロードとインストール
    os.system('sudo wget -nc https://github.com/ekzhang/bore/releases/download/v0.6.0/bore-v0.6.0-x86_64-unknown-linux-musl.tar.gz')
    os.system('sudo tar -zxvf bore-v0.6.0-x86_64-unknown-linux-musl.tar.gz')
    os.system('sudo chmod 764 bore')

    app_process = launch_app()
    if app_process is None:
        return None, None, ""

    tunnel_process, hostport = start_tunnel(
        ["sudo", "./bore", "local", str(APP_PORT), "--to", "bore.pub"],
        r"bore\.pub:\d+",
        "BORE",
        "bore",
    )
    # boreは http:// で公開される（TLSなし）
    return app_process, tunnel_process, (f"http://{hostport}" if hostport else "")


def setup_cloudflare_tunnel():
    """Cloudflare Tunnelの設定"""
    print("☁️ Cloudflare Tunnel をセットアップしています...")

    os.system('sudo wget -nc https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb')
    os.system('sudo dpkg -i cloudflared-linux-amd64.deb 2>/dev/null')

    app_process = launch_app()
    if app_process is None:
        return None, None, ""

    cloudflared_bin = shutil.which("cloudflared") or (
        "/usr/local/bin/cloudflared"
        if os.path.exists("/usr/local/bin/cloudflared")
        else "/usr/bin/cloudflared"
    )

    tunnel_process, url = start_tunnel(
        [cloudflared_bin, "tunnel", "--url", f"http://127.0.0.1:{APP_PORT}"],
        r"https://[a-zA-Z0-9-]+\.trycloudflare\.com",
        "CF",
        "Cloudflare",
    )
    return app_process, tunnel_process, url


# ---------------------------------------------------------------------------
# ユーティリティ / メイン
# ---------------------------------------------------------------------------
def get_colab_external_ip():
    """Colabの外部IPアドレスを取得します。"""
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", "10", "ipinfo.io/ip"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"⚠️ 外部IPアドレスの取得に失敗しました: {e}")
        return "UNKNOWN_IP"


TUNNEL_CHOICES = {
    "0": "localtunnel", "localtunnel": "localtunnel", "lt": "localtunnel",
    "1": "bore", "bore": "bore",
    "2": "cloudflared", "cloudflared": "cloudflared", "cf": "cloudflared",
}


def choose_tunnel_service():
    """コマンドライン引数、なければ対話入力でトンネル方式を選ぶ。"""
    if len(sys.argv) > 1:
        service = TUNNEL_CHOICES.get(sys.argv[1].strip().lower())
        if service:
            return service
        print(f"無効な引数です: {sys.argv[1]} (0 / 1 / 2 / localtunnel / bore / cloudflared)")
        sys.exit(2)

    while True:
        print("\n--- トンネル方法を選択してください ---")
        print("0. localtunnel (Node.js)")
        print("1. Bore")
        print("2. Cloudflared")
        try:
            choice = input("選択 (0/1/2): ").strip().lower()
        except EOFError:
            print("入力を受け取れません。`python setup_expose.py 0` のように引数で指定してください。")
            sys.exit(2)

        service = TUNNEL_CHOICES.get(choice)
        if service:
            return service
        print("無効な選択です。0, 1, 2 のいずれかを入力してください。")


def main():
    global exposed_url
    selected_tunnel_service = choose_tunnel_service()

    cleanup_old_processes()

    if selected_tunnel_service == "localtunnel":
        app_process, tunnel_process, exposed_url = setup_localtunnel()
    elif selected_tunnel_service == "bore":
        app_process, tunnel_process, exposed_url = setup_bore_tunnel()
    else:
        app_process, tunnel_process, exposed_url = setup_cloudflare_tunnel()

    external_ip = get_colab_external_ip()
    print(f"\nColab 外部IPアドレス: {external_ip}")
    print(f"expose : {exposed_url}")
    if selected_tunnel_service == "localtunnel" and exposed_url:
        print(f"ℹ️ localtunnelの確認ページが出たら、パスワード欄に上の外部IPアドレス ({external_ip}) を入力してください。")

    # メインプロセスを維持し、終了時に子プロセスを終了
    if app_process and tunnel_process:
        try:
            print("\n🟢 サーバ維持中... 終了するには Ctrl+C を押してください。")
            while True:
                if app_process.poll() is not None:
                    print(f"❌ アプリのプロセスが停止しました (コード: {app_process.poll()})")
                    break
                if tunnel_process.poll() is not None:
                    print(f"❌ トンネルプロセスが停止しました (コード: {tunnel_process.poll()})")
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n👋 終了シグナルを受信しました。プロセスを停止しています...")
        finally:
            for proc in (app_process, tunnel_process):
                stop_process(proc)
            cleanup_old_processes()
    else:
        # 片方だけ起動できた場合に、プロセスを残さない
        for proc in (app_process, tunnel_process):
            stop_process(proc)


if __name__ == "__main__":
    main()