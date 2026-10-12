# HTTP protocol

All times in transcripts and media metadata are integer milliseconds. Job identifiers and attempt tokens are server-generated UUIDs. API errors are `{code, message, action, details}`; `details` may be null. Browser responses use JSON except media/export. Internal calls use `Authorization: Bearer <WORKER_TOKEN>`. Protocol version is 1.

## Browser

- `POST /api/jobs` accepts `{filename, settings:{model:"base"|"small"|"medium", language:string|null, device:"cpu"|"cuda"}}`; returns Job (201).
- `PUT /api/jobs/{id}/source` accepts the raw file body, returns Job; enqueue only after the complete upload is published atomically.
- `GET /api/jobs` returns `Job[]`, newest first. `GET /api/jobs/{id}` returns Job.
- `POST /api/jobs/{id}/cancel`, `POST /api/jobs/{id}/retry` (optional `{device:"cpu"|"cuda"}`), and `DELETE /api/jobs/{id}` return Job, Job, and 204 respectively. Retry requires failed/cancelled state and a source file. Delete returns 409 while upload/processing is active; cancel first.
- `GET /api/jobs/{id}/transcript` returns Transcript. `PUT` accepts `{expected_revision:number, segments:Segment[]}` and returns the new Transcript. Revision mismatch returns 409 `REVISION_CONFLICT`.
- `GET /api/jobs/{id}/media` supports byte ranges and returns the accepted preview. `GET /api/jobs/{id}/waveform` returns `{duration_ms:number, peaks:number[]}` with signed normalized mono peaks.
- `GET /api/jobs/{id}/export?format=srt|txt|json|vtt` returns UTF-8 content with attachment filename.
- `GET /api/system` returns `{protocol_version:1, worker:Worker|null, worker_online:boolean, limits:{upload_bytes:number,duration_seconds:number}, cpu_command:string, gpu_command:string}`.

Job: `{id,filename,status,phase,progress,settings,created_at,updated_at,duration_ms,has_video,error}`. Status: `uploading|queued|processing|cancelling|cancelled|ready|failed`. Progress: number 0..1 or null. Error: the error envelope or null. `created_at` and `updated_at` are UTC ISO strings. Other media fields may be null before preparation.

Segment: `{id:string,start_ms:number,end_ms:number,text:string}`. Transcript: `{schema_version:1,revision:number,duration_ms:number,language:string|null,model:object,segments:Segment[]}`. Segments are ordered by start time, preserve stable IDs, allow overlaps, and require `0 <= start < end <= duration`.

## Worker

- `POST /internal/v1/register` accepts `{worker_id,protocol_version:1,capabilities:object}` and returns `{ok:true}`. Capabilities include versions, supported devices/compute types, model cache state, readiness, and last diagnostic. Mismatch is a structured 409. Register every 5 seconds even while running/degraded.
- `POST /internal/v1/claim` accepts `{worker_id}` and returns `{job:Job|null,attempt_token?:string,source_path?:string,attempt_dir?:string,lease_seconds?:60}`. Paths are absolute container paths under MEDIA_DIR, shared at the same mount path. Refuse claim while degraded or another lease is active.
- `POST /internal/v1/jobs/{id}/heartbeat` accepts `{worker_id,attempt_token,phase,progress}` and returns `{cancel_requested:boolean,lease_seconds:60}`. Stale lease is 409 `STALE_ATTEMPT`.
- `POST /internal/v1/jobs/{id}/complete` accepts `{worker_id,attempt_token,transcript:Transcript,preview_path:string,waveform_path:string,has_video:boolean}` and returns Job. Paths must remain inside the accepted attempt directory and exist; only this API publication makes a result ready.
- `POST /internal/v1/jobs/{id}/fail` accepts `{worker_id,attempt_token,error:{code,message,action,details}}` and returns Job. `POST /internal/v1/jobs/{id}/cancelled` accepts `{worker_id,attempt_token}` and returns Job after the child process has stopped.

Lease heartbeats run every 5 seconds; leases expire after 60 seconds. Requeue a lost lease once, then fail with retry guidance. Reject stale attempt publication. Cancellation and deterministic errors are never automatically retried.
