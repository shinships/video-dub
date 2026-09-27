"""Khoá API cục bộ.

App giữ credit và API key của người dùng, nên để cổng 8010 mở cho MỌI tiến trình trên máy là
không chấp nhận được: bất kỳ script nào đang chạy dưới cùng tài khoản cũng gọi được
`PUT /api/settings` hay đọc job. CORS không phải cơ chế bảo mật — nó chỉ ràng buộc trình duyệt,
`curl` và mọi thứ không phải trình duyệt đều bỏ qua.

Mô hình mối đe doạ ở đây là *tiến trình khác trên cùng máy*, không phải mạng ngoài (server đã
bind 127.0.0.1). Một token ngẫu nhiên lưu trong thư mục dữ liệu là đủ và không bắt người dùng
phải đăng nhập.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request, Response

from .config import settings


TOKEN_HEADER = "X-Video-Dub-Token"
TOKEN_COOKIE = "video_dub_token"
TOKEN_FILENAME = "api-token"

_cached: str | None = None


def token_path() -> Path:
    return settings.data_dir / TOKEN_FILENAME


def load_or_create_token() -> str:
    """Token ổn định qua các lần khởi động: sinh mới mỗi lần thì frontend đang mở sẽ mất
    quyền sau mỗi lần restart backend, và dev proxy phải đọc lại liên tục."""
    global _cached
    if _cached:
        return _cached

    from_env = (os.getenv("VIDEO_DUB_API_TOKEN") or "").strip()
    if from_env:
        _cached = from_env
        return _cached

    path = token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            _cached = existing
            return _cached
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Ghi rồi mới siết quyền là có cửa sổ đọc trộm; tạo file với 0600 ngay từ đầu.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    _cached = token
    return token


def reset_cache() -> None:
    """Quên token đã nhớ (dùng cho test, và khi đổi thư mục dữ liệu)."""
    global _cached
    _cached = None


def _presented(request: Request) -> str | None:
    header = request.headers.get(TOKEN_HEADER)
    if header:
        return header.strip()
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.cookies.get(TOKEN_COOKIE)


def require_token(request: Request, response: Response) -> None:
    """Chặn mọi route. Nhận token qua header, `Authorization: Bearer`, hoặc cookie.

    Phải chấp nhận cookie vì `<video src>`, `<audio src>`, link tải và `EventSource` KHÔNG đặt
    được header — mà nhét token vào query string thì nó chui thẳng vào log truy cập của uvicorn.
    Nên khi xác thực bằng header, ta phát luôn cookie cho các request kiểu đó dùng.
    """
    expected = load_or_create_token()
    presented = _presented(request)
    # compare_digest: so sánh chuỗi thông thường thoát sớm ở byte đầu khác nhau, đủ để dò dần
    # từng ký tự của token.
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=401,
            detail="Thiếu hoặc sai token truy cập cục bộ.",
            headers={"WWW-Authenticate": TOKEN_HEADER},
        )
    if request.cookies.get(TOKEN_COOKIE) != expected:
        response.set_cookie(
            TOKEN_COOKIE,
            expected,
            httponly=True,
            samesite="strict",
            path="/",
        )
