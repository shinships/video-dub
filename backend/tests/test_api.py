import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.db import init_db
from app.pipeline import seed_demo_job


@pytest.fixture(autouse=True)
def _seed_demo_job():
    """Job demo chỉ còn được seed lúc khởi động khi bật demo mode tường minh, nên test phải
    tự dựng nó — không dựa vào dữ liệu sót lại trong DB như trước."""
    init_db()
    seed_demo_job()


def test_health_and_demo_job():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        job = client.get("/api/jobs/demo")
        assert job.status_code == 200
        payload = job.json()
        assert payload["voice"] == "Aoede"
        assert len(payload["segments"]) >= 7


def test_voices_catalog_lists_only_vieneu():
    with TestClient(app) as client:
        response = client.get("/api/voices")
        assert response.status_code == 200
        payload = response.json()
        # Vbee và Gemini TTS đã gỡ — chỉ còn một engine local duy nhất.
        assert payload["default_engine"] == "vieneu"
        assert [engine["id"] for engine in payload["engines"]] == ["vieneu"]
        # Chưa cài vieneu (preset đọc không được) vẫn phải có 1 giọng từ env để UI chọn.
        assert all(engine["voices"] for engine in payload["engines"])
        assert all(engine["gendered_voices"] for engine in payload["engines"])


def test_patch_job_ignores_removed_tts_engine_field():
    # Chỉ còn một engine nên tts_engine đã rời khỏi API; client cũ gửi lên thì bỏ qua,
    # KHÔNG được ghi vào DB (ghi vào sẽ làm resolve_tts_engine đọc phải engine đã gỡ).
    with TestClient(app) as client:
        response = client.patch("/api/jobs/demo", json={"tts_engine": "vbee", "style": "Trang trọng"})
        assert response.status_code == 200
        assert response.json()["style"] == "Trang trọng"
        assert client.get("/api/jobs/demo").json().get("tts_engine") != "vbee"


def test_media_endpoints_return_404_when_files_missing():
    # Job demo không có source_path và segment chưa có audio_path.
    with TestClient(app) as client:
        job = client.get("/api/jobs/demo").json()
        assert client.get("/api/jobs/demo/source").status_code == 404
        segment_id = job["segments"][0]["id"]
        assert client.get(f"/api/jobs/demo/segments/{segment_id}/audio").status_code == 404
        assert client.get("/api/jobs/demo/segments/khong-ton-tai/audio").status_code == 404
        assert client.get("/api/jobs/demo/download?kind=srt").status_code == 404
        assert client.get("/api/jobs/khong-ton-tai/source").status_code == 404


def test_update_segment_only_changes_selected_segment():
    with TestClient(app) as client:
        before = client.get("/api/jobs/demo").json()
        target = before["segments"][1]
        try:
            response = client.patch(
                f"/api/jobs/demo/segments/{target['id']}",
                json={"translated_text": "Bản dịch đã được chỉnh sửa."},
            )
            assert response.status_code == 200
            after = response.json()
            assert after["segments"][1]["translated_text"] == "Bản dịch đã được chỉnh sửa."
            assert after["segments"][0]["translated_text"] == before["segments"][0]["translated_text"]
        finally:
            client.patch(
                f"/api/jobs/demo/segments/{target['id']}",
                json={"translated_text": before["segments"][1]["translated_text"]},
            )


def test_create_job_refuses_when_config_missing(monkeypatch):
    """Thiếu API key thì phải TỪ CHỐI nhận video, không âm thầm chạy demo.

    Đây là hồi quy quan trọng nhất về mặt sản phẩm: bản cũ rơi vào demo mode trong im lặng
    nên người dùng gõ sai key vẫn nhận về transcript bịa sẵn mà tưởng là kết quả thật.
    """
    from app.config import Settings

    broken = Settings(gemini_api_key="", deepseek_api_key="", google_project="", demo_mode=False)
    assert [item["key"] for item in broken.missing_requirements] == ["translator_key"]
    monkeypatch.setattr(main_module, "settings", broken)

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            files={"file": ("clip.mp4", b"khong-phai-video-that", "video/mp4")},
        )
        assert response.status_code == 422
        assert "API key" in response.json()["detail"]


def test_health_reports_what_is_missing(monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(gemini_api_key="", deepseek_api_key="", google_project="", demo_mode=False),
    )
    with TestClient(app) as client:
        payload = client.get("/api/health").json()
        assert payload["ready"] is False
        assert [item["key"] for item in payload["missing"]] == ["translator_key"]
        # Thiếu cấu hình KHÔNG còn tự bật demo mode.
        assert payload["demo_mode"] is False


def test_delete_job_removes_row_and_files(tmp_path):
    """Xoá dự án phải dọn cả DB lẫn đĩa — trước đây không có đường xoá nên data/ chỉ phình."""
    from app.config import settings
    from app.db import get_job

    with TestClient(app) as client:
        assert client.get("/api/jobs/demo").status_code == 200

        # Dựng file giả giống hệt bố cục thật của một job đã xuất xong.
        work = settings.jobs_dir / "demo"
        (work / "demucs" / "htdemucs" / "source").mkdir(parents=True, exist_ok=True)
        (work / "dubbed-vi.mp4").write_bytes(b"x" * 100)
        (work / "demucs" / "htdemucs" / "source" / "no_vocals.wav").write_bytes(b"y" * 50)
        upload = settings.uploads_dir / "demo.mp4"
        upload.write_bytes(b"z" * 30)

        response = client.delete("/api/jobs/demo")
        assert response.status_code == 200
        assert response.json()["freed_bytes"] == 180

        assert not work.exists()
        assert not upload.exists()
        assert get_job("demo") is None
        assert client.get("/api/jobs/demo").status_code == 404
        # Segments đi theo nhờ ON DELETE CASCADE.
        assert all(job["id"] != "demo" for job in client.get("/api/jobs").json())


