import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app

AUTH = {"Authorization": "Bearer test-secret"}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "db", tmp_path / "media", worker_token="test-secret", upload_bytes=32)
    with TestClient(app) as client:
        yield client


def new_job(client, filename="เสียง.wav", device="cpu"):
    response = client.post("/api/jobs", json={"filename": filename, "settings": {"device": device}})
    assert response.status_code == 201, response.text
    return response.json()


def queued_job(client, filename="เสียง.wav", device="cpu"):
    job = new_job(client, filename, device)
    response = client.put(f"/api/jobs/{job['id']}/source", content=b"recording")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    return response.json()


def register(client, ready=True, worker_id="worker"):
    response = client.post("/internal/v1/register", headers=AUTH,
                           json={"worker_id": worker_id, "protocol_version": 1, "capabilities": {"ready": ready}})
    assert response.status_code == 200, response.text


def claim(client, worker_id="worker"):
    response = client.post("/internal/v1/claim", headers=AUTH, json={"worker_id": worker_id})
    assert response.status_code == 200, response.text
    return response.json()


def identity(attempt):
    return {"worker_id": "worker", "attempt_token": attempt["attempt_token"]}


def completion(attempt):
    directory = Path(attempt["attempt_dir"])
    preview = directory / "preview.wav"
    preview.write_bytes(b"0123456789")
    waveform = directory / "waveform.json"
    waveform.write_text(json.dumps({"duration_ms": 5000, "peaks": [0, -0.5, 1]}), encoding="utf-8")
    return {**identity(attempt), "preview_path": str(preview), "waveform_path": str(waveform), "has_video": False,
            "transcript": {"schema_version": 1, "revision": 0, "duration_ms": 5000, "language": "th",
                           "model": {"name": "small", "revision": "pinned"},
                           "segments": [{"id": "one", "start_ms": 10, "end_ms": 3000, "text": "สวัสดี, 世界!\nSecond line."}]}}


def ready_job(client):
    job = queued_job(client)
    register(client)
    attempt = claim(client)
    response = client.post(f"/internal/v1/jobs/{job['id']}/complete", headers=AUTH, json=completion(attempt))
    assert response.status_code == 200, response.text
    return response.json(), attempt


def expire(client, job_id):
    with client.app.state.store.transaction() as db:
        db.execute("UPDATE jobs SET lease_until=? WHERE id=?", (time.time() - 1, job_id))


def test_api_runs_without_worker(client):
    assert client.get("/api/health").json()["ok"]
    system = client.get("/api/system").json()
    assert system["worker"] is None
    assert not system["worker_online"]
    assert system["limits"]["duration_seconds"] == 7200
    assert client.get("/api/jobs").json() == []


def test_upload_bounds_without_content_length_and_cleanup(client):
    job = new_job(client)
    response = client.put(f"/api/jobs/{job['id']}/source", content=iter([b"a" * 20, b"b" * 20]))
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json()["code"] == "UPLOAD_TOO_LARGE"
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"
    assert not list(client.app.state.store.job_dir(job["id"]).glob(".upload-*"))
    assert not list(client.app.state.store.job_dir(job["id"]).glob("source*"))


def test_upload_bounds_content_length_empty_and_duplicate(client):
    job = new_job(client)
    assert client.put(f"/api/jobs/{job['id']}/source", content=b"a" * 33).status_code == 413
    assert client.put(f"/api/jobs/{job['id']}/source", content=b"").status_code == 400
    job = queued_job(client)
    assert client.put(f"/api/jobs/{job['id']}/source", content=b"again").status_code == 409
    bad = client.post("/api/jobs", json={"filename": "../escape.wav"})
    assert bad.status_code == 422
    assert bad.json()["code"] == "INVALID_INPUT"


def test_protocol_auth_and_degraded_worker(client):
    queued_job(client)
    assert client.post("/internal/v1/claim", json={"worker_id": "worker"}).status_code == 401
    mismatch = client.post("/internal/v1/register", headers=AUTH,
                           json={"worker_id": "worker", "protocol_version": 2, "capabilities": {"ready": True}})
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "PROTOCOL_MISMATCH"
    register(client, ready=False)
    assert claim(client)["job"] is None
    register(client)
    assert client.get("/api/system").json()["worker_online"]


