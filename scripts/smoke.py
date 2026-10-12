"""Exercise the complete HTTP flow against a running Docker stack."""
import argparse
import io
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import wave


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8081")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    def request(path, method="GET", body=None, headers=None, expected=200):
        headers = dict(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(args.base_url + path, data=body, method=method, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            assert response.status == expected, (method, path, response.status, data[:500])
            if response.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(data)
            return data

    deadline = time.monotonic() + 60
    while True:
        system = request("/api/system")
        if system["worker_online"] and system["worker"]["capabilities"].get("ready"):
            break
        assert time.monotonic() < deadline, system
        time.sleep(1)
    mode = system["worker"]["capabilities"].get("mode")
    if args.source:
        assert mode != "fixture", "Real inference requires a real worker"
        source = args.source.read_bytes()
        filename = args.source.name
    else:
        assert mode == "fixture", "Synthetic test audio requires the isolated fixture worker"
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * 16000 * 6)
        source = buffer.getvalue()
        filename = "Editor check.wav"
    job = request("/api/jobs", "POST", {"filename": filename, "settings": {"model": "small", "language": None, "device": args.device}}, expected=201)
    path = "/api/jobs/" + job["id"]
    request(path + "/source", "PUT", source, {"Content-Type": "application/octet-stream"})
    deadline = time.monotonic() + args.timeout
    last_phase = None
    while True:
        job = request(path)
        if job["phase"] != last_phase:
            print(job["status"], job["phase"], flush=True)
            last_phase = job["phase"]
        assert job["status"] not in ("failed", "cancelled"), job
        if job["status"] == "ready":
            break
        assert time.monotonic() < deadline, job
        time.sleep(1)
    transcript = request(path + "/transcript")
    assert transcript["schema_version"] == 1 and transcript["segments"], transcript
    print("Inference:", json.dumps(transcript["model"]), flush=True)
    print("Recognized:", " ".join(s["text"] for s in transcript["segments"])[:180], flush=True)
    waveform = request(path + "/waveform")
    assert waveform["peaks"] and waveform["duration_ms"] == transcript["duration_ms"]
    assert len(request(path + "/media", headers={"Range": "bytes=0-31"}, expected=206)) == 32
    segments = transcript["segments"]
    segments[0]["text"] = "Hello สวัสดีโลก\nEdited caption"
    saved = request(path + "/transcript", "PUT", {"expected_revision": transcript["revision"], "segments": segments})
    assert saved["revision"] == transcript["revision"] + 1
    conflict = request(path + "/transcript", "PUT", {"expected_revision": transcript["revision"], "segments": segments}, expected=409)
    assert conflict["code"] == "REVISION_CONFLICT", conflict
    for format_name in ("srt", "txt", "json", "vtt"):
        data = request(path + "/export?format=" + format_name)
        text = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data.decode("utf-8")
        assert "สวัสดีโลก" in text, (format_name, text)
    if not args.keep:
        request(path, "DELETE", expected=204)
        request(path, expected=404)
    print("PASS upload, inference, preview, waveform, save, conflict, and four exports. Job:", job["id"])


if __name__ == "__main__":
    main()
