"""開発用のシードデータ投入: python scripts/seed.py（プロジェクトルートで実行）"""
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
from database import Database  # noqa: E402


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def main() -> None:
    db = Database.get_instance()
    print("シードデータを投入中...")
    now = datetime.now(timezone.utc)

    threads = []
    for i in range(50):
        created = now - timedelta(hours=50 - i)
        cursor = db.execute(
            "INSERT INTO bbs_threads (title, created_at) VALUES (?, ?)",
            (f"テストスレッド {i + 1}", fmt(created)),
        )
        threads.append((cursor.lastrowid, created))

    # レスの日時は「スレッド作成時刻 〜 現在」に収める
    for tid, created in threads:
        span_ms = int((now - created).total_seconds() * 1000)
        for j in range(random.randint(10, 200)):
            posted = created + timedelta(milliseconds=random.randint(0, span_ms))
            db.execute(
                "INSERT INTO posts (thread_id, name, body, created_at) VALUES (?, ?, ?, ?)",
                (tid, f"名無しさん{j}", f"テスト投稿本文 {j} 番目です。\n" * random.randint(1, 5), fmt(posted)),
            )

    db.commit()
    count = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    print(f"投入完了。posts総数: {count}")


if __name__ == "__main__":
    main()
