import json
from pathlib import Path
import shutil
import subprocess
import wave

import pytest

from worker.media import WorkerError, compatible_preview, inspect_media, prepare, timeline, waveform


def metadata(audio_start=0, video_start=0, duration=4):
    return {"format": {"duration": str(duration), "start_time": "0"}, "streams": [
        {"index": 0, "codec_type": "audio", "start_time": str(audio_start), "duration": str(duration - audio_start),
         "codec_name": "aac", "profile": "LC", "channels": 1},
        {"index": 1, "codec_type": "video", "start_time": str(video_start), "duration": str(duration - video_start),
         "codec_name": "h264", "pix_fmt": "yuv420p", "codec_tag_string": "avc1"}]}


def test_delayed_streams_share_earliest_timeline():
    info = timeline(metadata(audio_start=2))
    assert info["audio_offset_ms"] == 2000 and info["video_offset_ms"] == 0
    assert info["duration_seconds"] == 4
    info = timeline(metadata(audio_start=0, video_start=1))
    assert info["video_offset_ms"] == 1000


def test_large_matroska_timestamp_origin_does_not_count_as_duration():
    info = metadata(audio_start=50000, video_start=50000, duration=50002)
    info["format"].update(start_time="50000", format_name="matroska,webm")
    assert timeline(info)["duration_seconds"] == 2


def test_invalid_media_and_duration_are_rejected():
    with pytest.raises(WorkerError, match="no audio"):
        timeline({"streams": []})
    with pytest.raises(WorkerError, match="2 hours"):
        timeline(metadata(duration=7200.01))
    encrypted = metadata()
    encrypted["streams"][0]["codec_tag_string"] = "enca"
    with pytest.raises(WorkerError, match="encrypted"):
        timeline(encrypted)


def test_direct_preview_is_narrow_and_disallows_offsets():
    assert compatible_preview(Path("recording.mp4"), timeline(metadata()))
    assert not compatible_preview(Path("recording.mkv"), timeline(metadata()))
    assert not compatible_preview(Path("recording.mp4"), timeline(metadata(audio_start=1)))
    wrong_codec = metadata()
    wrong_codec["streams"][1]["codec_name"] = "hevc"
    assert not compatible_preview(Path("recording.mp4"), timeline(wrong_codec))


def test_signed_waveform_is_streamed_and_duration_is_exact(tmp_path):
    import struct

    source = tmp_path / "audio.wav"
    with wave.open(str(source), "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(struct.pack("<h", -16000) * 800 + struct.pack("<h", 8000) * 800)
    output = tmp_path / "waveform.json"
    assert waveform(source, output) == 100
    data = json.loads(output.read_text())
    assert data["peaks"] == [-0.48828, 0.24414]


requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


@requires_ffmpeg
def test_canonical_wav_playback_uses_original(tmp_path):
    source = tmp_path / "source.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=1:sample_rate=16000", "-c:a", "pcm_s16le", source)
    output = tmp_path / "attempt"
    output.mkdir()
    result = prepare(source, output, lambda _: None)
    assert result["duration_ms"] == 1000
    assert Path(result["preview_path"]).suffix == ".wav"
    assert Path(result["preview_path"]).read_bytes() == source.read_bytes()


@requires_ffmpeg
def test_delayed_audio_is_padded_in_inference_waveform_and_preview(tmp_path):
    source = tmp_path / "source.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=64x48:d=4:r=10", "-itsoffset", "2", "-f", "lavfi", "-i",
           "sine=frequency=400:duration=2:sample_rate=16000", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", source)
    output = tmp_path / "attempt"
    output.mkdir()
    info = inspect_media(source)
    assert info["audio_offset_ms"] > 1800
    result = prepare(source, output, lambda _: None)
    assert 3900 <= result["duration_ms"] <= 4100
    data = json.loads(Path(result["waveform_path"]).read_text())
    assert all(peak == 0 for peak in data["peaks"][:34])
    assert any(abs(peak) > 0.05 for peak in data["peaks"][42:])
    preview = inspect_media(Path(result["preview_path"]))
    assert preview["audio_offset_ms"] <= 1 and preview["video_offset_ms"] <= 1
    assert abs(preview["duration_seconds"] * 1000 - result["duration_ms"]) <= 100


@requires_ffmpeg
def test_video_delay_is_black_padded_and_audio_stays_at_zero(tmp_path):
    source = tmp_path / "source.mkv"
    ffmpeg("-itsoffset", "1", "-f", "lavfi", "-i", "color=c=blue:s=64x48:d=2:r=10", "-f", "lavfi", "-i",
           "sine=frequency=400:duration=3:sample_rate=16000", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", source)
    output = tmp_path / "attempt"
    output.mkdir()
    assert inspect_media(source)["video_offset_ms"] == 1000
    result = prepare(source, output, lambda _: None)
    preview = inspect_media(Path(result["preview_path"]))
    assert preview["audio_offset_ms"] <= 1 and preview["video_offset_ms"] <= 1
    pixels = subprocess.check_output(["ffmpeg", "-v", "error", "-i", result["preview_path"], "-frames:v", "1",
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    assert max(pixels) <= 3


@requires_ffmpeg
def test_corrupt_file_fails_with_action(tmp_path):
    source = tmp_path / "bad.mp3"
    source.write_bytes(b"invalid audio")
    with pytest.raises(WorkerError) as error:
        inspect_media(source)
    assert error.value.error["code"] == "MEDIA_INVALID"
    assert error.value.error["action"]


@requires_ffmpeg
@pytest.mark.parametrize("extension,audio_codec", [("mkv", "pcm_s16le"), ("mp4", "aac")])
def test_positive_timestamp_origin_is_removed_without_trailing_silence(tmp_path, extension, audio_codec):
    source = tmp_path / ("source." + extension)
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=64x48:d=2:r=10", "-f", "lavfi", "-i",
           "sine=frequency=400:duration=2:sample_rate=16000", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", audio_codec, "-output_ts_offset", "5", source)
    output = tmp_path / "attempt"
    output.mkdir()
    assert inspect_media(source)["origin_seconds"] > 4.8
    result = prepare(source, output, lambda _: None)
    assert 2000 <= result["duration_ms"] <= 2070
    preview = inspect_media(Path(result["preview_path"]))
    assert preview["origin_seconds"] == 0
    assert abs(preview["duration_seconds"] * 1000 - result["duration_ms"]) <= 100