def test_only_one_claim_and_lease_recovery_once(client):
    job = queued_job(client)
    queued_job(client, filename="other.wav")
    register(client)
    first = claim(client)
    assert first["job"]["id"] == job["id"]
    assert first["lease_seconds"] == 60
    assert claim(client)["job"] is None
    expire(client, job["id"])
    second = claim(client)
    assert second["job"]["id"] == job["id"]
    assert first["attempt_token"] != second["attempt_token"]
    expired = client.post(f"/internal/v1/jobs/{job['id']}/heartbeat", headers=AUTH,
                          json={**identity(first), "phase": "Transcribing", "progress": 0.5})
    assert expired.status_code == 409
    assert expired.json()["code"] == "STALE_ATTEMPT"
    late = client.post(f"/internal/v1/jobs/{job['id']}/complete", headers=AUTH, json=completion(first))
    assert late.status_code == 409
    expire(client, job["id"])
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"


def test_restart_recovery_and_partial_upload_cleanup(client):
    job = queued_job(client)
    register(client)
    claim(client)
    expire(client, job["id"])
    partial_job = new_job(client)
    store = client.app.state.store
    partial_dir = store.job_dir(partial_job["id"])
    partial_dir.mkdir()
    (partial_dir / ".upload-dead").write_bytes(b"partial")
    with store.transaction() as db:
        db.execute("UPDATE jobs SET upload_active=1 WHERE id=?", (partial_job["id"],))
    store.initialize()
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "queued"
    assert client.get(f"/api/jobs/{partial_job['id']}").json()["error"]["code"] == "UPLOAD_INTERRUPTED"
    assert not (partial_dir / ".upload-dead").exists()


def test_cancel_acknowledgement_delete_and_retry_cpu(client):
    job = queued_job(client, device="cuda")
    register(client)
    attempt = claim(client)
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelling"
    heartbeat = client.post(f"/internal/v1/jobs/{job['id']}/heartbeat", headers=AUTH,
                            json={**identity(attempt), "phase": "Transcribing", "progress": None})
    assert heartbeat.json()["cancel_requested"]
    assert client.post(f"/internal/v1/jobs/{job['id']}/complete", headers=AUTH, json=completion(attempt)).status_code == 409
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 409
    assert client.post(f"/internal/v1/jobs/{job['id']}/cancelled", headers=AUTH, json=identity(attempt)).json()["status"] == "cancelled"
    retry = client.post(f"/api/jobs/{job['id']}/retry", json={"device": "cpu"})
    assert retry.json()["settings"]["device"] == "cpu"
    assert retry.json()["status"] == "queued"
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert not client.app.state.store.job_dir(job["id"]).exists()
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_deterministic_failure_requires_retry(client):
    job = queued_job(client)
    register(client)
    attempt = claim(client)
    response = client.post(f"/internal/v1/jobs/{job['id']}/fail", headers=AUTH,
                           json={**identity(attempt), "error": {"code": "GPU_UNAVAILABLE", "message": "GPU unavailable",
                                                               "action": "Use CPU", "details": "driver"}})
    assert response.json()["status"] == "failed"
    assert claim(client)["job"] is None
    assert client.post(f"/api/jobs/{job['id']}/retry", json={"device": "cpu"}).json()["status"] == "queued"


