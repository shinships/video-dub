from pathlib import Path
import sys

import pytest

# Thêm đường dẫn project root để import tools
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.file_lock import (
    acquire_file_lock,
    file_lock,
    is_file_locked,
    list_active_locks,
    release_file_lock,
)
from tools.watch_folder import (
    compress_video,
    get_output_path,
    is_candidate_file,
    is_file_stable,
    load_history,
    save_history,
    scan_and_process,
    send_telegram_notification,
    send_telegram_video,
)


def test_get_output_path():
    source = Path("/tmp/my_video.mp4")
    out = get_output_path(source)
    assert out == Path("/tmp/my_video_VN.mp4")

    custom_out_dir = Path("/tmp/custom_output")
    out2 = get_output_path(source, output_dir=custom_out_dir)
    assert out2 == Path("/tmp/custom_output/my_video_VN.mp4")


def test_is_candidate_file(tmp_path):
    valid_video = tmp_path / "clip.mp4"
    valid_video.touch()
    assert is_candidate_file(valid_video) is True

    # File kết quả có hậu tố _VN -> phải bỏ qua để không lặp vô tận
    vn_output = tmp_path / "clip_VN.mp4"
    vn_output.touch()
    assert is_candidate_file(vn_output) is False

    # File nén 720p hoặc _compressed -> phải bỏ qua
    compressed = tmp_path / "clip_VN_compressed.mp4"
    compressed.touch()
    assert is_candidate_file(compressed) is False

    p720 = tmp_path / "clip_VN_720p.mp4"
    p720.touch()
    assert is_candidate_file(p720) is False

    # File có đuôi .vi.mp4 cũ -> bỏ qua
    legacy_output = tmp_path / "clip.vi.mp4"
    legacy_output.touch()
    assert is_candidate_file(legacy_output) is False

    # File ẩn
    hidden = tmp_path / ".hidden_clip.mp4"
    hidden.touch()
    assert is_candidate_file(hidden) is False

    # File tải dở
    part = tmp_path / "clip.part"
    part.touch()
    assert is_candidate_file(part) is False

    # File định dạng khác
    txt = tmp_path / "note.txt"
    txt.touch()
    assert is_candidate_file(txt) is False

    # Thư mục
    sub = tmp_path / "subdir"
    sub.mkdir()
    assert is_candidate_file(sub) is False


def test_is_file_stable(tmp_path):
    f = tmp_path / "sample.mp4"
    # File rỗng 0-byte -> không ổn định
    f.touch()
    assert is_file_stable(f, wait_seconds=0.05) is False

    # File có dữ liệu
    f.write_bytes(b"dummy video content 123456")
    assert is_file_stable(f, wait_seconds=0.05) is True

    # File không tồn tại
    non_existent = tmp_path / "not_found.mp4"
    assert is_file_stable(non_existent, wait_seconds=0.05) is False


def test_load_and_save_history(tmp_path):
    hist_file = tmp_path / ".history.json"
    assert load_history(hist_file) == set()

    items = {"video1.mp4:100", "video2.mp4:200"}
    save_history(hist_file, items)
    loaded = load_history(hist_file)
    assert loaded == items


