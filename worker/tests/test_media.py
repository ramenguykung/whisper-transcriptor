import json
from fractions import Fraction
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
         "codec_name": "h264", "pix_fmt": "yuv420p", "codec_tag_string": "avc1", "width": 640, "height": 480,
         "r_frame_rate": "10/1", "avg_frame_rate": "10/1"}]}


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


@pytest.mark.parametrize("video,compatible", [
    ({"width": 1280, "height": 720, "r_frame_rate": "30/1", "avg_frame_rate": "30/1"}, True),
    ({"height": 1282}, False), ({"width": 1920}, False), ({"width": 1279}, False),
    ({"r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001"}, True),
    ({"r_frame_rate": "60/1"}, False), ({"avg_frame_rate": "31/1"}, False),
    ({"r_frame_rate": "0/0"}, False), ({"avg_frame_rate": "NaN"}, False),
    ({"r_frame_rate": None}, False), ({"avg_frame_rate": "0/1"}, False),
    ({"color_transfer": "arib-std-b67"}, False), ({"color_transfer": "smpte2084"}, False),
    ({"color_transfer": "bt709"}, True),
])
def test_direct_preview_obeys_browser_decode_bounds(video, compatible):
    info = metadata()
    info["streams"][1].update(video)
    assert compatible_preview(Path("recording.mp4"), timeline(info)) is compatible


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
@pytest.mark.parametrize("width,height,fps,rotation,expected_size,expected_fps", [
    (1080, 1920, 60, 0, (720, 1280), 30), (64, 48, 10, 0, (64, 48), 10),
    (64, 48, 10, 90, (48, 64), 10),
])
def test_video_preview_caps_decode_load_without_upscaling(tmp_path, width, height, fps, rotation, expected_size, expected_fps):
    source = tmp_path / "source.mov"
    ffmpeg("-f", "lavfi", "-i", f"color=c=blue:s={width}x{height}:d=1:r={fps}", "-f", "lavfi", "-i",
           "sine=frequency=400:duration=1:sample_rate=16000", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", source)
    if rotation:
        rotated = tmp_path / "rotated.mov"
        help_text = subprocess.check_output(["ffmpeg", "-hide_banner", "-h", "full"], stderr=subprocess.STDOUT)
        if b"-display_rotation" in help_text:
            ffmpeg("-display_rotation", str(rotation), "-i", source, "-c", "copy", rotated)
        else:
            ffmpeg("-i", source, "-c", "copy", "-metadata:s:v:0", f"rotate={rotation}", rotated)
        source = rotated
    output = tmp_path / "attempt"
    output.mkdir()
    result = prepare(source, output, lambda _: None)
    preview = inspect_media(Path(result["preview_path"]))
    video = preview["video_stream"]
    assert (video["width"], video["height"]) == expected_size
    assert Fraction(video["avg_frame_rate"]) == expected_fps
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert preview["audio_stream"]["codec_name"] == "aac"
    assert preview["audio_offset_ms"] <= 1 and preview["video_offset_ms"] <= 1
    assert result["duration_ms"] == 1000
    assert abs(preview["duration_seconds"] * 1000 - result["duration_ms"]) <= 100
    ffmpeg("-xerror", "-ss", "0.6", "-i", result["preview_path"], "-frames:v", "1", "-f", "null", "-")


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
@pytest.mark.parametrize("transfer", ["arib-std-b67", "smpte2084"])
def test_hdr_preview_is_tone_mapped_to_consistent_sdr_with_black_padding(tmp_path, transfer):
    source = tmp_path / "hdr.mkv"
    ffmpeg("-itsoffset", "0.5", "-f", "lavfi", "-i", "color=c=0x4080c0:s=64x48:d=1:r=10", "-f", "lavfi", "-i",
           "sine=frequency=400:duration=1.5:sample_rate=16000", "-map", "0:v", "-map", "1:a", "-c:v", "ffv1",
           "-pix_fmt", "yuv420p10le", "-color_range", "tv", "-colorspace", "bt2020nc",
           "-color_primaries", "bt2020", "-color_trc", transfer, "-c:a", "pcm_s16le", source)
    output = tmp_path / "attempt"
    output.mkdir()
    info = inspect_media(source)
    assert info["video_stream"]["pix_fmt"] == "yuv420p10le" and info["video_offset_ms"] == 500
    result = prepare(source, output, lambda _: None)
    preview = inspect_media(Path(result["preview_path"]))
    assert result["duration_ms"] == 1500
    assert preview["audio_offset_ms"] <= 1 and preview["video_offset_ms"] <= 1
    assert abs(preview["duration_seconds"] * 1000 - result["duration_ms"]) <= 100
    frames = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                                "-show_frames", "-of", "json", result["preview_path"]]))["frames"]
    for frame in frames:
        assert (frame["color_space"], frame["color_transfer"], frame["color_primaries"], frame["color_range"]) == (
            "bt709", "bt709", "bt709", "tv")
        assert not any(item["side_data_type"] in ("Mastering display metadata", "Content light level metadata",
                                                "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)", "Dolby Vision RPU Data",
                                                "Dolby Vision Metadata") for item in frame.get("side_data_list", []))
    black = subprocess.check_output(["ffmpeg", "-v", "error", "-i", result["preview_path"], "-frames:v", "1",
                                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    assert max(black) <= 3
    before = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.7", "-i", str(source), "-frames:v", "1",
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    after = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.7", "-i", result["preview_path"], "-frames:v", "1",
                                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    assert sum(abs(a - b) for a, b in zip(before, after)) / len(after) > 10


@pytest.mark.parametrize("side_data,expected_peak", [
    ([{"max_luminance": "40000000/10000"}, {"max_content": 1000}], 10),
    ([{"max_luminance": "10000000/10000"}], 10),
    ([{"max_content": "0/0"}, {"max_luminance": "invalid"}, {"max_content": 0}], 100),
])
def test_pq_preview_uses_one_valid_static_peak(monkeypatch, tmp_path, side_data, expected_peak):
    info = timeline(metadata())
    info["video_stream"].update(color_transfer="smpte2084", side_data_list=side_data)
    commands = []
    monkeypatch.setattr("worker.media.inspect_media", lambda _: info)
    monkeypatch.setattr("worker.media.waveform", lambda *_: 1000)
    monkeypatch.setattr("worker.media.run_command", lambda command: commands.append(command))
    prepare(Path("source.mp4"), tmp_path, lambda _: None)
    video_filter = commands[-1][commands[-1].index("-vf") + 1]
    peak = float(video_filter.split("tonemap=hable:peak=")[1].split(":")[0])
    assert peak == expected_peak


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
