import sys
from types import SimpleNamespace

from worker.child import EXPECTED, MODELS, probe, work


def test_models_use_immutable_revisions():
    assert set(MODELS) == {"base", "small", "medium"}
    assert all(len(model["revision"]) == 40 for model in MODELS.values())


def test_version_mismatch_is_actionable_and_not_ready(monkeypatch):
    monkeypatch.setattr("worker.child.versions", lambda: {"faster-whisper": "0.0", "ctranslate2": "4.8.2"})
    result = probe()
    assert not result["ready"]
    assert result["last_diagnostic"]["code"] == "DEPENDENCY_MISMATCH"
    assert result["last_diagnostic"]["details"]["faster-whisper"]["expected"] == "1.2.1"


def test_gpu_failure_preserves_cpu_capability(monkeypatch):
    monkeypatch.setattr("worker.child.versions", lambda: EXPECTED.copy())

    def gpu_unavailable():
        raise RuntimeError("CUDA driver too old")

    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(
        get_supported_compute_types=lambda *_: {"int8"}, get_cuda_device_count=gpu_unavailable))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace())
    result = probe()
    assert result["ready"] and result["supported_devices"] == ["cpu"]
    assert result["last_diagnostic"]["code"] == "GPU_UNAVAILABLE"


def test_gtx_compute_type_is_checked_before_gpu_advertisement(monkeypatch):
    monkeypatch.setattr("worker.child.versions", lambda: EXPECTED.copy())
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(
        get_supported_compute_types=lambda *_, **__: {"int8", "int8_float32"}, get_cuda_device_count=lambda: 1))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace())
    result = probe()
    assert result["ready"] and result["cuda_supported"]
    assert "int8_float32" in result["compute_types"]["cuda"]


def test_explicit_fixture_mode_is_visible(monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "fixture")
    result = probe()
    assert result["ready"] and result["mode"] == "fixture"


def test_inference_preserves_unicode_and_reports_actual_compute_type(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("WORKER_MODE", raising=False)
    events = []
    calls = []
    monkeypatch.setattr("worker.child.emit", events.append)
    monkeypatch.setattr("worker.child.prepare", lambda *_: {"audio_path": "audio.wav", "preview_path": "preview.mp3",
                         "waveform_path": "waveform.json", "duration_ms": 1000, "has_video": False})

    class Model:
        model = SimpleNamespace(device="cpu", compute_type="int8_float32")

        def __init__(self, repository, **kwargs):
            calls.append((repository, kwargs))

        def transcribe(self, _audio, **kwargs):
            calls.append(kwargs)
            return iter([SimpleNamespace(start=0.0, end=1.02, text=" สวัสดี, café.")]), SimpleNamespace(language="th")

    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_supported_compute_types=lambda *_, **__: {"int8"}))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    work({"source_path": str(tmp_path / "source.wav"), "attempt_dir": str(tmp_path / "attempt"),
          "job": {"settings": {"model": "small", "language": "th", "device": "cpu"}}})
    result = events[-1]["transcript"]
    assert result["model"]["requested_compute_type"] == "int8"
    assert result["model"]["compute_type"] == "int8_float32"
    assert result["segments"][0]["text"] == " สวัสดี, café."
    assert result["segments"][0]["end_ms"] == 1000
    assert calls[0][1]["revision"] == MODELS["small"]["revision"]
    assert calls[1] == {"language": "th", "beam_size": 5, "vad_filter": True}