def test_conflict_export_unicode_and_timing(client):
    job, _ = ready_job(client)
    path = f"/api/jobs/{job['id']}/transcript"
    transcript = client.get(path).json()
    segments = transcript["segments"]
    segments[0]["text"] = "แก้ไข, 世界!\nSecond line."
    saved = client.put(path, json={"expected_revision": 0, "segments": segments})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert saved.json()["segments"][0]["id"] == "one"
    conflict = client.put(path, json={"expected_revision": 0, "segments": segments})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "REVISION_CONFLICT"
    assert client.get(path).json() == saved.json()
    for format in ("srt", "txt", "json", "vtt"):
        output = client.get(f"/api/jobs/{job['id']}/export?format={format}")
        assert output.status_code == 200
        assert "แก้ไข, 世界!" in output.text
        assert "attachment;" in output.headers["content-disposition"]
    assert "00:00:00,010 --> 00:00:03,000" in client.get(f"/api/jobs/{job['id']}/export?format=srt").text
    assert client.get(f"/api/jobs/{job['id']}/export?format=vtt").text.startswith("WEBVTT\n")
    segments[0]["end_ms"] = 6000
    invalid = client.put(path, json={"expected_revision": 1, "segments": segments})
    assert invalid.status_code == 422
    assert client.get(path).json()["revision"] == 1
    segments[0]["end_ms"] = 1.5
    assert client.put(path, json={"expected_revision": 1, "segments": segments}).status_code == 422


def test_overlap_allowed_duplicate_and_order_rejected(client):
    job, _ = ready_job(client)
    path = f"/api/jobs/{job['id']}/transcript"
    segments = [{"id": "a", "start_ms": 0, "end_ms": 2500, "text": "A"},
                {"id": "b", "start_ms": 2000, "end_ms": 5000, "text": "B"}]
    assert client.put(path, json={"expected_revision": 0, "segments": segments}).status_code == 200
    assert client.put(path, json={"expected_revision": 1, "segments": segments[::-1]}).status_code == 422
    segments[1]["id"] = "a"
    assert client.put(path, json={"expected_revision": 1, "segments": segments}).status_code == 422


def test_range_and_waveform(client):
    job, _ = ready_job(client)
    path = f"/api/jobs/{job['id']}/media"
    response = client.get(path, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert client.get(path, headers={"Range": "bytes=-3"}).content == b"789"
    assert client.get(path, headers={"Range": "bytes=10-20"}).status_code == 416
    assert client.get(f"/api/jobs/{job['id']}/waveform").json() == {"duration_ms": 5000, "peaks": [0, -0.5, 1]}


def test_worker_artifacts_must_stay_inside_attempt(client, tmp_path):
    job = queued_job(client)
    register(client)
    attempt = claim(client)
    body = completion(attempt)
    outside = tmp_path / "private.wav"
    outside.write_bytes(b"private")
    body["preview_path"] = str(outside)
    invalid = client.post(f"/internal/v1/jobs/{job['id']}/complete", headers=AUTH, json=body)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_ARTIFACT"
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "processing"
    body = completion(attempt)
    Path(body["waveform_path"]).write_text('{"duration_ms":5000,"peaks":[2]}', encoding="utf-8")
    assert client.post(f"/internal/v1/jobs/{job['id']}/complete", headers=AUTH, json=body).status_code == 422


def test_silence_and_overlong_transcript(client):
    job = queued_job(client)
    register(client)
    attempt = claim(client)
    body = completion(attempt)
    body["transcript"]["duration_ms"] = 7_200_001
    assert client.post(f"/internal/v1/jobs/{job['id']}/complete", headers=AUTH, json=body).status_code == 422
    body = completion(attempt)
    body["transcript"]["segments"] = []
    assert client.post(f"/internal/v1/jobs/{job['id']}/complete", headers=AUTH, json=body).json()["status"] == "ready"
    assert client.get(f"/api/jobs/{job['id']}/transcript").json()["segments"] == []


def test_concurrent_claims_and_saves_have_one_winner(client):
    queued_job(client)
    queued_job(client, filename="second.wav")
    register(client)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: claim(client), range(2)))
    assert sum(result["job"] is not None for result in claims) == 1
    attempt = next(result for result in claims if result["job"] is not None)
    job_id = attempt["job"]["id"]
    assert client.post(f"/internal/v1/jobs/{job_id}/complete", headers=AUTH, json=completion(attempt)).status_code == 200
    save = {"expected_revision": 0, "segments": [{"id": "one", "start_ms": 0, "end_ms": 5000, "text": "Edit"}]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.put(f"/api/jobs/{job_id}/transcript", json=save), range(2)))
    assert sorted(result.status_code for result in responses) == [200, 409]
    assert client.get(f"/api/jobs/{job_id}/transcript").json()["revision"] == 1
