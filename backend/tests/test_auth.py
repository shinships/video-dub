"""Test cho khoá API cục bộ (bước F).

Đây là file DUY NHẤT gỡ ghi đè token trong conftest — mọi test khác chạy với xác thực đã bỏ
qua, nên nếu ở đây thiếu ca nào thì không còn chỗ nào bắt được.
"""

import os
import stat

import pytest
from fastapi.testclient import TestClient

from app.auth import TOKEN_COOKIE, TOKEN_HEADER, load_or_create_token, reset_cache, require_token
from app.db import init_db
from app.main import app
from app.pipeline import seed_demo_job


@pytest.fixture(autouse=True)
def enforce_token():
    """Bật lại xác thực thật cho riêng file này."""
    init_db()
    seed_demo_job()
    app.dependency_overrides.pop(require_token, None)
    yield


@pytest.fixture
def token():
    return load_or_create_token()


def test_request_without_token_is_refused(token):
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 401
    assert "token" in response.json()["detail"].lower()


def test_wrong_token_is_refused(token):
    with TestClient(app) as client:
        response = client.get("/api/health", headers={TOKEN_HEADER: "sai-token"})
    assert response.status_code == 401

    # Token đúng nhưng bị cắt cụt cũng phải hỏng — chống dò dần từng ký tự.
    with TestClient(app) as client:
        assert client.get("/api/health", headers={TOKEN_HEADER: token[:-1]}).status_code == 401


def test_header_token_is_accepted(token):
    with TestClient(app) as client:
        response = client.get("/api/health", headers={TOKEN_HEADER: token})
    assert response.status_code == 200


def test_bearer_token_is_accepted(token):
    with TestClient(app) as client:
        response = client.get("/api/health", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_authenticating_by_header_hands_back_a_cookie(token):
    """`<video src>`, link tải và EventSource KHÔNG đặt được header. Không phát cookie thì
    chúng chỉ còn đường nhét token vào query string — chui thẳng vào log của uvicorn."""
    with TestClient(app) as client:
        response = client.get("/api/health", headers={TOKEN_HEADER: token})

    assert response.cookies[TOKEN_COOKIE] == token
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header and "samesite=strict" in cookie_header


def test_cookie_alone_is_enough_for_media_requests(token):
    with TestClient(app) as client:
        client.cookies.set(TOKEN_COOKIE, token)
        response = client.get("/api/jobs/demo")
    assert response.status_code == 200


def test_every_route_is_covered_not_just_the_sensitive_looking_ones(token):
    """Gắn token theo từng route thì thêm endpoint mới là quên, mà quên kiểu này không ai
    phát hiện ra. Dependency đặt ở cấp app nên mặc định là đóng."""
    paths = ["/api/health", "/api/voices", "/api/jobs", "/api/jobs/demo", "/api/settings"]
    with TestClient(app) as client:
        assert [client.get(path).status_code for path in paths] == [401] * len(paths)


def test_write_endpoints_are_refused_too(token):
    """Điều thực sự cần chặn: một script khác trên cùng máy đọc/ghi được cấu hình."""
    with TestClient(app) as client:
        assert client.put("/api/settings", json={"whisper_model": "small"}).status_code == 401
        assert client.post("/api/jobs/demo/export").status_code == 401
        assert client.delete("/api/jobs/demo").status_code == 401


def test_token_survives_a_restart(token):
    """Sinh token mới mỗi lần khởi động thì frontend đang mở mất quyền sau mỗi lần restart."""
    reset_cache()
    assert load_or_create_token() == token


def test_token_file_is_not_readable_by_other_accounts():
    path = __import__("app.auth", fromlist=["token_path"]).token_path()
    load_or_create_token()

    if os.name == "nt":
        pytest.skip("Quyền POSIX không áp dụng trên Windows")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_env_token_wins_so_tauri_can_inject_one(monkeypatch):
    """Bản đóng gói sẽ truyền token cho sidecar qua env thay vì đọc file."""
    monkeypatch.setenv("VIDEO_DUB_API_TOKEN", "token-tu-tauri")
    reset_cache()
    try:
        assert load_or_create_token() == "token-tu-tauri"
        with TestClient(app) as client:
            assert client.get("/api/health", headers={TOKEN_HEADER: "token-tu-tauri"}).status_code == 200
    finally:
        reset_cache()


def test_token_file_exists_as_soon_as_the_server_is_up(tmp_path, monkeypatch):
    """Tạo token muộn (ở request đầu tiên) thì dev proxy của Vite — chạy trước trình duyệt —
    đọc được file rỗng, và dòng log chỉ đường tới một file chưa tồn tại."""
    import app.config as config_module
    from app.auth import token_path
    from app.config import SettingsStore

    monkeypatch.setenv("VIDEO_DUB_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VIDEO_DUB_API_TOKEN", raising=False)
    original = config_module.store
    config_module.store = SettingsStore()
    reset_cache()
    try:
        assert not token_path().exists()
        with TestClient(app):  # chỉ khởi động, chưa gửi request nào
            assert token_path().is_file()
            assert token_path().read_text(encoding="utf-8").strip()
    finally:
        config_module.store = original
        reset_cache()
