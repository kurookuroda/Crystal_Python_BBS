import html
import re

from database import Database
from models import BbsThread, Post

OVERFLOW_ID = 2**70
VALID_TIME = "2026-01-01%2000:00:00.000"


def make_thread(title="テスト", posts=0) -> int:
    thread_id = BbsThread.create(title)
    for i in range(posts):
        Post.create(thread_id, f"名無しさん{i}", f"本文{i}")
    return thread_id


def walk_pages(client, thread_id) -> list[int]:
    """「次へ」リンクを最後までたどり、各ページのレス数を返す。"""
    counts, url = [], f"/thread/{thread_id}"
    while True:
        r = client.get(url)
        assert r.status_code == 200
        counts.append(len(re.findall(r'class="post"', r.text)))
        m = re.search(r'href="(/thread/\d+\?cursor_time=[^"]+)"', r.text)
        if not m:
            return counts
        url = html.unescape(m.group(1))


def test_index_ok(client):
    assert client.get("/").status_code == 200


def test_create_thread_and_post_flow(client):
    r = client.post("/thread", data={"title": "新しいスレ"})
    assert r.status_code == 303 and r.headers["location"] == "/"

    thread_id = Database.get_instance().execute("SELECT MAX(id) FROM bbs_threads").fetchone()[0]
    r = client.post(f"/thread/{thread_id}/post", data={"body": "こんにちは\n2行目", "name": ""})
    assert r.status_code == 303 and r.headers["location"] == f"/thread/{thread_id}"

    page = client.get(f"/thread/{thread_id}").text
    assert "名無しさん" in page and "こんにちは" in page


def test_blank_title_and_body_are_ignored(client):
    before = BbsThread.count()
    client.post("/thread", data={"title": "   "})
    assert BbsThread.count() == before

    thread_id = make_thread()
    client.post(f"/thread/{thread_id}/post", data={"body": "   "})
    assert Post.after(thread_id, None, None) == []


def test_non_numeric_or_unknown_thread_is_404(client):
    assert client.get("/thread/abc").status_code == 404
    assert client.get("/thread/999999").status_code == 404
    assert client.post("/thread/999999/post", data={"body": "x"}).status_code == 404


def test_post_redirect_is_normalized(client):
    thread_id = make_thread()
    r = client.post(f"/thread/0{thread_id}/post", data={"body": "x"})
    assert r.headers["location"] == f"/thread/{thread_id}"


def test_xss_is_escaped(client):
    thread_id = make_thread("<script>alert(1)</script>")
    Post.create(thread_id, "<b>name</b>", "<img src=x onerror=alert(1)>")
    assert "<script>alert(1)</script>" not in client.get("/").text
    page = client.get(f"/thread/{thread_id}").text
    assert "&lt;script&gt;" in page
    assert "<img src=x" not in page and "<b>name</b>" not in page


def test_input_length_limits(client):
    assert client.post("/thread", data={"title": "a" * 201}).status_code == 422
    thread_id = make_thread()
    assert client.post(f"/thread/{thread_id}/post", data={"body": "a" * 10_001}).status_code == 422
    assert client.post(f"/thread/{thread_id}/post", data={"body": "x", "name": "a" * 101}).status_code == 422


def test_keyset_pagination_visits_every_post_exactly_once(client):
    # 同一ミリ秒の投稿が混ざっても、(created_at, id) のタイブレークで漏れ・重複が出ない
    thread_id = make_thread(posts=95)
    assert walk_pages(client, thread_id) == [30, 30, 30, 5]


def test_exact_multiple_of_page_size_has_no_empty_last_page(client):
    thread_id = make_thread(posts=60)
    assert walk_pages(client, thread_id) == [30, 30]


def test_invalid_cursor_falls_back_to_first_page(client):
    thread_id = make_thread(posts=3)
    base = f"/thread/{thread_id}"
    for query in (
        f"?cursor_time={VALID_TIME}&cursor_id=abc",
        "?cursor_time=zzz&cursor_id=1",
        f"?cursor_time={VALID_TIME}&cursor_id={OVERFLOW_ID}",
        f"?cursor_time={VALID_TIME}&cursor_id=-5",
        f"?cursor_time={VALID_TIME}",
    ):
        r = client.get(base + query)
        assert r.status_code == 200, query
        assert len(re.findall(r'class="post"', r.text)) == 3, query


def test_thread_list_pagination(client):
    for i in range(45):
        make_thread(f"page-test-{i}")
    assert client.get("/?page=2").status_code == 200
    # 範囲外・異常値でも落ちない（最終ページに丸められる）
    assert client.get("/?page=99999999999999999999").status_code in (200, 422)
    assert client.get("/?page=0").status_code == 200
    assert client.get("/?page=abc").status_code == 422
