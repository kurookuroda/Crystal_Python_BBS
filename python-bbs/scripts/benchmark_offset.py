"""OFFSET方式とキーセット方式を20万件のスレッドで比較する。
本体のDBは汚さず、専用の bench.db を作って測る: python scripts/benchmark_offset.py"""
import os
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone

DB_PATH = "bench.db"
TOTAL = 200_000
PER_PAGE = 30
OFFSETS = [0, 1_000, 10_000, 50_000, 100_000, 190_000]

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

db = sqlite3.connect(DB_PATH)
db.execute("""CREATE TABLE posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
)""")
db.execute("CREATE INDEX idx_posts_thread_created ON posts(thread_id, created_at, id)")

base = datetime(2026, 1, 1, tzinfo=timezone.utc)
rows = (
    (1, f"名無しさん{i}", "ベンチマーク用の本文です。",
     (base + timedelta(milliseconds=10 * i)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
    for i in range(TOTAL)
)
db.executemany("INSERT INTO posts (thread_id, name, body, created_at) VALUES (?, ?, ?, ?)", rows)
db.commit()
print(f"投入完了: {TOTAL:,}件 / SQLite {sqlite3.sqlite_version}\n")

OFFSET_SQL = """SELECT id, thread_id, name, body, created_at FROM posts
                WHERE thread_id = ? ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?"""
KEYSET_SQL = """SELECT id, thread_id, name, body, created_at FROM posts
                WHERE thread_id = ? AND (created_at, id) > (?, ?)
                ORDER BY created_at ASC, id ASC LIMIT ?"""


def measure(sql: str, params: tuple, runs: int = 7) -> float:
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        db.execute(sql, params).fetchall()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


print(f"{'offset':>8} | {'OFFSET方式':>12} | {'キーセット方式':>14}")
print("-" * 42)
for off in OFFSETS:
    if off == 0:
        t_off = measure(OFFSET_SQL, (1, PER_PAGE, 0))
        t_key = measure(
            "SELECT id, thread_id, name, body, created_at FROM posts "
            "WHERE thread_id = ? ORDER BY created_at ASC, id ASC LIMIT ?",
            (1, PER_PAGE),
        )
    else:
        cur_time, cur_id = db.execute(
            "SELECT created_at, id FROM posts WHERE thread_id = ? "
            "ORDER BY created_at ASC, id ASC LIMIT 1 OFFSET ?",
            (1, off - 1),
        ).fetchone()
        t_off = measure(OFFSET_SQL, (1, PER_PAGE, off))
        t_key = measure(KEYSET_SQL, (1, cur_time, cur_id, PER_PAGE))
    print(f"{off:>8,} | {t_off:>9.3f} ms | {t_key:>11.3f} ms")

db.close()
