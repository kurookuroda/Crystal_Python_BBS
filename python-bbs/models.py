"""データモデル。SQLの組み立てはすべてプレースホルダ（?）を使う。"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from database import Database


def now_str() -> str:
    """SQLiteのDEFAULT（strftime('%Y-%m-%d %H:%M:%f')）と同じ形式のUTC日時。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@dataclass(frozen=True)
class BbsThread:
    id: int
    title: str
    created_at: str
    post_count: int = 0

    @classmethod
    def paginate(cls, page: int, per_page: int = 20) -> List["BbsThread"]:
        """スレッド一覧（OFFSET方式。ページ番号ジャンプを優先）。"""
        offset = (page - 1) * per_page
        db = Database.get_instance()
        cursor = db.execute(
            """
            SELECT t.id, t.title, t.created_at, COUNT(p.id) AS post_count
            FROM bbs_threads t LEFT JOIN posts p ON t.id = p.thread_id
            GROUP BY t.id
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        )
        return [cls(**row) for row in cursor.fetchall()]

    @classmethod
    def count(cls) -> int:
        db = Database.get_instance()
        return db.execute("SELECT COUNT(*) FROM bbs_threads").fetchone()[0]

    @classmethod
    def find(cls, thread_id: int) -> Optional["BbsThread"]:
        db = Database.get_instance()
        row = db.execute(
            "SELECT id, title, created_at FROM bbs_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        return cls(**row) if row else None

    @classmethod
    def create(cls, title: str) -> int:
        db = Database.get_instance()
        with Database.write_lock:
            cursor = db.execute(
                "INSERT INTO bbs_threads (title, created_at) VALUES (?, ?)",
                (title, now_str()),
            )
            db.commit()
            return cursor.lastrowid


@dataclass(frozen=True)
class Post:
    id: int
    thread_id: int
    name: str
    body: str
    created_at: str

    @classmethod
    def after(
        cls,
        thread_id: int,
        cursor_time: Optional[str],
        cursor_id: Optional[int],
        per_page: int = 30,
    ) -> List["Post"]:
        """レス一覧（キーセット方式）。カーソルなしなら先頭ページ。"""
        db = Database.get_instance()
        if cursor_time is not None and cursor_id is not None:
            cursor = db.execute(
                """SELECT id, thread_id, name, body, created_at FROM posts
                   WHERE thread_id = ? AND (created_at, id) > (?, ?)
                   ORDER BY created_at ASC, id ASC
                   LIMIT ?""",
                (thread_id, cursor_time, cursor_id, per_page),
            )
        else:
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
        row = db.execute(
            """SELECT EXISTS(
                 SELECT 1 FROM posts
                 WHERE thread_id = ? AND (created_at, id) > (?, ?)
               )""",
            (thread_id, last_created_at, last_id),
        ).fetchone()
        return row[0] == 1

    @classmethod
    def create(cls, thread_id: int, name: str, body: str) -> None:
        db = Database.get_instance()
        with Database.write_lock:
            db.execute(
                "INSERT INTO posts (thread_id, name, body, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, name, body, now_str()),
            )
            db.commit()