def test_scan_and_process_skips_existing(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    done_dir = tmp_path / "done"

    # Tạo 1 video và 1 video_VN.mp4 tương ứng sẵn
    video = watch_dir / "intro.mp4"
    video.write_bytes(b"data1")
    vn_video = watch_dir / "intro_VN.mp4"
    vn_video.write_bytes(b"data2")
    orig_size = video.stat().st_size

    calls = []
    monkeypatch.setattr(
        "tools.watch_folder.process_video",
        lambda *args, **kwargs: calls.append(args) or True,
    )

    history_file = watch_dir / ".history.json"
    count = scan_and_process(
        watch_dir=watch_dir,
        output_dir=watch_dir,
        move_done_dir=done_dir,
        voice="Minh Đức",
        history_file=history_file,
    )

    # Đã có intro_VN.mp4 nên không gọi process_video
    assert count == 0
    assert len(calls) == 0
    # Và đã được ghi nhận vào lịch sử
    hist = load_history(history_file)
    assert f"intro.mp4:{orig_size}" in hist
    # File gốc đã được chuyển vào done_dir
    assert (done_dir / "intro.mp4").is_file()


def test_scan_and_process_records_failed_file(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()

    video = watch_dir / "silent_video.mp4"
    video.write_bytes(b"data12345")

    # Giả lập process_video thất bại kèm chi tiết lỗi cụ thể
    monkeypatch.setattr(
        "tools.watch_folder.process_video",
        lambda *args, **kwargs: (False, "Quá trình nhận dạng giọng nói (Whisper) không phát hiện thấy lời thoại tiếng Anh nào trong video."),
    )

    sent_notifications = []
    monkeypatch.setattr(
        "tools.watch_folder.send_telegram_notification",
        lambda msg, **kwargs: sent_notifications.append(msg) or True,
    )

    history_file = watch_dir / ".history.json"
    count = scan_and_process(
        watch_dir=watch_dir,
        output_dir=None,
        voice="Minh Đức",
        history_file=history_file,
        notify_telegram=True,
    )

    assert count == 0
    # Đã ghi nhận vào lịch sử để không bị lặp lại ở lần quét tiếp theo
    hist = load_history(history_file)
    assert f"silent_video.mp4:{video.stat().st_size}" in hist

    # Kiểm tra nội dung thông báo Telegram có đầy đủ thông tin cụ thể
    assert len(sent_notifications) == 1
    msg = sent_notifications[0]
    assert "silent_video" in msg
    assert "Quá trình nhận dạng giọng nói (Whisper) không phát hiện thấy lời thoại" in msg
    assert "File nguồn:" in msg
    assert "Chi tiết lý do:" in msg


def test_send_telegram_notification_missing_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_DUB_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert send_telegram_notification("hello") is False


def test_send_telegram_notification_success(monkeypatch):
    import json

    called_payload = []

    class MockResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout):
        called_payload.append(json.loads(req.data.decode("utf-8")))
        return MockResponse()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    res = send_telegram_notification(
        message="<b>Test</b>",
        token="bot12345",
        chat_id="-1009999",
        topic_id="3",
    )
    assert res is True
    assert len(called_payload) == 1
    assert called_payload[0]["chat_id"] == "-1009999"
    assert called_payload[0]["message_thread_id"] == 3
    assert called_payload[0]["text"] == "<b>Test</b>"


def test_send_telegram_video_oversize(tmp_path, monkeypatch):
    big_video = tmp_path / "large.mp4"
    big_video.write_bytes(b"0" * 1024)

    text_calls = []
    monkeypatch.setattr(
        "tools.watch_folder.send_telegram_notification",
        lambda msg, **kw: text_calls.append(msg) or True,
    )

    real_stat = big_video.stat()

    class FakeStat:
        st_size = 60 * 1024 * 1024
        st_mode = real_stat.st_mode

    monkeypatch.setattr(Path, "stat", lambda self: FakeStat())

    res = send_telegram_video(
        big_video,
        caption="Video to",
        token="token123",
        chat_id="-1001",
        topic_id="3",
    )
    assert res is True
    assert len(text_calls) == 1
    assert "vượt trần 50MB" in text_calls[0]


def test_send_telegram_video_success(tmp_path, monkeypatch):
    sample_video = tmp_path / "sample.mp4"
    sample_video.write_bytes(b"video content")

    posted_files = []

    class MockHttpxResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    def mock_post(url, data, files, timeout):
        posted_files.append((url, data, files))
        return MockHttpxResponse()

    monkeypatch.setattr("httpx.post", mock_post)
    monkeypatch.setattr(
        "tools.watch_folder.probe_video",
        lambda _path: {"width": 1280, "height": 720, "duration": 79},
    )

    res = send_telegram_video(
        sample_video,
        caption="<b>Clip hay</b>",
        token="token_abc",
        chat_id="-1003879",
        topic_id="3",
    )
    assert res is True
    assert len(posted_files) == 1
    assert "https://api.telegram.org/bottoken_abc/sendVideo" in posted_files[0][0]
    assert posted_files[0][1]["chat_id"] == "-1003879"
    assert posted_files[0][1]["message_thread_id"] == "3"
    assert posted_files[0][1]["width"] == "1280"
    assert posted_files[0][1]["height"] == "720"
    assert posted_files[0][1]["duration"] == "79"


def test_send_telegram_video_returns_false_on_probe_failure_without_upload(
    tmp_path, monkeypatch, capsys
):
    sample_video = tmp_path / "unreadable.mp4"
    sample_video.write_bytes(b"video content")
    upload_calls = []

    def fail_probe(_path):
        raise RuntimeError("ffprobe không đọc được video")

    monkeypatch.setattr("tools.watch_folder.probe_video", fail_probe)
    monkeypatch.setattr(
        "httpx.post",
        lambda *args, **kwargs: upload_calls.append((args, kwargs)),
    )

    result = send_telegram_video(
        sample_video,
        caption="Clip lỗi",
        token="token_abc",
        chat_id="-1003879",
    )

    assert result is False
    assert upload_calls == []
    assert "[Telegram] Không thể probe metadata video" in capsys.readouterr().err


def test_compress_video_invokes_script(tmp_path, monkeypatch):
    fake_script = tmp_path / "fake_compress.py"
    fake_script.write_text("print('compressing')", encoding="utf-8")

    src = tmp_path / "clip_VN.mp4"
    src.write_bytes(b"dummy")

    # Giả lập script tạo file _compressed.mp4
    def mock_run(cmd, check=True):
        compressed = src.parent / f"{src.stem}_compressed.mp4"
        compressed.write_bytes(b"compressed dummy")
        return None

    monkeypatch.setattr("subprocess.run", mock_run)

    out = compress_video(src, resolution=720, compress_script=fake_script)
    assert out is not None
    assert out.name == "clip_VN_720p.mp4"
    assert out.is_file()


def test_scan_and_process_compresses_and_sends_video(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    video = watch_dir / "vid.mp4"
    video.write_bytes(b"some video bytes")

    def mock_process(source_path, output_path, *args, **kwargs):
        output_path.write_bytes(b"master 1080p")
        return True

    monkeypatch.setattr(
        "tools.watch_folder.process_video",
        mock_process,
    )
    # Giả lập nén trả về file 720p
    monkeypatch.setattr(
        "tools.watch_folder.compress_video",
        lambda path, resolution: path.parent / f"{path.stem}_720p.mp4",
    )
    p720 = watch_dir / "vid_VN_720p.mp4"
    p720.write_bytes(b"720p content")

    sent_videos = []
    monkeypatch.setattr(
        "tools.watch_folder.send_telegram_video",
        lambda path, caption: sent_videos.append((path, caption)) or True,
    )

    out_dir = tmp_path / "output"
    done_dir = tmp_path / "done"

    count = scan_and_process(
        watch_dir=watch_dir,
        output_dir=out_dir,
        move_done_dir=done_dir,
        voice="Minh Đức",
        history_file=watch_dir / ".history.json",
        notify_telegram=True,
        compress_720=True,
    )
    assert count == 1
    assert len(sent_videos) == 1
    # File thành phẩm 720p đã được chuẩn hoá tên theo keywords ngắn gọn dạng <keywords>_VN.mp4 trong output_dir
    assert sent_videos[0][0].parent == out_dir
    assert sent_videos[0][0].name.endswith("_VN.mp4")
    assert sent_videos[0][0].is_file()
    assert sent_videos[0][1].startswith("✅ ")
    # File gốc đã được chuyển vào done_dir (Originals)
    assert (done_dir / "vid.mp4").is_file()


def test_file_lock_acquire_and_release(tmp_path):
    target = tmp_path / "video1.mp4"
    target.touch()

    # Chưa khoá
    locked, info = is_file_locked(target)
    assert locked is False
    assert info == {}

    # Chiếm khoá
    fp = acquire_file_lock(target, owner="test_owner", details={"foo": "bar"})
    assert fp is not None

    # Bây giờ phải báo là đang khoá
    locked, info = is_file_locked(target)
    assert locked is True
    assert info.get("owner") == "test_owner"
    assert info.get("details", {}).get("foo") == "bar"

    # Giải phóng khoá
    release_file_lock(fp)

    # Sau khi giải phóng, is_file_locked phải trả về False
    locked, info = is_file_locked(target)
    assert locked is False


def test_file_lock_concurrent_exclusion(tmp_path):
    target = tmp_path / "video2.mp4"
    target.touch()

    # Tiến trình 1 chiếm khoá
    fp1 = acquire_file_lock(target, owner="proc1")
    assert fp1 is not None

    # Tiến trình 2 cùng chiếm khoá trên file đó -> phải thất bại (trả về None)
    fp2 = acquire_file_lock(target, owner="proc2")
    assert fp2 is None

    # Giải phóng fp1
    release_file_lock(fp1)

    # Bây giờ tiến trình 2 có thể chiếm được
    fp3 = acquire_file_lock(target, owner="proc2")
    assert fp3 is not None
    release_file_lock(fp3)


def test_file_lock_context_manager(tmp_path):
    target = tmp_path / "video3.mp4"
    target.touch()

    with file_lock(target, owner="ctx_owner") as acquired:
        assert acquired is True
        locked, info = is_file_locked(target)
        assert locked is True
        assert info.get("owner") == "ctx_owner"

    # Ra khỏi context manager -> tự giải phóng
    locked, _ = is_file_locked(target)
    assert locked is False


def test_list_active_locks(tmp_path):
    locks_dir = tmp_path / ".locks"
    v1 = tmp_path / "clip1.mp4"
    v2 = tmp_path / "clip2.mp4"
    v1.touch()
    v2.touch()

    assert list_active_locks(locks_dir) == []

    fp1 = acquire_file_lock(v1, owner="bot", locks_dir=locks_dir)
    assert fp1 is not None

    active = list_active_locks(locks_dir)
    assert len(active) == 1
    assert active[0]["owner"] == "bot"
    assert active[0]["file"] == "clip1.mp4"

    fp2 = acquire_file_lock(v2, owner="watch", locks_dir=locks_dir)
    assert fp2 is not None

    active2 = list_active_locks(locks_dir)
    assert len(active2) == 2
    files = {a["file"] for a in active2}
    assert files == {"clip1.mp4", "clip2.mp4"}

    release_file_lock(fp1)
    release_file_lock(fp2)

    assert list_active_locks(locks_dir) == []


def test_scan_and_process_skips_locked_file(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    video = watch_dir / "busy_video.mp4"
    video.write_bytes(b"some content")

    # Giả lập bot đang chiếm khoá video này
    fp = acquire_file_lock(video, owner="telegram_bot", details={"title": "Busy Vid"})
    assert fp is not None

    calls = []
    monkeypatch.setattr(
        "tools.watch_folder.process_video",
        lambda *args, **kwargs: calls.append(args) or True,
    )

    history_file = watch_dir / ".history.json"
    count = scan_and_process(
        watch_dir=watch_dir,
        output_dir=None,
        voice="Minh Đức",
        history_file=history_file,
    )

    # File đang bị lock bởi bot -> watch_folder phải bỏ qua, không được chạy!
    assert count == 0
    assert len(calls) == 0

    # Và chưa được ghi vào history (để bot hoàn tất sau này)
    hist = load_history(history_file)
    assert f"busy_video.mp4:{video.stat().st_size}" not in hist

    # Giải phóng khoá
    release_file_lock(fp)


def test_is_candidate_file_skips_originals(tmp_path):
    orig_dir = tmp_path / "Originals"
    orig_dir.mkdir()
    video_in_orig = orig_dir / "clip.mp4"
    video_in_orig.touch()

    # Phải bỏ qua các file nằm trong thư mục Originals
    assert is_candidate_file(video_in_orig) is False


def test_cleanup_originals(tmp_path):
    from tools.cleanup_originals import cleanup_originals

    orig_dir = tmp_path / "Originals"
    orig_dir.mkdir()

    f1 = orig_dir / "vid1.mp4"
    f2 = orig_dir / "vid2.mp4"
    hidden = orig_dir / ".keep"
    f1.write_bytes(b"data1")
    f2.write_bytes(b"data2")
    hidden.write_bytes(b"hidden")

    # Dry-run: không xoá file
    deleted_dry = cleanup_originals(orig_dir, dry_run=True)
    assert deleted_dry == 2
    assert f1.is_file()
    assert f2.is_file()
    assert hidden.is_file()

    # Real run: xoá 2 file video, giữ file ẩn
    deleted_real = cleanup_originals(orig_dir, dry_run=False)
    assert deleted_real == 2
    assert not f1.is_file()
    assert not f2.is_file()
    assert hidden.is_file()


def test_sanitize_video_title():
    from tools.telegram_dub_bot import sanitize_video_title

    # 1. Các trường hợp cụ thể người dùng yêu cầu:
    t1 = "Use ChatGPT Images to explore campaign concepts [Ez-anO32D_s]"
    assert sanitize_video_title(t1) == "Use ChatGPT Images to explore campaign concepts"

    t2 = "What a Smuggled North Korean Smartphone Reveals About the Regime ｜ WSJ [nBRZKG1kjBM]"
    assert sanitize_video_title(t2, channel="WSJ") == "What a Smuggled North Korean Smartphone Reveals About the Regime"

    t3 = "Google Open-Sourced This Offline Translator — So I Rebuilt It [2UTQJ56Wnd4]"
    assert sanitize_video_title(t3) == "Google Open-Sourced This Offline Translator — So I Rebuilt It"

    # 2. Xoá nhãn kênh sau dấu phân cách pipe chuẩn (|) hoặc //
    t4 = "How Putin War Pay | Bloomberg Originals [abc12345678]"
    assert sanitize_video_title(t4) == "How Putin War Pay"

    t5 = "Breaking News // BBC News"
    assert sanitize_video_title(t5) == "Breaking News"

    # 3. Kênh xuất hiện sau dấu gạch ngang
    t6 = "SpaceX Launch Update - CNBC"
    assert sanitize_video_title(t6, channel="CNBC") == "SpaceX Launch Update"

    # 4. Xoá emoji và chuẩn hoá dấu hai chấm
    t7 = "🔥 Top 5 AI Breakthroughs: You Must See! 🚀"
    assert sanitize_video_title(t7) == "Top 5 AI Breakthroughs - You Must See!"

    # 5. Xoá các tag kỹ thuật [Official Video], [HD], (4K)...
    t8 = "Hit Song (Official Music Video) [4K] [60FPS]"
    assert sanitize_video_title(t8) == "Hit Song"

    # 6. Fallback an toàn khi tiêu đề rỗng
    assert sanitize_video_title("", video_id="abc123") == "video_abc123"
    assert sanitize_video_title("   ") == "video"


def test_generate_concise_keywords(tmp_path):
    from tools.keyword_renamer import generate_concise_keywords, get_keyword_output_path, rule_based_keywords

    # 1. Test rule-based keywords logic
    t1 = "What a Smuggled North Korean Smartphone Reveals About the Regime"
    kw1 = rule_based_keywords(t1, max_words=4)
    assert len(kw1.split()) <= 4
    assert "Smuggled" in kw1
    assert "North" in kw1

    # 2. Test generate_concise_keywords function
    t2 = "Google Open-Sourced This Offline Translator — So I Rebuilt It"
    kw2 = generate_concise_keywords(t2, max_words=4)
    assert len(kw2.split()) <= 4
    assert "Google" in kw2

    # 3. Test get_keyword_output_path
    out_dir = tmp_path / "out"
    target = get_keyword_output_path("cargo drone", out_dir, suffix="_VN")
    assert target.parent == out_dir
    assert target.name.endswith("_VN.mp4")
    assert "Cargo" in target.name or "Drone" in target.name




