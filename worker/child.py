"""Disposable process containing all native libraries and inference."""

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import uuid

from worker.media import WorkerError, prepare

MODELS = json.loads(Path(__file__).with_name("models.json").read_text(encoding="utf-8"))
EXPECTED = {"faster-whisper": "1.2.1", "ctranslate2": "4.8.2"}


def emit(event):
    print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)


def versions():
    result = {"python": sys.version.split()[0]}
    for package in ("faster-whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "huggingface-hub", "numpy"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def probe():
    result = {"versions": versions(), "ready": False, "readiness": "degraded", "devices": [],
              "compute_types": {}, "supported_compute_types": {}, "cuda_supported": False,
              "mode": os.getenv("WORKER_MODE", "real"), "last_diagnostic": None}
    try:
        for name in (os.getenv("FFMPEG", "ffmpeg"), os.getenv("FFPROBE", "ffprobe")):
            if shutil.which(name) is None:
                raise WorkerError("MEDIA_TOOL_MISSING", "Media tools are missing.", "Rebuild the worker.", name)
        if result["mode"] == "fixture":
            result.update(ready=True, readiness="ready", devices=["cpu"], compute_types={"cpu": ["int8"]})
        else:
            mismatch = {name: {"expected": expected, "actual": result["versions"].get(name)}
                        for name, expected in EXPECTED.items() if result["versions"].get(name) != expected}
            if mismatch:
                raise WorkerError("DEPENDENCY_MISMATCH", "Worker versions differ.", "Rebuild the worker.", mismatch)
            import ctranslate2
            import faster_whisper  # Import here so ABI failures cannot crash the supervisor.

            cpu_types = sorted(ctranslate2.get_supported_compute_types("cpu"))
            if "int8" not in cpu_types:
                raise WorkerError("CPU_UNSUPPORTED", "CPU inference is unavailable.", "Rebuild the worker.", cpu_types)
            result.update(ready=True, readiness="ready", devices=["cpu"], compute_types={"cpu": cpu_types})
            try:
                if ctranslate2.get_cuda_device_count() > 0:
                    gpu_types = sorted(ctranslate2.get_supported_compute_types("cuda", device_index=0))
                    result["compute_types"]["cuda"] = gpu_types
                    if "int8_float32" in gpu_types:
                        result["devices"].append("cuda")
                        result["cuda_supported"] = True
                    else:
                        result["last_diagnostic"] = {"code": "GPU_UNSUPPORTED", "message": "GPU compute type is unavailable.",
                            "action": "Use CPU.", "details": gpu_types}
            except Exception as exc:
                result["last_diagnostic"] = {"code": "GPU_UNAVAILABLE", "message": "GPU is unavailable.",
                    "action": "Use CPU.", "details": str(exc)}
    except WorkerError as exc:
        result["last_diagnostic"] = exc.error
    except Exception:
        result["last_diagnostic"] = {"code": "DEPENDENCY_ERROR", "message": "Worker could not load.",
            "action": "Rebuild the worker.", "details": traceback.format_exc()[-8000:]}
    result["supported_devices"] = result["devices"]
    result["supported_compute_types"] = result["compute_types"]
    return result


def work(payload):
    source = Path(payload["source_path"]).resolve()
    attempt_dir = Path(payload["attempt_dir"]).resolve()
    media_dir = Path(os.getenv("MEDIA_DIR", "/data/media")).resolve()
    if not source.is_relative_to(media_dir) or not attempt_dir.is_relative_to(media_dir) or source.is_relative_to(attempt_dir):
        raise WorkerError("PATH_INVALID", "Worker paths are invalid.", "Check shared media settings.")
    attempt_dir.mkdir(parents=True, exist_ok=True)
    prepared = prepare(source, attempt_dir, emit)
    settings = payload["job"]["settings"]
    model_name = settings["model"]
    if model_name not in MODELS or settings["device"] not in ("cpu", "cuda"):
        raise WorkerError("SETTINGS_INVALID", "Transcription settings are invalid.", "Choose another setting.")
    model_spec = MODELS[model_name]
    compute_type = "int8" if settings["device"] == "cpu" else "int8_float32"
    provenance = {"name": model_name, **model_spec, "requested_device": settings["device"],
                  "requested_compute_type": compute_type, "device": None, "compute_type": None, "versions": versions()}
    if os.getenv("WORKER_MODE") == "fixture":
        emit({"type": "progress", "phase": "Transcribing", "progress": None})
        time.sleep(float(os.getenv("FIXTURE_DELAY_SECONDS", "0")))
        provenance["fixture"] = True
        provenance.update(device=settings["device"], compute_type=compute_type)
        segments = [{"id": str(uuid.uuid4()), "start_ms": 0, "end_ms": prepared["duration_ms"],
                     "text": "Local transcript. สวัสดี"}]
        language = settings.get("language") or "en"
    else:
        emit({"type": "progress", "phase": "Loading model", "progress": None})
        try:
            import ctranslate2
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise WorkerError("DEPENDENCY_ERROR", "Worker could not load.", "Rebuild the worker.", str(exc)) from exc
        if compute_type not in ctranslate2.get_supported_compute_types(settings["device"], device_index=0):
            raise WorkerError("GPU_UNSUPPORTED" if settings["device"] == "cuda" else "CPU_UNSUPPORTED",
                              "Compute type is unavailable.", "Use CPU." if settings["device"] == "cuda" else "Rebuild the worker.")
        try:
            model = WhisperModel(model_spec["repository"], revision=model_spec["revision"],
                                 device=settings["device"], compute_type=compute_type,
                                 download_root=os.getenv("MODEL_CACHE_DIR", "/data/models"))
        except Exception as exc:
            code = "GPU_ERROR" if settings["device"] == "cuda" else "MODEL_ERROR"
            action = "Use CPU." if settings["device"] == "cuda" else "Check model download access and retry."
            raise WorkerError(code, "Model could not load.", action, str(exc)) from exc
        provenance["device"] = getattr(model.model, "device", None)
        provenance["compute_type"] = getattr(model.model, "compute_type", None)
        emit({"type": "progress", "phase": "Transcribing", "progress": None})
        raw_segments, info = model.transcribe(prepared["audio_path"], language=settings.get("language"),
                                            beam_size=5, vad_filter=True)
        language = info.language
        segments = []
        for segment in raw_segments:
            start = max(0, min(prepared["duration_ms"] - 1, round(segment.start * 1000)))
            end = min(prepared["duration_ms"], max(start + 1, round(segment.end * 1000)))
            segments.append({"id": str(uuid.uuid4()), "start_ms": start, "end_ms": end, "text": segment.text})
            emit({"type": "progress", "phase": "Transcribing", "progress": min(0.99, end / prepared["duration_ms"])})
        segments.sort(key=lambda segment: segment["start_ms"])
    transcript = {"schema_version": 1, "revision": 0, "duration_ms": prepared["duration_ms"],
                  "language": language, "model": provenance, "segments": segments}
    emit({"type": "complete", "transcript": transcript, "preview_path": prepared["preview_path"],
          "waveform_path": prepared["waveform_path"], "has_video": prepared["has_video"]})


def main():
    if "--probe" in sys.argv:
        emit({"type": "capabilities", "capabilities": probe()})
        return
    payload = {}
    try:
        payload = json.load(sys.stdin)
        work(payload)
    except WorkerError as exc:
        emit({"type": "error", "error": exc.error})
        sys.exit(1)
    except Exception:
        requested_device = locals().get("payload", {}).get("job", {}).get("settings", {}).get("device")
        emit({"type": "error", "error": {"code": "TRANSCRIPTION_ERROR", "message": "Transcription failed.",
              "action": "Use CPU or retry." if requested_device == "cuda" else "Retry. Open Details if it fails again.",
              "details": traceback.format_exc()[-8000:]}})
        sys.exit(1)


if __name__ == "__main__":
    main()