def test_delete_missing_job_is_404():
    with TestClient(app) as client:
        assert client.delete("/api/jobs/khong-ton-tai").status_code == 404


@pytest.fixture
def clean_settings_file():
    """PUT /api/settings ghi settings.json vào data_dir dùng chung của test và reload store
    toàn cục — phải dọn, nếu không test sau lại chạy trên cấu hình do test trước để lại."""
    from app.config import store

    path = store.current.settings_file
    yield
    path.unlink(missing_ok=True)
    store.reload()


def test_settings_endpoint_never_returns_secret_values(clean_settings_file):
    """API key đã lưu thì không có lý do gì chạy ngược ra ngoài qua HTTP. UI chỉ cần biết
    'đã đặt hay chưa' để hiện placeholder."""
    from app.config import SECRET_FIELDS, store

    with TestClient(app) as client:
        payload = client.get("/api/settings").json()

    for name in SECRET_FIELDS:
        assert payload["values"][name] == ""
        assert name in payload["secrets_set"]
    # Nhưng phải phản ánh đúng thực tế là đang có key hay không.
    assert payload["secrets_set"]["deepseek_api_key"] == bool(store.current.deepseek_api_key)


def test_put_settings_refuses_fields_outside_whitelist(clean_settings_file):
    """Hồi quy bảo mật: tên field do client gửi không được chảy thẳng vào chỗ ghi dữ liệu."""
    with TestClient(app) as client:
        for bad in ({"data_dir": "/tmp/x"}, {"deepseek_base_url": "http://ke-tan-cong"}, {"cost": "1"}):
            response = client.put("/api/settings", json=bad)
            assert response.status_code == 422
            assert "không cho sửa" in response.json()["detail"]


def test_put_settings_takes_effect_without_restart(clean_settings_file):
    """Tiêu chí nghiệm thu của bước này: đổi cài đặt xong pipeline thấy ngay, không phải
    khởi động lại backend."""
    from app.config import settings, store

    assert store.current.whisper_model != "large-v3"
    with TestClient(app) as client:
        response = client.put("/api/settings", json={"whisper_model": "large-v3"})
        assert response.status_code == 200
        assert response.json()["values"]["whisper_model"] == "large-v3"

    assert settings.whisper_model == "large-v3"  # proxy, không phải ảnh chụp cũ


def test_put_settings_rejects_invalid_choice(clean_settings_file):
    with TestClient(app) as client:
        response = client.put("/api/settings", json={"stt_engine": "khong-ton-tai"})
        assert response.status_code == 422
        assert "whisper" in response.json()["detail"]


def test_settings_endpoint_describes_fields_for_the_form(clean_settings_file):
    with TestClient(app) as client:
        payload = client.get("/api/settings").json()

    by_name = {field["name"]: field for field in payload["fields"]}
    assert by_name["stt_engine"]["choices"] == ["whisper", "google"]
    assert by_name["multi_speaker"]["type"] == "bool"
    assert by_name["gemini_api_key"]["secret"] is True
    assert all(field["label"] for field in payload["fields"])
    # UI cần biết giá trị đang đến từ đâu để giải thích ô mình không tự điền.
    assert set(payload["sources"]) == set(by_name)


def test_export_twice_queues_only_one_render():
    """Hồi quy về tài nguyên: export cũ dùng BackgroundTasks nên đi vòng qua hàng đợi —
    bấm N lần là N×TTS_WORKERS tiến trình TTS cùng lúc trên máy người dùng."""
    import app.main as main

    main.pending_tasks.clear()
    with TestClient(app) as client:
        first = client.post("/api/jobs/demo/export")
        second = client.post("/api/jobs/demo/export")

    assert first.json()["status"] == "processing"
    assert second.json()["status"] == "already_queued"


def test_retry_on_export_stage_does_not_re_translate(tmp_path):
    """Job hỏng ở pha export mà chạy lại cả pipeline là gọi lại API dịch — tính tiền người
    dùng lần thứ hai cho phần đã làm xong."""
    from app.db import update_job

    background = tmp_path / "no_vocals.wav"
    background.write_bytes(b"RIFF....WAVE")

    with TestClient(app) as client:
        update_job("demo", status="failed", stage="export", artifacts={"background": str(background)})
        assert client.post("/api/jobs/demo/retry").json()["kind"] == "export"

        update_job("demo", status="failed", stage="translate")
        assert client.post("/api/jobs/demo/retry").json()["kind"] == "process"

        # Nền đã mất thì dù stage là "export" cũng phải chạy lại từ đầu, chứ không lao vào
        # một lần export chắc chắn hỏng.
        background.unlink()
        update_job("demo", status="failed", stage="export")
        assert client.post("/api/jobs/demo/retry").json()["kind"] == "process"


def test_cancel_reports_how_many_processes_it_killed():
    with TestClient(app) as client:
        payload = client.post("/api/jobs/demo/cancel").json()

    assert payload["status"] == "cancelled"
    assert payload["killed"] == 0  # job demo không sinh tiến trình con nào
