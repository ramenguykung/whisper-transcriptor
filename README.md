# Whisper Transcriptor

Local audio and video transcription with synchronized subtitle editing. React and Vite, FastAPI, SQLite, and a separate faster-whisper worker run in Docker.

## Start

Start Docker Desktop with its Linux backend. Python 3 is needed for the small configuration and test scripts. From the repository:

```sh
python scripts/setup.py
docker compose up --build -d
```

Open [localhost:8080](http://localhost:8080). Upload a recording, choose a model, transcribe, edit against playback, and export SRT, TXT, JSON, or VTT. Limits are 2 GB and 2 hours. Model downloads happen on the first use and stay in the model volume. Audio and video stay local.

For NVIDIA GPU support on Windows, enable Docker Desktop's WSL 2 backend and use a compatible NVIDIA driver:

```sh
docker compose -f compose.yaml -f compose.gpu.yaml up --build -d
```

The GPU worker replaces the CPU worker. The GTX 1060 uses `int8_float32`; CPU uses `int8`. Choose the device in Upload settings. Use CPU retries a failed job on the running worker. If the GPU container cannot start, run the CPU start command instead. The interface stays available without a worker.

## Editing

Click a caption to seek. Follow keeps the active caption visible. Edit text and start/end times or drag timeline boundaries. Split uses the text cursor and playhead inside the selected caption. Merge joins consecutive selected captions with a newline. Overlapping captions are allowed. Undo and Redo change the current document; autosave preserves revisions.

Pending edits are stored in this browser. Export waits for a save. A revision conflict preserves the local draft and offers Reload or Download draft. Delete removes the original and derived files after processing stops. Completed jobs remain until you delete them.

Each recording opens for editing in one tab at a time. Close that editor before opening it in another tab, then use Reload. Editing requires a current browser with Web Locks support on localhost.

## Checks

```sh
python -m venv .venv
.venv/bin/python -m pip install -r api/requirements-dev.txt
.venv/bin/python -m pytest api/tests worker/tests
cd web
npm ci
npm test
npm run build
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`. Worker media tests also require FFmpeg and FFprobe on PATH.

Run deterministic integration checks in a separate project and port:

```sh
WEB_PORT=8081 docker compose -p transcriptor-test -f compose.yaml -f compose.test.yaml up --build -d
python scripts/smoke.py
docker compose -p transcriptor-test -f compose.yaml -f compose.test.yaml down
```

In PowerShell, set `$env:WEB_PORT = '8081'` before the Compose command, then remove it with `Remove-Item Env:WEB_PORT` afterward. Fixture mode is only for this isolated test stack and never runs real inference.

Check real inference using your own short speech recording:

```sh
python scripts/smoke.py --base-url http://localhost:8080 --source sample.wav --device cpu
```

Use `--device cuda` with the GPU worker. This check exercises actual inference, playback ranges, waveform, revision saves, conflicts, and all exports.

The scale check creates a 1,999,872,044-byte WAV in `.local`, streams it to the stack, and validates the two-hour timeline. Fixture mode also creates 6000 captions for browser rendering checks:

```sh
python scripts/scale.py --keep
python scripts/scale.py --base-url http://localhost:8080 --real
```

Reserve about 5 GB of free space for this check. Delete `.local/scale-2-hours.wav` afterward. Without `--keep`, the script deletes its completed test job; fixture mode requires the isolated test stack.

Validated on this Windows Docker Desktop host on 2026-10-13: short real CPU and GTX 1060 inference with multilingual `small`, converted MKV playback with shifted timestamps, a near-2-GB two-hour silent recording through real CPU inference, and a 6000-caption editor at desktop and narrow widths. All 59 regression tests passed: 14 API, 28 worker, and 17 editor tests. They cover leases and uploads, media normalization, cancellation, editing, autosave races, exclusive draft access, and exports. Longer speech accuracy and the `base` and `medium` models have not been benchmarked.

## Runtime status

System status shows worker heartbeat, versions, supported compute types, cached models, and the last structured failure. Details contain the diagnostics and recovery command. Dependencies, images, and model revisions are pinned independently of the API. Protocol mismatches block job claims and explain which service needs rebuilding.

```sh
docker compose logs --tail 100 api worker
docker compose down
```

Stopping the stack keeps named volumes. `docker compose down -v` deletes all recordings, transcripts, and model caches; use it only when intentionally clearing local data.

## Commits

Group commits by module, feature, then modification type. Use Conventional Commits, stage explicit paths, run relevant checks, and sign every commit with `git commit -S`. Verify with `git verify-commit HEAD`. See [AGENTS.md](AGENTS.md) for the enforced repository rules.
