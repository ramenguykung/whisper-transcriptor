import queue
import threading
import time
from types import SimpleNamespace

from worker.supervisor import APIError, Active, Supervisor


class FakeChild:
    def __init__(self, **_):
        self.events = queue.Queue()
        self.process = SimpleNamespace(returncode=None)
        self.stopped = False
        self.closed = False

    def finished(self):
        return self.process.returncode is not None

    def stop(self):
        self.stopped = True
        self.process.returncode = -9

    def close(self, **_):
        self.closed = True

    def diagnostic(self):
        return "Native loader failed."


class FakeClient:
    def __init__(self):
        self.calls = []
        self.cancel = False
        self.stale = False
        self.offline = False

    def post(self, path, body):
        self.calls.append((path, body))
        if self.offline:
            raise APIError(0, {"code": "API_OFFLINE", "message": "Offline"})
        if path.endswith("/heartbeat"):
            if self.stale:
                raise APIError(409, {"code": "STALE_ATTEMPT", "message": "Expired"})
            return {"cancel_requested": self.cancel, "lease_seconds": 60}
        if path == "/claim":
            return {"job": None}
        return {"ok": True}


def running():
    now = [0.0]
    client = FakeClient()
    child = FakeChild()
    supervisor = Supervisor(client, worker_id="test", clock=lambda: now[0], child_factory=FakeChild)
    supervisor.capabilities["ready"] = True
    supervisor.probe_at = 999
    supervisor.active = Active({"job": {"id": "job"}, "attempt_token": "attempt"}, child, 60, 0)
    return supervisor, client, child, now


def test_register_and_lease_continue_while_inference_blocks():
    supervisor, client, child, now = running()
    for second in (0, 5, 10, 15):
        now[0] = second
        supervisor.tick()
    assert len([path for path, _ in client.calls if path == "/register"]) == 4
    assert len([path for path, _ in client.calls if path.endswith("/heartbeat")]) == 4
    assert not child.stopped
    assert supervisor.active.deadline == 75


def test_cancel_kills_child_before_acknowledgement_even_if_complete_races():
    supervisor, client, child, _ = running()
    child.events.put({"type": "complete", "transcript": {}, "preview_path": "p"})
    client.cancel = True
    supervisor.tick()
    assert child.stopped and child.closed
    assert supervisor.active is None
    assert any(path.endswith("/cancelled") for path, _ in client.calls)
    assert not any(path.endswith("/complete") for path, _ in client.calls)


def test_stale_attempt_stops_without_publishing():
    supervisor, client, child, _ = running()
    client.stale = True
    supervisor.tick()
    assert child.stopped and supervisor.active is None
    assert not any(path.endswith(("/complete", "/fail", "/cancelled")) for path, _ in client.calls)


def test_offline_api_stops_at_lease_deadline():
    supervisor, client, child, now = running()
    client.offline = True
    now[0] = 54
    supervisor.tick()
    assert not child.stopped
    now[0] = 55
    supervisor.tick()
    assert child.stopped and supervisor.active is None


def test_native_crash_reports_diagnostic_and_keeps_supervisor_alive():
    supervisor, client, child, _ = running()
    child.process.returncode = 139
    supervisor.tick()
    error = next(body["error"] for path, body in client.calls if path.endswith("/fail"))
    assert error["code"] == "CHILD_EXIT" and error["details"] == "Native loader failed."
    assert supervisor.active is None and child.closed


def test_degraded_worker_does_not_claim():
    supervisor = Supervisor(FakeClient(), child_factory=FakeChild)
    supervisor.registered = True
    supervisor.claim()
    assert not supervisor.client.calls


def test_probe_crash_registers_degraded_health():
    supervisor = Supervisor(FakeClient(), clock=lambda: 31, child_factory=FakeChild)
    supervisor.probe_child = FakeChild()
    supervisor.probe_deadline = 30
    supervisor.tick()
    assert supervisor.capabilities["ready"] is False
    assert supervisor.capabilities["last_diagnostic"]["code"] == "DEPENDENCY_ERROR"
    assert any(path == "/register" for path, _ in supervisor.client.calls)


def test_failed_publication_retries_without_flooding_api():
    supervisor, client, child, now = running()
    child.process.returncode = 1
    original = client.post

    def fail_publication(path, body):
        if path.endswith("/fail"):
            client.calls.append((path, body))
            raise APIError(0, {"code": "API_OFFLINE", "message": "Offline"})
        return original(path, body)

    client.post = fail_publication
    supervisor.tick()
    now[0] = 1
    supervisor.tick()
    assert len([path for path, _ in client.calls if path.endswith("/fail")]) == 1
    now[0] = 5
    supervisor.tick()
    assert len([path for path, _ in client.calls if path.endswith("/fail")]) == 2


def test_delayed_heartbeat_uses_request_start_for_deadline():
    supervisor, client, _, now = running()
    original = client.post

    def delayed_response(path, body):
        response = original(path, body)
        if path.endswith("/heartbeat"):
            now[0] += 2
        return response

    client.post = delayed_response
    supervisor.heartbeat()
    assert supervisor.active.deadline == 60


def test_delayed_claim_uses_request_start_for_deadline():
    now = [0.0]

    class DelayedClaim(FakeClient):
        def post(self, path, body):
            now[0] += 2
            return {"job": {"id": "job"}, "attempt_token": "a", "attempt_dir": "unused", "lease_seconds": 60}

    supervisor = Supervisor(DelayedClaim(), clock=lambda: now[0], child_factory=FakeChild)
    supervisor.capabilities["ready"] = True
    supervisor.registered = True
    supervisor.claim()
    assert supervisor.active.deadline == 60


def test_lease_watchdog_stops_child_during_blocked_register():
    supervisor, client, child, now = running()
    entered = threading.Event()
    release = threading.Event()
    original = client.post

    def blocked_register(path, body):
        if path == "/register":
            entered.set()
            release.wait(timeout=2)
        return original(path, body)

    client.post = blocked_register
    supervisor.start_lease_guard()
    registration = threading.Thread(target=supervisor.register)
    registration.start()
    assert entered.wait(timeout=1)
    now[0] = 55
    until = time.monotonic() + 1
    while not child.stopped and time.monotonic() < until:
        time.sleep(0.01)
    assert child.stopped and supervisor.active.lost.is_set()
    release.set()
    registration.join(timeout=2)
    supervisor.stop()
