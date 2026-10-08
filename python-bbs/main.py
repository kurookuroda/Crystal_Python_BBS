"""FastAPI掲示板のエントリポイント: uvicorn main:app --reload"""
from datetime import datetime
from typing import Optional, Tuple

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from database import Database
from models import BbsThread, Post

THREADS_PER_PAGE = 20
POSTS_PER_PAGE = 30
MAX_PAGE = 999_999
SQLITE_MAX_INT = 2**63 - 1  # SQLite INTEGER は64bit符号付き

app = FastAPI(title="Simple BBS")
templates = Jinja2Templates(directory="templates")

# アプリ起動時にDBを初期化
Database.get_instance()


def parse_cursor(
    cursor_time: Optional[str], cursor_id: Optional[str]
) -> Optional[Tuple[str, int]]:
    """URLのカーソルを検証する。不正なら None（= 先頭ページにフォールバック）。

    cursor_id を int で受けると FastAPI が 422 を返してしまうため、str で受けて自前で検証する。
    """
    if not cursor_time or not cursor_id:
        return None
    try:
        parsed_id = int(cursor_id)
        datetime.strptime(cursor_time, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    # 範囲外の値をSQLiteに渡すと OverflowError（500）になる
    if not 0 < parsed_id <= SQLITE_MAX_INT:
        return None
    return cursor_time, parsed_id


@app.get("/")
def index(request: Request, page: int = 1):
    total = BbsThread.count()
    total_pages = max(1, (total + THREADS_PER_PAGE - 1) // THREADS_PER_PAGE)
    page = min(max(1, page), MAX_PAGE, total_pages)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "threads": BbsThread.paginate(page, THREADS_PER_PAGE),
            "page": page,
            "total_pages": total_pages,
        },
    )


@app.post("/thread")
def create_thread(title: str = Form(..., max_length=200)):
    title = title.strip()
    if title:
        BbsThread.create(title)
    return RedirectResponse(url="/", status_code=303)


# {thread_id:int} により、数字以外のパスは自動で404になる
@app.get("/thread/{thread_id:int}")
def show_thread(
    request: Request,
    thread_id: int,
    cursor_time: Optional[str] = None,
    cursor_id: Optional[str] = None,
):
    thread = BbsThread.find(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="スレッドが見つかりません")

    cursor = parse_cursor(cursor_time, cursor_id)
    posts = Post.after(
        thread_id,
        cursor[0] if cursor else None,
        cursor[1] if cursor else None,
        POSTS_PER_PAGE,
    )

    last_post = posts[-1] if posts else None
    has_next = last_post is not None and Post.has_more(
        thread_id, last_post.created_at, last_post.id
    )

    return templates.TemplateResponse(
        request,
        "thread.html",
        {
            "thread": thread,
            "posts": posts,
            "has_next": has_next,
            "last_post": last_post,
        },
    )


@app.post("/thread/{thread_id:int}/post")
def create_post(
    thread_id: int,
    name: Optional[str] = Form(default="", max_length=100),
    body: str = Form(..., max_length=10_000),
):
    if BbsThread.find(thread_id) is None:
        raise HTTPException(status_code=404, detail="スレッドが見つかりません")

    name = (name or "").strip() or "名無しさん"
    body = body.strip()
    if body:
        Post.create(thread_id, name, body)

    # PRGパターン: リロードによる二重投稿を防ぐ
    return RedirectResponse(url=f"/thread/{thread_id}", status_code=303)
