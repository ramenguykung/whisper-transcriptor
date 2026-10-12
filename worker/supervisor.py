"""Small stdlib supervisor: leases continue while native code loads or runs."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from worker.child import MODELS, versions

REGISTER_SECONDS = 5
HEARTBEAT_SECONDS = 5
LEASE_STOP_MARGIN = 5


class APIError(Exception):
    def __init__(self, status, error):
        super().__init__(error.get("message", "API unavailable"))
        self.status = status
        self.error = error


class Client:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def post(self, path, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.url + "/internal/v1" + path, data=body, method="POST",
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        try:
            with urlopen(request, timeout=3) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                error = json.load(exc)
            except (ValueError, UnicodeError):
                error = {"code": "API_ERROR", "message": "API rejected the worker.", "details": str(exc)}
            raise APIError(exc.code, error) from exc
        except (URLError, TimeoutError, ConnectionError, OSError, ValueError) as exc:
            raise APIError(0, {"code": "API_OFFLINE", "message": "API is unavailable.",
                              "action": "Check the API container.", "details": str(exc)}) from exc


class Child:
    def __init__(self, payload=None, probe=False):
        self.events = queue.Queue()
        self.stop_lock = threading.Lock()
        self.log_path = (Path(payload["attempt_dir"]) / "worker.log") if payload else Path(tempfile.gettempdir()) / f"whisper-probe-{uuid.uuid4()}.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = self.log_path.open("wb")
        args = [sys.executable, "-u", "-m", "worker.child"]
        if probe:
            args.append("--probe")
        kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                            text=True, encoding="utf-8", **kwargs)
            if payload:
                self.process.stdin.write(json.dumps(payload, ensure_ascii=False))
            self.process.stdin.close()
        except Exception:
            self.log.close()
            raise
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        self.events.put(event)
                except ValueError:
                    pass
        finally:
            self.process.stdout.close()

    def finished(self):
        return self.process.poll() is not None and not self.reader.is_alive()

    def stop(self):
        with self.stop_lock:
            self._stop()

    def _stop(self):
        # Kill the whole session, including ffmpeg; publication waits until it has stopped.
        if os.name == "nt":
            if self.process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, check=False, timeout=1)
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1)
        self.reader.join(timeout=1)
        self.log.close()

    def diagnostic(self):
        self.log.flush() if not self.log.closed else None
        with self.log_path.open("rb") as stream:
            stream.seek(max(0, self.log_path.stat().st_size - 8000))
            return stream.read().decode("utf-8", errors="replace")

    def close(self, remove_log=False):
        self.log.close()
        if remove_log:
            self.log_path.unlink(missing_ok=True)


@dataclass
class Active:
    payload: dict
    child: Child
    deadline: float
    heartbeat_at: float
    phase: str = "Starting"
    progress: float | None = None
    result: dict | None = None
    cancelled: bool = False
    publish_at: float = 0
    lost: threading.Event = field(default_factory=threading.Event)


class Supervisor:
    def __init__(self, client, worker_id=None, clock=time.monotonic, child_factory=Child):
        self.client = client
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4()}"
        self.clock = clock
        self.child_factory = child_factory
        self.capabilities = {"ready": False, "readiness": "checking", "versions": versions(),
                             "devices": [], "supported_devices": [], "compute_types": {},
                             "supported_compute_types": {}, "cuda_supported": False,
                             "mode": os.getenv("WORKER_MODE", "real"), "last_diagnostic": None}
        self.active = None
        self.probe_child = None
        self.probe_deadline = 0
        self.probe_at = 0
        self.register_at = 0
        self.claim_at = 0
        self.registered = False
        self.guard_stopping = threading.Event()
        self.guard_thread = None

    def check_lease(self):
        active = self.active
        if active and not active.lost.is_set() and self.clock() >= active.deadline - LEASE_STOP_MARGIN:
            active.lost.set()
            active.child.stop()

    def start_lease_guard(self):
        def watch():
            while not self.guard_stopping.wait(0.1):
                self.check_lease()

        self.guard_thread = threading.Thread(target=watch, daemon=True)
        self.guard_thread.start()

    def _model_cache(self):
        cache_root = Path(os.getenv("MODEL_CACHE_DIR", "/data/models"))
        return {name: {**spec, "cached": (cache_root / ("models--" + spec["repository"].replace("/", "--")) /
                                         "snapshots" / spec["revision"] / "model.bin").is_file()}
                for name, spec in MODELS.items()}

    def register(self):
        self.capabilities["model_cache"] = self._model_cache()
        try:
            self.client.post("/register", {"worker_id": self.worker_id, "protocol_version": 1,
                                          "capabilities": self.capabilities})
            self.registered = True
        except APIError as exc:
            self.registered = False
            if exc.status in (401, 403, 409):
                self.capabilities["ready"] = False
                self.capabilities["readiness"] = "degraded"
                self.capabilities["last_diagnostic"] = exc.error
            print(json.dumps({"worker": "register", "error": exc.error}, ensure_ascii=False), file=sys.stderr, flush=True)
        self.register_at = self.clock() + REGISTER_SECONDS

    def probe(self):
        now = self.clock()
        if self.probe_child:
            result = None
            while not self.probe_child.events.empty():
                event = self.probe_child.events.get_nowait()
                if event.get("type") == "capabilities":
                    result = event["capabilities"]
            if result:
                self.capabilities = result
                self.probe_child.stop()
                self.probe_child.close(remove_log=True)
                self.probe_child = None
                self.probe_at = now + 30
                self.register_at = 0
            elif self.probe_child.finished() or now >= self.probe_deadline:
                self.probe_child.stop()
                detail = self.probe_child.diagnostic()
                self.capabilities.update(ready=False, readiness="degraded", last_diagnostic={
                    "code": "DEPENDENCY_ERROR", "message": "Worker could not load.",
                    "action": "Rebuild the worker.", "details": detail or "Capability check stopped or timed out."})
                self.probe_child.close(remove_log=True)
                self.probe_child = None
                self.probe_at = now + 30
                self.register_at = 0
        elif not self.active and now >= self.probe_at:
            try:
                self.probe_child = self.child_factory(probe=True)
                self.probe_deadline = now + 30
            except Exception as exc:
                self.capabilities.update(ready=False, readiness="degraded", last_diagnostic={
                    "code": "CHILD_START_ERROR", "message": "Worker could not start.",
                    "action": "Rebuild the worker.", "details": str(exc)})
                self.probe_at = now + 30

    def claim(self):
        if self.active or self.probe_child or not self.registered or not self.capabilities.get("ready"):
            return
        try:
            requested_at = self.clock()
            payload = self.client.post("/claim", {"worker_id": self.worker_id})
            if payload.get("job"):
                now = self.clock()
                deadline = requested_at + payload.get("lease_seconds", 60)
                if now >= deadline - LEASE_STOP_MARGIN:
                    return
                try:
                    child = self.child_factory(payload=payload)
                except Exception as exc:
                    self.client.post(f"/jobs/{payload['job']['id']}/fail", {"worker_id": self.worker_id,
                        "attempt_token": payload["attempt_token"], "error": {"code": "CHILD_START_ERROR",
                        "message": "Worker could not start.", "action": "Retry.", "details": str(exc)}})
                    return
                self.active = Active(payload=payload, child=child, deadline=deadline, heartbeat_at=now)
        except APIError:
            pass
        self.claim_at = self.clock() + 1

    def abandon(self):
        if self.active:
            self.active.lost.set()
            self.active.child.stop()
            self.active.child.close()
            self.active = None

    def heartbeat(self):
        active = self.active
        if active is None:
            return
        self.check_lease()
        if active.lost.is_set():
            self.abandon()
            return
        body = {"worker_id": self.worker_id, "attempt_token": active.payload["attempt_token"],
                "phase": active.phase, "progress": active.progress}
        try:
            requested_at = self.clock()
            response = self.client.post(f"/jobs/{active.payload['job']['id']}/heartbeat", body)
            if active.lost.is_set():
                self.abandon()
                return
            active.deadline = requested_at + response.get("lease_seconds", 60)
            if response.get("cancel_requested"):
                active.child.stop()
                active.cancelled = True
        except APIError as exc:
            if exc.status in (401, 403, 404, 409):
                self.abandon()
                return
        active.heartbeat_at = self.clock() + HEARTBEAT_SECONDS

    def collect(self):
        active = self.active
        if active is None:
            return
        self.check_lease()
        if active.lost.is_set():
            self.abandon()
            return
        while not active.child.events.empty():
            event = active.child.events.get_nowait()
            if event.get("type") == "progress":
                active.phase = str(event.get("phase", "Working"))
                active.progress = event.get("progress")
            elif event.get("type") in ("complete", "error"):
                active.result = event
        if not active.child.finished() and not active.cancelled:
            return
        if self.clock() < active.publish_at:
            return
        # A completion that races with Cancel must observe cancellation before publication.
        if not active.cancelled:
            self.heartbeat()
            if self.active is None:
                return
        body = {"worker_id": self.worker_id, "attempt_token": active.payload["attempt_token"]}
        if active.cancelled:
            endpoint = "cancelled"
        elif active.result and active.result["type"] == "complete" and active.child.process.returncode == 0:
            endpoint = "complete"
            body.update({key: value for key, value in active.result.items() if key != "type"})
        else:
            endpoint = "fail"
            body["error"] = active.result.get("error") if active.result and active.result["type"] == "error" else {
                "code": "CHILD_EXIT", "message": "Worker stopped.", "action": "Use CPU or retry.",
                "details": active.child.diagnostic() or f"Child exit: {active.child.process.returncode}"}
            self.capabilities["last_diagnostic"] = body["error"]
        try:
            self.client.post(f"/jobs/{active.payload['job']['id']}/{endpoint}", body)
        except APIError as exc:
            active.publish_at = self.clock() + 5
            if exc.status in (401, 403, 404, 409):
                self.abandon()
            return
        active.child.close()
        self.active = None
        self.register_at = 0

    def tick(self):
        now = self.clock()
        self.check_lease()
        if self.active and self.active.lost.is_set():
            self.abandon()
        if self.active and now >= self.active.heartbeat_at:
            self.heartbeat()
        self.collect()
        self.probe()
        if self.clock() >= self.register_at:
            self.register()
        if self.clock() >= self.claim_at:
            self.claim()

    def stop(self):
        self.guard_stopping.set()
        if self.guard_thread:
            self.guard_thread.join(timeout=6)
        self.abandon()
        if self.probe_child:
            self.probe_child.stop()
            self.probe_child.close(remove_log=True)


def main():
    token = os.environ.get("WORKER_TOKEN")
    if not token:
        raise SystemExit("WORKER_TOKEN is required.")
    supervisor = Supervisor(Client(os.getenv("API_URL", "http://api:8000"), token))
    supervisor.start_lease_guard()
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    try:
        while not stopping.is_set():
            supervisor.tick()
            stopping.wait(0.2)
    finally:
        supervisor.stop()


if __name__ == "__main__":
    main()
