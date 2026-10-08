"""SQLite接続の一元管理。

DBファイルのパスは環境変数 BBS_DB_PATH で変更できる（既定: ./bbs.db）。
"""
import os
import sqlite3
import threading
from typing import Optional

DEFAULT_DB_PATH = "bbs.db"


class Database:
    _instance: Optional[sqlite3.Connection] = None
    _lock = threading.Lock()  # 接続の生成を保護する
    write_lock = threading.RLock()  # execute + commit + lastrowid をひとまとめにする

    @classmethod
    def get_instance(cls) -> sqlite3.Connection:
        if cls._instance is None:
            with cls._lock:
                # 二重チェックロック
                if cls._instance is None:
                    path = os.environ.get("BBS_DB_PATH", DEFAULT_DB_PATH)
                    db = sqlite3.connect(path, check_same_thread=False)
                    db.row_factory = sqlite3.Row
                    cls._setup(db)
                    cls._instance = db
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

        # スレッド内の並べ替えとキーセットページネーションを支えるインデックス
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_posts_thread_created
            ON posts(thread_id, created_at, id)
        """)

        db.commit()

    @classmethod
    def close(cls) -> None:
        with cls._lock:
            if cls._instance:
                cls._instance.close()
                cls._instance = None
