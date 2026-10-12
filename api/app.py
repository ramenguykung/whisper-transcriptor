"""Local transcription API. SQLite and published artifacts belong to this service."""

import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import datetime, timezone
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

PROTOCOL_VERSION = 1
LEASE_SECONDS = 60
UPLOAD_BYTES = 2_000_000_000
DURATION_SECONDS = 7200
CPU_COMMAND = "docker compose up -d --build worker"
GPU_COMMAND = "docker compose -f compose.yaml -f compose.gpu.yaml up -d --build worker"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def error(code: str, message: str, action: str | None = None, details: Any = None) -> dict:
    return {"code": code, "message": message, "action": action, "details": details}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, action: str | None = None, details: Any = None):
        self.status = status
        self.body = error(code, message, action, details)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(Input):
    model: Literal["base", "small", "medium"] = "small"
    language: str | None = Field(default=None, min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$")
    device: Literal["cpu", "cuda"] = "cpu"


class NewJob(Input):
    filename: str = Field(min_length=1, max_length=240)
    settings: Settings = Field(default_factory=Settings)

    @model_validator(mode="after")
    def safe_filename(self):
        if any(ord(c) < 32 for c in self.filename) or "/" in self.filename or "\\" in self.filename:
            raise ValueError("Use a filename without path components or control characters")
        return self


class Retry(Input):
    device: Literal["cpu", "cuda"] | None = None


class Segment(Input):
    id: str = Field(min_length=1, max_length=128)
    start_ms: StrictInt = Field(ge=0)
    end_ms: StrictInt = Field(gt=0)
    text: str


class Transcript(Input):
    schema_version: Literal[1]
    revision: StrictInt = Field(ge=0)
    duration_ms: StrictInt = Field(gt=0, le=DURATION_SECONDS * 1000)
    language: str | None
    model: dict
    segments: list[Segment]

    @model_validator(mode="after")
    def valid_segments(self):
        validate_segments(self.segments, self.duration_ms)
        return self


def validate_segments(segments: list[Segment], duration_ms: int):
    seen = set()
    last_start = -1
    for segment in segments:
        if segment.id in seen or segment.start_ms < last_start:
            raise ValueError("Segments need unique IDs and must be ordered by start time")
        if not 0 <= segment.start_ms < segment.end_ms <= duration_ms:
            raise ValueError("Segment timing must stay inside the recording")
        seen.add(segment.id)
        last_start = segment.start_ms


class TranscriptSave(Input):
    expected_revision: StrictInt = Field(ge=0)
    segments: list[Segment]


class WorkerIdentity(Input):
    worker_id: str = Field(min_length=1, max_length=128)


class Register(WorkerIdentity):
    protocol_version: StrictInt
    capabilities: dict


class Attempt(WorkerIdentity):
    attempt_token: str = Field(min_length=1, max_length=128)


class Heartbeat(Attempt):
    phase: str = Field(min_length=1, max_length=80)
    progress: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class Complete(Attempt):
    transcript: Transcript
    preview_path: str
    waveform_path: str
    has_video: bool


class ErrorEnvelope(Input):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    action: str | None = Field(default=None, max_length=500)
    details: Any = None


class Failure(Attempt):
    error: ErrorEnvelope


class Store:
    def __init__(self, data_dir: Path, media_dir: Path):
        self.data_dir = data_dir.resolve()
        self.media_dir = media_dir.resolve()
        self.database = self.data_dir / "jobs.sqlite3"

    def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, status TEXT NOT NULL,
                    phase TEXT NOT NULL, progress REAL, settings TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    duration_ms INTEGER, has_video INTEGER, error TEXT,
                    source_path TEXT, preview_path TEXT, waveform_path TEXT,
                    transcript TEXT, worker_id TEXT, attempt_token TEXT,
                    lease_until REAL, lost_leases INTEGER NOT NULL DEFAULT 0,
                    upload_active INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY, protocol_version INTEGER NOT NULL,
                    capabilities TEXT NOT NULL, last_seen TEXT NOT NULL, last_seen_epoch REAL NOT NULL
                );
            """)
            interrupted = db.execute("SELECT id FROM jobs WHERE upload_active=1").fetchall()
            for row in interrupted:
                self.update(db, row["id"], status="failed", phase="Upload interrupted", upload_active=0,
                            error=json.dumps(error("UPLOAD_INTERRUPTED", "Upload interrupted", "Upload the file again")))
                for partial in self.job_dir(row["id"]).glob(".upload-*"):
                    partial.unlink(missing_ok=True)
            self.recover_leases(db)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except ApiError:
            # Lease recovery must survive a rejected stale request.
            db.commit()
            raise
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def job_dir(self, job_id: str) -> Path:
        # IDs used for paths originate in SQLite, never in a request path.
        return self.media_dir / job_id

    @staticmethod
    def row(db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ApiError(404, "JOB_NOT_FOUND", "Recording not found", "Choose another recording")
        return row

    @staticmethod
    def update(db, job_id, **values):
        values["updated_at"] = utc_now()
        columns = ", ".join(f"{name}=?" for name in values)
        db.execute(f"UPDATE jobs SET {columns} WHERE id=?", (*values.values(), job_id))

    @staticmethod
    def public(row) -> dict:
        keys = ["id", "filename", "status", "phase", "progress", "created_at", "updated_at", "duration_ms"]
        return {**{key: row[key] for key in keys}, "settings": json.loads(row["settings"]),
                "has_video": bool(row["has_video"]) if row["has_video"] is not None else None,
                "error": json.loads(row["error"]) if row["error"] else None}

    def recover_leases(self, db):
        rows = db.execute("SELECT * FROM jobs WHERE status IN ('processing','cancelling') AND lease_until<=?",
                          (time.time(),)).fetchall()
        for row in rows:
            if row["status"] == "cancelling":
                status, phase, issue = "cancelled", "Cancelled", None
            elif row["lost_leases"] == 0:
                status, phase, issue = "queued", "Queued", None
            else:
                status, phase = "failed", "Worker stopped"
                issue = json.dumps(error("WORKER_LOST", "Worker stopped responding", "Retry or restart the worker"))
            self.update(db, row["id"], status=status, phase=phase, progress=None, error=issue,
                        lost_leases=row["lost_leases"] + 1, worker_id=None, attempt_token=None, lease_until=None)

    def attempt(self, db, job_id: str, identity: Attempt):
        self.recover_leases(db)
        row = self.row(db, job_id)
        if (row["status"] not in ("processing", "cancelling") or row["attempt_token"] != identity.attempt_token
                or row["worker_id"] != identity.worker_id or (row["lease_until"] or 0) <= time.time()):
            raise ApiError(409, "STALE_ATTEMPT", "Attempt expired", "Stop this attempt")
        return row

    def artifact(self, row, path: str) -> Path:
        root = (self.job_dir(row["id"]) / "attempts" / row["attempt_token"]).resolve()
        resolved = Path(path).resolve()
        if (not root.is_relative_to(self.media_dir) or not resolved.is_relative_to(root)
                or not resolved.is_file()):
            raise ApiError(422, "INVALID_ARTIFACT", "Invalid worker output", "Retry", path)
        return resolved


def subtitle_time(milliseconds: int, separator: str) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def export_text(transcript: dict, format: str) -> str:
    if format == "json":
        return json.dumps(transcript, ensure_ascii=False, indent=2) + "\n"
    if format == "txt":
        return "\n".join(s["text"] for s in transcript["segments"]) + "\n"
    separator = "," if format == "srt" else "."
    blocks = []
    for index, segment in enumerate(transcript["segments"], 1):
        timing = f"{subtitle_time(segment['start_ms'], separator)} --> {subtitle_time(segment['end_ms'], separator)}"
        text = segment["text"]
        # Blank lines separate subtitle cues; keep the exact editable text in JSON/TXT.
        text = re.sub(r"\n[ \t]*\n", "\n\u200b\n", text.replace("\r\n", "\n").replace("\r", "\n"))
        blocks.append(f"{index}\n{timing}\n{text}")
    return ("WEBVTT\n\n" if format == "vtt" else "") + "\n\n".join(blocks) + "\n"


def create_app(data_dir: str | Path | None = None, media_dir: str | Path | None = None,
               worker_token: str | None = None, upload_bytes: int = UPLOAD_BYTES) -> FastAPI:
    store = Store(Path(data_dir or os.environ.get("API_DATA_DIR", "./data/db")),
                  Path(media_dir or os.environ.get("MEDIA_DIR", "./data/media")))
    token = worker_token if worker_token is not None else os.environ.get("WORKER_TOKEN", "")

    async def maintain():
        while True:
            await asyncio.sleep(5)
            with store.transaction() as db:
                store.recover_leases(db)

    @asynccontextmanager
    async def lifespan(application):
        if not token:
            raise RuntimeError("Set WORKER_TOKEN before starting the API")
        store.initialize()
        task = asyncio.create_task(maintain())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    application = FastAPI(title="Whisper Transcriptor", lifespan=lifespan)
    application.state.store = store

    @application.exception_handler(ApiError)
    async def api_error(_request, issue):
        return JSONResponse(status_code=issue.status, content=issue.body)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request, issue):
        details = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in issue.errors()]
        return JSONResponse(status_code=422, content=error("INVALID_INPUT", "Check the supplied values", "Fix the highlighted values", details))

    @application.exception_handler(StarletteHTTPException)
    async def http_error(_request, issue):
        return JSONResponse(status_code=issue.status_code, content=error("HTTP_ERROR", str(issue.detail)))

    @application.exception_handler(Exception)
    async def unexpected_error(_request, issue):
        logging.getLogger("uvicorn.error").error("API request failed", exc_info=issue)
        return JSONResponse(status_code=500, content=error("SERVER_ERROR", "Request failed", "Check API logs and retry"))

    def authorize(authorization: str | None = Header(default=None)):
        if authorization is None or not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            raise ApiError(401, "UNAUTHORIZED", "Worker authentication failed", "Check WORKER_TOKEN")

    internal = [Depends(authorize)]

    @application.get("/api/health")
    def health():
        return {"ok": True, "protocol_version": PROTOCOL_VERSION}

    @application.post("/api/jobs", status_code=201)
    def new_job(body: NewJob):
        job_id, now = str(uuid4()), utc_now()
        with store.transaction() as db:
            db.execute("INSERT INTO jobs(id,filename,status,phase,settings,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (job_id, body.filename, "uploading", "Upload", body.settings.model_dump_json(), now, now))
            return store.public(store.row(db, job_id))

    @application.get("/api/jobs")
    def list_jobs():
        with store.transaction() as db:
            store.recover_leases(db)
            return [store.public(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    @application.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        with store.transaction() as db:
            store.recover_leases(db)
            return store.public(store.row(db, job_id))

    @application.put("/api/jobs/{job_id}/source")
    async def upload(job_id: str, request: Request):
        length = request.headers.get("content-length")
        if length is not None:
            try:
                expected = int(length)
            except ValueError:
                raise ApiError(400, "INVALID_LENGTH", "Invalid upload size", "Upload again")
            if expected < 0 or expected > upload_bytes:
                raise ApiError(413, "UPLOAD_TOO_LARGE", "File exceeds 2 GB", "Choose a smaller file")
        else:
            expected = None
        with store.transaction() as db:
            row = store.row(db, job_id)
            if row["status"] != "uploading" or row["upload_active"]:
                raise ApiError(409, "UPLOAD_UNAVAILABLE", "Upload already started", "Create a new recording")
            store.update(db, job_id, upload_active=1)
            directory = store.job_dir(row["id"])
            suffix = Path(row["filename"]).suffix.lower()
        partial = directory / (".upload-" + str(uuid4()))
        source = directory / ("source" + suffix)
        size = 0
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with partial.open("xb") as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > upload_bytes:
                        raise ApiError(413, "UPLOAD_TOO_LARGE", "File exceeds 2 GB", "Choose a smaller file")
                    with store.transaction() as db:
                        if store.row(db, job_id)["status"] == "cancelling":
                            raise ApiError(409, "UPLOAD_CANCELLED", "Upload cancelled", "Upload again")
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if size == 0 or (expected is not None and size != expected):
                raise ApiError(400, "UPLOAD_INCOMPLETE", "Upload incomplete", "Upload again")
            with store.transaction() as db:
                if store.row(db, job_id)["status"] == "cancelling":
                    raise ApiError(409, "UPLOAD_CANCELLED", "Upload cancelled", "Upload again")
                partial.replace(source)
                store.update(db, job_id, status="queued", phase="Queued", progress=None,
                             source_path=str(source), upload_active=0)
                return store.public(store.row(db, job_id))
        except BaseException as issue:
            partial.unlink(missing_ok=True)
            with store.transaction() as db:
                row = store.row(db, job_id)
                cancelled = row["status"] == "cancelling"
                issue_body = issue.body if isinstance(issue, ApiError) else error("UPLOAD_INTERRUPTED", "Upload interrupted", "Upload again")
                store.update(db, job_id, upload_active=0, status="cancelled" if cancelled else "failed",
                             phase="Cancelled" if cancelled else "Upload failed", error=None if cancelled else json.dumps(issue_body))
            if isinstance(issue, ClientDisconnect):
                raise ApiError(400, "UPLOAD_INTERRUPTED", "Upload interrupted", "Upload again")
            raise

    @application.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        with store.transaction() as db:
            store.recover_leases(db)
            row = store.row(db, job_id)
            if row["status"] in ("processing", "cancelling") or row["upload_active"]:
                store.update(db, job_id, status="cancelling", phase="Stopping")
            elif row["status"] in ("queued", "uploading"):
                store.update(db, job_id, status="cancelled", phase="Cancelled", progress=None)
            return store.public(store.row(db, job_id))

    @application.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str, body: Retry | None = None):
        with store.transaction() as db:
            store.recover_leases(db)
            row = store.row(db, job_id)
            if row["status"] not in ("failed", "cancelled") or not row["source_path"] or not Path(row["source_path"]).is_file():
                raise ApiError(409, "RETRY_UNAVAILABLE", "Recording cannot be retried", "Upload the file again")
            settings = json.loads(row["settings"])
            if body and body.device:
                settings["device"] = body.device
            store.update(db, job_id, status="queued", phase="Queued", progress=None, error=None,
                         settings=json.dumps(settings), lost_leases=0)
            return store.public(store.row(db, job_id))

    @application.delete("/api/jobs/{job_id}", status_code=204)
    def delete(job_id: str):
        with store.transaction() as db:
            store.recover_leases(db)
            row = store.row(db, job_id)
            if row["status"] in ("processing", "cancelling") or row["upload_active"]:
                raise ApiError(409, "JOB_ACTIVE", "Recording is still active", "Cancel and wait for it to stop")
            directory = store.job_dir(row["id"]).resolve()
            if not directory.is_relative_to(store.media_dir) or directory == store.media_dir:
                raise ApiError(500, "INVALID_PATH", "Invalid recording path")
            if directory.exists():
                shutil.rmtree(directory)
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return Response(status_code=204)

    def ready_row(db, job_id):
        row = store.row(db, job_id)
        if row["status"] != "ready" or not row["transcript"]:
            raise ApiError(409, "NOT_READY", "Transcript is not ready", "Wait for transcription")
        return row

    @application.get("/api/jobs/{job_id}/transcript")
    def get_transcript(job_id: str):
        with store.transaction() as db:
            return json.loads(ready_row(db, job_id)["transcript"])

    @application.put("/api/jobs/{job_id}/transcript")
    def save_transcript(job_id: str, body: TranscriptSave):
        with store.transaction() as db:
            row = ready_row(db, job_id)
            transcript = json.loads(row["transcript"])
            if body.expected_revision != transcript["revision"]:
                raise ApiError(409, "REVISION_CONFLICT", "Transcript changed elsewhere", "Reload or download your draft",
                               {"revision": transcript["revision"]})
            try:
                validate_segments(body.segments, transcript["duration_ms"])
            except ValueError as issue:
                raise ApiError(422, "INVALID_TIMING", str(issue), "Fix segment timing")
            transcript["segments"] = [segment.model_dump() for segment in body.segments]
            transcript["revision"] += 1
            store.update(db, job_id, transcript=json.dumps(transcript, ensure_ascii=False))
            return transcript

    @application.get("/api/jobs/{job_id}/media")
    def media(job_id: str):
        with store.transaction() as db:
            row = ready_row(db, job_id)
            path = row["preview_path"]
        if not path or not Path(path).is_file():
            raise ApiError(404, "MEDIA_MISSING", "Playback file is missing", "Retry transcription")
        return FileResponse(path)

    @application.get("/api/jobs/{job_id}/waveform")
    def waveform(job_id: str):
        with store.transaction() as db:
            path = ready_row(db, job_id)["waveform_path"]
        if not path or not Path(path).is_file():
            raise ApiError(404, "WAVEFORM_MISSING", "Waveform is missing", "Retry transcription")
        return JSONResponse(json.loads(Path(path).read_text(encoding="utf-8")))

    @application.get("/api/jobs/{job_id}/export")
    def export(job_id: str, format: Literal["srt", "txt", "json", "vtt"] = "srt"):
        with store.transaction() as db:
            row = ready_row(db, job_id)
            transcript = json.loads(row["transcript"])
            filename = Path(row["filename"]).stem + "." + format
        media_types = {"srt": "application/x-subrip", "txt": "text/plain", "json": "application/json", "vtt": "text/vtt"}
        return Response(export_text(transcript, format), media_type=media_types[format],
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})

    @application.get("/api/system")
    def system():
        with store.transaction() as db:
            store.recover_leases(db)
            row = db.execute("SELECT * FROM workers ORDER BY last_seen_epoch DESC LIMIT 1").fetchone()
        worker = None if row is None else {"worker_id": row["worker_id"], "protocol_version": row["protocol_version"],
                                          "capabilities": json.loads(row["capabilities"]), "last_seen": row["last_seen"]}
        return {"protocol_version": PROTOCOL_VERSION, "worker": worker,
                "worker_online": row is not None and time.time() - row["last_seen_epoch"] < 15,
                "limits": {"upload_bytes": upload_bytes, "duration_seconds": DURATION_SECONDS},
                "cpu_command": CPU_COMMAND, "gpu_command": GPU_COMMAND}

    @application.post("/internal/v1/register", dependencies=internal)
    def register(body: Register):
        if body.protocol_version != PROTOCOL_VERSION:
            raise ApiError(409, "PROTOCOL_MISMATCH", "Worker version is incompatible", "Rebuild API and worker together",
                           {"expected": PROTOCOL_VERSION, "received": body.protocol_version})
        with store.transaction() as db:
            store.recover_leases(db)
            db.execute("INSERT INTO workers VALUES(?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET "
                       "protocol_version=excluded.protocol_version,capabilities=excluded.capabilities,"
                       "last_seen=excluded.last_seen,last_seen_epoch=excluded.last_seen_epoch",
                       (body.worker_id, body.protocol_version, json.dumps(body.capabilities), utc_now(), time.time()))
        return {"ok": True}

    @application.post("/internal/v1/claim", dependencies=internal)
    def claim(body: WorkerIdentity):
        with store.transaction() as db:
            store.recover_leases(db)
            worker = db.execute("SELECT * FROM workers WHERE worker_id=?", (body.worker_id,)).fetchone()
            if not worker or time.time() - worker["last_seen_epoch"] >= 15:
                return {"job": None}
            capabilities = json.loads(worker["capabilities"])
            ready = capabilities.get("ready", capabilities.get("readiness") in (True, "ready"))
            if ready is not True:
                return {"job": None}
            if db.execute("SELECT id FROM jobs WHERE status IN ('processing','cancelling') AND lease_until>?", (time.time(),)).fetchone():
                return {"job": None}
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return {"job": None}
            attempt_token = str(uuid4())
            attempt_dir = store.job_dir(row["id"]) / "attempts" / attempt_token
            attempt_dir.mkdir(parents=True, exist_ok=False)
            store.update(db, row["id"], status="processing", phase="Preparing", progress=None,
                         worker_id=body.worker_id, attempt_token=attempt_token, lease_until=time.time() + LEASE_SECONDS)
            return {"job": store.public(store.row(db, row["id"])), "attempt_token": attempt_token,
                    "source_path": row["source_path"], "attempt_dir": str(attempt_dir), "lease_seconds": LEASE_SECONDS}

    @application.post("/internal/v1/jobs/{job_id}/heartbeat", dependencies=internal)
    def heartbeat(job_id: str, body: Heartbeat):
        with store.transaction() as db:
            row = store.attempt(db, job_id, body)
            cancelled = row["status"] == "cancelling"
            store.update(db, job_id, phase="Stopping" if cancelled else body.phase, progress=body.progress,
                         lease_until=time.time() + LEASE_SECONDS)
            return {"cancel_requested": cancelled, "lease_seconds": LEASE_SECONDS}

    @application.post("/internal/v1/jobs/{job_id}/complete", dependencies=internal)
    def complete(job_id: str, body: Complete):
        with store.transaction() as db:
            row = store.attempt(db, job_id, body)
            if row["status"] == "cancelling":
                raise ApiError(409, "CANCEL_REQUESTED", "Recording was cancelled", "Stop and acknowledge cancellation")
            preview = store.artifact(row, body.preview_path)
            waveform_path = store.artifact(row, body.waveform_path)
            try:
                wave = json.loads(waveform_path.read_text(encoding="utf-8"))
                if (wave["duration_ms"] != body.transcript.duration_ms or not isinstance(wave["peaks"], list)
                        or any(type(value) not in (float, int) or not math.isfinite(value) or abs(value) > 1 for value in wave["peaks"])):
                    raise ValueError("Waveform does not match recording")
            except (ValueError, KeyError, TypeError) as issue:
                raise ApiError(422, "INVALID_WAVEFORM", "Invalid waveform", "Retry", str(issue))
            transcript = body.transcript.model_dump()
            transcript["revision"] = 0
            store.update(db, job_id, status="ready", phase="Ready", progress=1, error=None,
                         duration_ms=transcript["duration_ms"], has_video=int(body.has_video),
                         preview_path=str(preview), waveform_path=str(waveform_path),
                         transcript=json.dumps(transcript, ensure_ascii=False), worker_id=None, attempt_token=None, lease_until=None)
            return store.public(store.row(db, job_id))

    @application.post("/internal/v1/jobs/{job_id}/fail", dependencies=internal)
    def fail(job_id: str, body: Failure):
        with store.transaction() as db:
            row = store.attempt(db, job_id, body)
            if row["status"] == "cancelling":
                raise ApiError(409, "CANCEL_REQUESTED", "Recording was cancelled", "Stop and acknowledge cancellation")
            store.update(db, job_id, status="failed", phase="Failed", progress=None,
                         error=body.error.model_dump_json(), worker_id=None, attempt_token=None, lease_until=None)
            return store.public(store.row(db, job_id))

    @application.post("/internal/v1/jobs/{job_id}/cancelled", dependencies=internal)
    def cancelled(job_id: str, body: Attempt):
        with store.transaction() as db:
            store.attempt(db, job_id, body)
            store.update(db, job_id, status="cancelled", phase="Cancelled", progress=None, error=None,
                         worker_id=None, attempt_token=None, lease_until=None)
            return store.public(store.row(db, job_id))

    return application


app = create_app()
