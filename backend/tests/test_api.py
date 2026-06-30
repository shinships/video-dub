from fastapi.testclient import TestClient

from app.main import app


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
