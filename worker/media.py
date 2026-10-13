"""Stream media preparation without loading a recording into browser memory."""

from array import array
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave

MAX_BYTES = 2_000_000_000
MAX_SECONDS = 7200
SAMPLE_RATE = 16000
FORMATS = "wav,mp3,flac,mov,ogg,matroska,webm,avi,aac"
HDR_TRANSFERS = ("arib-std-b67", "smpte2084")


class WorkerError(Exception):
    def __init__(self, code, message, action, details=None):
        super().__init__(message)
        self.error = {"code": code, "message": message, "action": action, "details": details}


def run_command(command, timeout=None):
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise WorkerError("MEDIA_TOOL_MISSING", "Media tools are missing.", "Rebuild the worker.", str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkerError("MEDIA_TIMEOUT", "Media could not be read.", "Try another file.") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-8000:]
        raise WorkerError("MEDIA_INVALID", "Media could not be decoded.", "Try another file.", detail)
    return result.stdout


def number(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def inspect_media(source):
    if source.stat().st_size > MAX_BYTES:
        raise WorkerError("UPLOAD_TOO_LARGE", "File exceeds 2 GB.", "Use a smaller file.")
    output = run_command([
        os.getenv("FFPROBE", "ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", FORMATS, "-show_format", "-show_streams", "-of", "json", str(source),
    ], timeout=60)
    try:
        metadata = json.loads(output)
    except ValueError as exc:
        raise WorkerError("MEDIA_INVALID", "Media could not be read.", "Try another file.") from exc
    return timeline(metadata)


def timeline(metadata):
    streams = metadata.get("streams", [])
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    video = next((stream for stream in streams if stream.get("codec_type") == "video"
                  and not stream.get("disposition", {}).get("attached_pic")), None)
    if audio is None:
        raise WorkerError("NO_AUDIO", "File has no audio.", "Choose a file with audio.")
    selected = [stream for stream in (audio, video) if stream is not None]
    if any(stream.get("codec_tag_string") in ("encv", "enca") for stream in selected):
        raise WorkerError("MEDIA_ENCRYPTED", "Media is encrypted.", "Use an unencrypted file.")
    format_info = metadata.get("format", {})
    format_start = number(format_info.get("start_time"), 0)
    starts = [number(stream.get("start_time"), format_start) for stream in selected]
    origin = min(starts)
    ends = [start + number(stream.get("duration"), 0) for stream, start in zip(selected, starts)]
    format_duration = number(format_info.get("duration"), 0)
    # Matroska reports an absolute end timestamp; MP4 reports a duration from its start.
    matroska = bool({"matroska", "webm"} & set(format_info.get("format_name", "").split(",")))
    format_end = format_duration if matroska else format_start + format_duration
    duration = max(*ends, format_end) - origin
    if duration <= 0:
        raise WorkerError("MEDIA_INVALID", "Media duration is unknown.", "Try another file.")
    if duration > MAX_SECONDS + 0.001:
        raise WorkerError("MEDIA_TOO_LONG", "File exceeds 2 hours.", "Use a shorter file.")
    return {"duration_seconds": duration, "audio_index": audio["index"],
            "video_index": video["index"] if video else None,
            "audio_offset_ms": max(0, round((starts[0] - origin) * 1000)),
            "video_offset_ms": max(0, round((starts[1] - origin) * 1000)) if video else 0,
            "has_video": video is not None, "origin_seconds": origin,
            "audio_stream": audio, "video_stream": video}


def compatible_preview(source, info):
    audio = info["audio_stream"]
    video = info["video_stream"]
    if info["origin_seconds"] != 0 or info["audio_offset_ms"] or info["video_offset_ms"]:
        return False
    if source.suffix.lower() == ".wav" and video is None:
        return audio.get("codec_name") == "pcm_s16le" and audio.get("sample_rate") == "16000" and audio.get("channels") == 1
    if source.suffix.lower() == ".mp4" and video is not None:
        try:
            rates = [Fraction(video[key]) for key in ("r_frame_rate", "avg_frame_rate")]
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return False
        return (video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
                and video.get("codec_tag_string") == "avc1" and audio.get("codec_name") == "aac"
                and audio.get("profile") == "LC" and audio.get("channels") in (1, 2)
                and video.get("color_transfer") not in HDR_TRANSFERS
                and all(0 < video.get(key, 0) <= 1280 and video[key] % 2 == 0 for key in ("width", "height"))
                and all(0 < rate <= 30 for rate in rates))
    return False


def waveform(audio_path, output_path, points_per_second=20):
    peaks = []
    block_frames = SAMPLE_RATE // points_per_second
    with wave.open(str(audio_path), "rb") as recording:
        if recording.getnchannels() != 1 or recording.getsampwidth() != 2 or recording.getframerate() != SAMPLE_RATE:
            raise WorkerError("MEDIA_INVALID", "Audio preparation failed.", "Retry.")
        duration_ms = round(recording.getnframes() * 1000 / SAMPLE_RATE)
        while data := recording.readframes(block_frames):
            values = array("h", data)
            if sys.byteorder != "little":
                values.byteswap()
            peak = max(values, key=abs)
            peaks.append(round(peak / 32768, 5))
    output_path.write_text(json.dumps({"duration_ms": duration_ms, "peaks": peaks}, separators=(",", ":")), encoding="utf-8")
    return duration_ms


def prepare(source, attempt_dir, emit):
    emit({"type": "progress", "phase": "Checking media", "progress": None})
    info = inspect_media(source)
    seconds = info["duration_seconds"]
    audio_path = attempt_dir / "audio.wav"
    waveform_path = attempt_dir / "waveform.json"
    prefix = [os.getenv("FFMPEG", "ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y"]
    input_args = ["-protocol_whitelist", "file,pipe", "-format_whitelist", FORMATS, "-i", str(source)]
    emit({"type": "progress", "phase": "Preparing audio", "progress": None})
    # Decode once to the canonical timeline; pad stream offsets before inference and playback.
    filters = (f"asetpts=PTS-STARTPTS,aresample={SAMPLE_RATE}:async=1:first_pts=0,"
               f"adelay={info['audio_offset_ms']}:all=1,apad,atrim=duration={seconds:.6f}")
    run_command(prefix + input_args + ["-map", f"0:{info['audio_index']}", "-vn", "-af", filters,
                "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(audio_path)])
    emit({"type": "progress", "phase": "Preparing waveform", "progress": None})
    duration_ms = waveform(audio_path, waveform_path)
    if duration_ms <= 0:
        raise WorkerError("MEDIA_INVALID", "Audio is empty.", "Try another file.")
    emit({"type": "progress", "phase": "Preparing playback", "progress": None})
    if compatible_preview(source, info):
        preview_path = attempt_dir / ("preview" + source.suffix.lower())
        try:
            os.link(source, preview_path)
        except OSError:
            shutil.copyfile(source, preview_path)
    elif info["has_video"]:
        preview_path = attempt_dir / "preview.mp4"
        video = info["video_stream"]
        hdr = video.get("color_transfer") in HDR_TRANSFERS
        scale_factor = "min(1\\,1280/max(iw\\,ih))"
        preview_width = f"max(2\\,trunc(iw*{scale_factor}/2)*2)"
        preview_height = f"max(2\\,trunc(ih*{scale_factor}/2)*2)"
        color_filter = "format=rgb24"
        output_filter = "format=yuv420p"
        color_args = []
        if hdr:
            peak = 10 if video["color_transfer"] == "arib-std-b67" else 100
            if video["color_transfer"] == "smpte2084":
                # Use one peak for the clip so frame metadata cannot change preview brightness.
                for item in video.get("side_data_list", []):
                    try:
                        nits = float(Fraction(item.get("max_content") or item.get("max_luminance")))
                    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                        continue
                    if 0 < nits <= 10000:
                        peak = max(1, nits / 100)
                        if item.get("max_content"):
                            break
            color_filter = (f"zscale=w={preview_width}:h={preview_height}:t=linear:npl=100,"
                            "format=gbrpf32le,zscale=p=bt709,"
                            f"tonemap=hable:peak={peak}:desat=0,"
                            "zscale=t=bt709:m=gbr:r=full,format=gbrp,"
                            "sidedata=mode=delete:type=MASTERING_DISPLAY_METADATA,"
                            "sidedata=mode=delete:type=CONTENT_LIGHT_LEVEL,"
                            "sidedata=mode=delete:type=DYNAMIC_HDR_PLUS")
            output_filter = ("zscale=pin=bt709:tin=bt709:min=gbr:rin=full:"
                             "p=bt709:t=bt709:m=bt709:r=limited:d=error_diffusion,format=yuv420p,"
                             "setparams=range=limited:colorspace=bt709:color_trc=bt709:color_primaries=bt709")
            color_args = ["-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709",
                          "-color_primaries", "bt709"]
        video_filter = (f"setpts=PTS-STARTPTS,{color_filter},"
                        f"tpad=color=black:start_duration={info['video_offset_ms'] / 1000:.6f}:start_mode=add:"
                        f"stop_mode=clone:stop_duration={seconds:.6f},"
                        f"trim=duration={duration_ms / 1000:.6f},setpts=PTS-STARTPTS,"
                        f"scale={preview_width}:{preview_height},{output_filter}")
        run_command(prefix + input_args + ["-i", str(audio_path), "-map", f"0:{info['video_index']}",
                    "-map", "1:a:0", "-vf", video_filter, "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-fpsmax", "30", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", "-t", f"{duration_ms / 1000:.6f}"] + color_args + [str(preview_path)])
    else:
        preview_path = attempt_dir / "preview.mp3"
        run_command(prefix + ["-i", str(audio_path), "-map", "0:a:0", "-c:a", "libmp3lame",
                    "-b:a", "128k", str(preview_path)])
    return {"audio_path": str(audio_path), "preview_path": str(preview_path),
            "waveform_path": str(waveform_path), "has_video": info["has_video"], "duration_ms": duration_ms}
