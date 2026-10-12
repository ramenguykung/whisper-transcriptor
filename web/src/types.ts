export type Device = 'cpu' | 'cuda'
export type Settings = { model: 'base' | 'small' | 'medium'; language: string | null; device: Device }
export type Problem = { code: string; message: string; action: string; details: unknown }
export type Job = {
  id: string
  filename: string
  status: 'uploading' | 'queued' | 'processing' | 'cancelling' | 'cancelled' | 'ready' | 'failed'
  phase: string
  progress: number | null
  settings: Settings
  created_at: string
  updated_at: string
  duration_ms: number | null
  has_video: boolean | null
  error: Problem | null
}
export type Segment = { id: string; start_ms: number; end_ms: number; text: string }
export type Transcript = {
  schema_version: 1
  revision: number
  duration_ms: number
  language: string | null
  model: Record<string, unknown>
  segments: Segment[]
}
export type WorkerCapabilities = {
  ready?: boolean
  versions?: Record<string, string>
  devices?: string[]
  supported_devices?: string[]
  compute_types?: Record<string, string[]>
  models?: unknown
  diagnostic?: Problem | null
  last_diagnostic?: Problem | null
  [key: string]: unknown
}
export type System = {
  protocol_version: number
  worker_online: boolean
  worker: { worker_id: string; protocol_version: number; capabilities: WorkerCapabilities; last_seen: string } | null
  limits: { upload_bytes: number; duration_seconds: number }
  cpu_command: string
  gpu_command: string
}
export type Waveform = { duration_ms: number; peaks: number[] }
