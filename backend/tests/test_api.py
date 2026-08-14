import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


@pytest.fixture(autouse=True)
def _no_vbee_voices_api(monkeypatch):
    """/api/voices gọi API voices của Vbee -> chặn mạng trong test, mặc định coi như
    không lấy được danh sách (UI lùi về giọng cấu hình trong env)."""
    monkeypatch.setattr(main_module, "fetch_vbee_voices", lambda *a, **k: [])


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


def test_voices_catalog_lists_only_vieneu_and_vbee():
    with TestClient(app) as client:
        response = client.get("/api/voices")
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_engine"]
        engine_ids = [engine["id"] for engine in payload["engines"]]
        # Gemini TTS đã bị loại bỏ — chỉ còn VieNeu và Vbee.
        assert engine_ids == ["vieneu", "vbee"]
        # Không gọi được API voices vẫn phải có 1 giọng (từ env) để UI chọn.
        assert all(engine["voices"] for engine in payload["engines"])


def test_voices_catalog_lists_vbee_voices_from_api(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        main_module,
        "fetch_vbee_voices",
        lambda *a, **k: [
            {"id": "sg_female_x", "label": "SG - X", "desc": "female · vi-VN", "gender": "female"},
            {"id": "hn_male_y", "label": "HN - Y", "desc": "male · vi-VN", "gender": "male"},
        ],
    )
    with TestClient(app) as client:
        engines = client.get("/api/voices").json()["engines"]
        vbee = next(engine for engine in engines if engine["id"] == "vbee")
        ids = [voice["id"] for voice in vbee["voices"]]
        assert "sg_female_x" in ids and "hn_male_y" in ids
        # Giọng cấu hình trong env luôn có mặt để lựa chọn khớp cấu hình mặc định.
        assert settings.vbee_voice in ids


def test_patch_job_rejects_gemini_tts_engine():
    with TestClient(app) as client:
        response = client.patch("/api/jobs/demo", json={"tts_engine": "gemini"})
        assert response.status_code == 422


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
