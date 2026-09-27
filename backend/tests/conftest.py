import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock
import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Tách DB/thư mục dữ liệu của test khỏi dữ liệu thật. Trước đây test chạy thẳng vào
# data/video_dub.sqlite3 của người dùng: vừa ghi rác vào dự án thật, vừa khiến test "xanh
# giả" nhờ dữ liệu còn sót từ lần chạy trước (vd job demo).
# Vẫn phải đặt TRƯỚC khi import app.config, vì SettingsStore đọc env ngay lúc khởi tạo ở
# cuối module đó. (Env vẫn là một nguồn cấu hình hợp lệ, xếp sau keychain và settings.json.)
os.environ["VIDEO_DUB_DATA_DIR"] = tempfile.mkdtemp(prefix="video-dub-test-")

# Không đụng keychain thật của máy dev: nếu lập trình viên đã lưu API key qua màn hình Cài
# đặt thì keychain xếp TRƯỚC env, và test sẽ đọc phải key thật -> kết quả phụ thuộc vào máy.
os.environ["VIDEO_DUB_USE_KEYCHAIN"] = "false"


@pytest.fixture(autouse=True)
def prevent_real_telegram_calls(monkeypatch):
    """Bảo đảm tuyệt đối: Mọi lệnh gọi tới Telegram API trong lúc chạy test đều được chặn/mock giả lập,
    không bao giờ gửi tin nhắn thật đến nhóm Telegram của người dùng."""
    real_urlopen = urllib.request.urlopen

    def safe_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "api.telegram.org" in url:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 999999}}'
            return mock_resp
        return real_urlopen(req, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", safe_urlopen)


@pytest.fixture(autouse=True)
def bypass_local_token():
    """Bỏ qua token cục bộ cho các test KHÁC, để chúng tập trung vào thứ đang kiểm.

    Là dependency của app (không phải middleware) chính vì chỗ này: ghi đè được gọn gàng, và
    test riêng cho phần xác thực chỉ việc gỡ ghi đè ra. Xem `tests/test_auth.py`.
    """
    from app.auth import require_token
    from app.main import app

    app.dependency_overrides[require_token] = lambda: None
    yield
    app.dependency_overrides.pop(require_token, None)
