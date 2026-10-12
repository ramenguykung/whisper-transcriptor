"""Stream a near-2-GB, two-hour WAV through an isolated acceptance stack."""
import argparse
import http.client
import json
from pathlib import Path
import struct
import time
import urllib.request
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8081")
    parser.add_argument("--real", action="store_true", help="Validate real CPU inference on two hours of silence")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    def request(path, body=None, method="GET"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(args.base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response) if response.status != 204 else None

    system = request("/api/system")
    assert system["worker_online"] and system["worker"]["capabilities"].get("ready"), system
    mode = system["worker"]["capabilities"].get("mode")
    assert (mode != "fixture") if args.real else (mode == "fixture"), "Select the matching worker mode"
    root = Path(__file__).resolve().parents[1] / ".local"
    root.mkdir(exist_ok=True)
    path = root / "scale-2-hours.wav"
    rate, frames = 69440, 69440 * 7200
    data_bytes = frames * 4
    with path.open("wb") as file:
        file.write(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", data_bytes + 36, b"WAVE", b"fmt ",
                               16, 1, 2, rate, rate * 4, 4, 16, b"data", data_bytes))
        file.seek(data_bytes + 43)
        file.write(b"\0")
    size = path.stat().st_size
    assert size <= system["limits"]["upload_bytes"] and size > 1_990_000_000
    job = request("/api/jobs", {"filename": "Scale check 2 hours.wav", "settings": {
        "model": "small", "language": "en", "device": "cpu"}}, "POST")
    job_path = "/api/jobs/" + job["id"]
    target = urlsplit(args.base_url)
    connection = http.client.HTTPConnection(target.hostname, target.port, timeout=60)
    started = time.monotonic()
    connection.putrequest("PUT", job_path + "/source")
    connection.putheader("Content-Type", "application/octet-stream")
    connection.putheader("Content-Length", str(size))
    connection.endheaders()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            connection.send(chunk)
    response = connection.getresponse()
    result = response.read()
    assert response.status == 200, result[:500]
    connection.close()
    print(f"Streamed {size} bytes in {time.monotonic() - started:.1f}s", flush=True)
    deadline = time.monotonic() + 900
    phase = None
    while True:
        job = request(job_path)
        if phase != job["phase"]:
            phase = job["phase"]
            print(job["status"], phase, flush=True)
        assert job["status"] not in ("failed", "cancelled"), job
        if job["status"] == "ready":
            break
        assert time.monotonic() < deadline, job
        time.sleep(2)
    transcript = request(job_path + "/transcript")
    waveform = request(job_path + "/waveform")
    assert transcript["duration_ms"] == waveform["duration_ms"] == 7_200_000
    assert len(waveform["peaks"]) == 144_000
    if args.real:
        assert not transcript["segments"], "Silent input must not invent speech"
    else:
        segments = [{"id": f"scale-{index}", "start_ms": index * 1200, "end_ms": (index + 1) * 1200,
                     "text": f"Scale check segment {index + 1}"} for index in range(6000)]
        saved = request(job_path + "/transcript", {"expected_revision": transcript["revision"], "segments": segments}, "PUT")
        assert len(saved["segments"]) == 6000
    with urllib.request.urlopen(urllib.request.Request(args.base_url + job_path + "/media",
                                                       headers={"Range": "bytes=0-31"})) as preview:
        assert preview.status == 206 and len(preview.read()) == 32
    if not args.keep:
        request(job_path, method="DELETE")
    print(f"PASS two-hour processing, waveform, playback, and {'real silence inference' if args.real else '6000-segment document'} in {time.monotonic() - started:.1f}s. Job: {job['id']}")


if __name__ == "__main__":
    main()
