import { useCallback, useEffect, useRef, useState } from 'react'
import { api, message, uploadSource } from './api'
import { drafts } from './drafts'
import { formatTime } from './edit'
import Editor from './Editor'
import type { Device, Job, Settings, System } from './types'

const initialSettings: Settings = { model: 'small', language: null, device: 'cpu' }
const accept = '.wav,.mp3,.flac,.m4a,.aac,.ogg,.mp4,.mov,.mkv,.webm,.avi,audio/*,video/*'
const statusLabels = { uploading: 'Uploading', queued: 'Queued', processing: 'Transcribing', cancelling: 'Cancelling', cancelled: 'Cancelled', ready: 'Ready', failed: 'Failed' }

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [system, setSystem] = useState<System | null>(null)
  const [loading, setLoading] = useState(true)
  const [connection, setConnection] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [settings, setSettings] = useState(initialSettings)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [notice, setNotice] = useState('')
  const [upload, setUpload] = useState<{ id: string; progress: number } | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const flushRef = useRef<(() => Promise<void>) | null>(null)
  const uploadRef = useRef<ReturnType<typeof uploadSource> | null>(null)
  const uploadJobRef = useRef<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const refresh = useCallback(async () => {
    const [jobResult, systemResult] = await Promise.allSettled([api<Job[]>('/jobs'), api<System>('/system')])
    if (jobResult.status === 'fulfilled') { setJobs(jobResult.value); setConnection('') }
    else setConnection('API unavailable. Start Docker and reload.')
    if (systemResult.status === 'fulfilled') setSystem(systemResult.value)
    else setSystem(null)
    setLoading(false)
  }, [])
  useEffect(() => {
    void refresh()
    const interval = setInterval(() => void refresh(), 2000)
    return () => clearInterval(interval)
  }, [refresh])
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (uploadRef.current) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', unload)
    return () => window.removeEventListener('beforeunload', unload)
  }, [])
  const registerFlush = useCallback((flush: (() => Promise<void>) | null) => { flushRef.current = flush }, [])
  const selected = jobs.find(job => job.id === selectedId) ?? null
  const capabilities = system?.worker?.capabilities
  const gpuReady = Boolean(system?.worker_online && capabilities?.ready && (capabilities.supported_devices ?? capabilities.devices ?? []).includes('cuda'))
  const workerReady = Boolean(system?.worker_online && capabilities?.ready)
  const runtimeLabel = !system ? 'Runtime unavailable' : !system.worker_online ? 'Worker offline' : !capabilities?.ready ? 'Worker needs attention' : gpuReady ? 'CPU and GPU ready' : 'CPU ready'

  async function selectJob(id: string | null) {
    try { await flushRef.current?.(); setSelectedId(id); setLibraryOpen(false); setDeleteId(null); setNotice('') }
    catch (error) { setNotice(message(error)) }
  }
  function chooseFile(next: File | null) {
    setNotice('')
    if (!next) { setFile(null); return }
    const limit = system?.limits.upload_bytes ?? 2_000_000_000
    if (next.size > limit) { setNotice('File exceeds 2 GB. Choose a smaller recording.'); setFile(null); return }
    if (!next.size) { setNotice('The file is empty. Choose another recording.'); setFile(null); return }
    setFile(next)
  }
  async function transcribe() {
    if (!file || busy) return
    setBusy(true); setNotice('')
    try {
      await flushRef.current?.()
      const job = await api<Job>('/jobs', { method: 'POST', body: JSON.stringify({ filename: file.name, settings: { ...settings, device: gpuReady ? settings.device : 'cpu' } }) })
      await flushRef.current?.()
      uploadJobRef.current = job.id
      setJobs(value => [job, ...value]); setSelectedId(job.id); setLibraryOpen(false)
      setUpload({ id: job.id, progress: 0 })
      const request = uploadSource(job.id, file, progress => setUpload({ id: job.id, progress }))
      uploadRef.current = request
      const queued = await request.promise
      setJobs(value => value.map(entry => entry.id === queued.id ? queued : entry))
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) setNotice(message(error))
    } finally {
      uploadRef.current = null; uploadJobRef.current = null; setUpload(null); setBusy(false); void refresh()
    }
  }
  async function action(job: Job, type: 'cancel' | 'retry', device?: Device) {
    setNotice('')
    try {
      await flushRef.current?.()
      if (type === 'cancel' && uploadJobRef.current === job.id) uploadRef.current?.abort()
      await api(`/jobs/${job.id}/${type}`, { method: 'POST', ...(device ? { body: JSON.stringify({ device }) } : {}) })
      void refresh()
    } catch (error) { setNotice(message(error)) }
  }
  async function deleteJob(job: Job) {
    setDeleting(true); setNotice('')
    try {
      await flushRef.current?.()
      if (uploadJobRef.current === job.id) uploadRef.current?.abort()
      let latest = job
      if (['uploading', 'queued', 'processing', 'cancelling'].includes(latest.status)) {
        latest = await api<Job>(`/jobs/${job.id}/cancel`, { method: 'POST' })
        const deadline = Date.now() + 65_000
        while (['uploading', 'queued', 'processing', 'cancelling'].includes(latest.status)) {
          if (Date.now() > deadline) throw new Error('Processing is still stopping. Retry delete in a moment.')
          await new Promise(resolve => setTimeout(resolve, 500))
          latest = await api<Job>(`/jobs/${job.id}`)
        }
      }
      await api(`/jobs/${job.id}`, { method: 'DELETE' })
      await drafts.remove(job.id).catch(() => {})
      setSelectedId(current => current === job.id ? null : current)
      setDeleteId(null); void refresh()
    } catch (error) { setNotice(message(error)) } finally { setDeleting(false) }
  }

  return <div className="app-shell">
    <div className="app-bar"><a className="brand" href="#" onClick={event => { event.preventDefault(); void selectJob(null) }}>Whisper Transcriptor</a>
      <span className="local-label">Local workspace</span>
      <button className="library-toggle" aria-expanded={libraryOpen} onClick={() => setLibraryOpen(value => !value)}>Files</button>
      <span className={`runtime-pill${workerReady ? ' ready' : ''}`}>{runtimeLabel}</span>
    </div>
    <div className="app-body">
      <aside className={`library${libraryOpen ? ' open' : ''}`} aria-label="Files and settings">
        <section className="upload-section"><h2>New transcript</h2>
          <label className={`file-picker${file ? ' has-file' : ''}`} onDragOver={event => event.preventDefault()}
            onDrop={event => { event.preventDefault(); chooseFile(event.dataTransfer.files[0] ?? null) }}>
            <span>{file ? file.name : 'Choose audio or video'}</span>
            <span className="file-hint">{file ? `${(file.size / 1_000_000).toFixed(1)} MB` : 'Or drop a file here'}</span>
            <input ref={inputRef} type="file" accept={accept} disabled={busy} onChange={event => chooseFile(event.target.files?.[0] ?? null)} />
          </label>
          <div className="settings-grid"><label>Model<select value={settings.model} onChange={event => setSettings(value => ({ ...value, model: event.target.value as Settings['model'] }))} disabled={busy}>
            <option value="base">Base</option><option value="small">Small</option><option value="medium">Medium</option></select></label>
            <label>Device<select value={gpuReady ? settings.device : 'cpu'} onChange={event => setSettings(value => ({ ...value, device: event.target.value as Device }))} disabled={busy}>
              <option value="cpu">CPU</option><option value="cuda" disabled={!gpuReady}>GPU</option></select></label>
            <label className="language-setting">Language<input value={settings.language ?? ''} placeholder="Auto" maxLength={12} disabled={busy}
              onChange={event => setSettings(value => ({ ...value, language: event.target.value.trim() || null }))} aria-describedby="language-help" /></label>
          </div>
          <span className="field-hint" id="language-help">Leave blank for auto. Use a code such as en or th.</span>
          <button className="primary transcribe-button" disabled={!file || busy || deleting || Boolean(connection)} onClick={() => void transcribe()}>{busy ? 'Uploading' : 'Transcribe'}</button>
          <p className="upload-limit">Up to 2 GB and 2 hours</p>
        </section>
        <section className="job-library"><div className="library-heading"><h2>Files</h2><span>{jobs.length}</span></div>
          {loading ? <p className="muted" aria-busy="true">Loading files</p> : !jobs.length ? <p className="muted library-empty">Your recordings appear here after upload.</p>
            : <ul>{jobs.map(job => <li key={job.id}><button className={`job-button${selectedId === job.id ? ' current' : ''}`} onClick={() => void selectJob(job.id)} aria-current={selectedId === job.id ? 'true' : undefined}>
              <span className="job-name" title={job.filename}>{job.filename}</span><span className={`job-state ${job.status}`}>{statusLabels[job.status]}{job.duration_ms !== null ? <span className="mono">{formatTime(job.duration_ms).slice(0, 8)}</span> : ''}</span>
            </button></li>)}</ul>}
        </section>
        <details className="runtime-details"><summary>Runtime details</summary>
          {system ? <><p>{runtimeLabel}</p>{capabilities?.last_diagnostic && <div className="runtime-error"><p>{capabilities.last_diagnostic.message}</p><p>{capabilities.last_diagnostic.action}</p></div>}
            {!workerReady && <><p>Start or restart the CPU worker</p><pre className="command">{system.cpu_command}</pre></>}
            <details><summary>Diagnostics</summary><pre>{JSON.stringify(system.worker, null, 2)}</pre></details>
            <p>GPU startup</p><pre className="command">{system.gpu_command}</pre></> : <p>Start the Docker stack to check the worker.</p>}
        </details>
      </aside>
      <div className="main-area">
        {connection && <div className="connection-alert" role="alert"><span>{connection}</span><button onClick={() => void refresh()}>Reload</button></div>}
        {notice && <div className="notice app-notice" role="alert"><span>{notice}</span><button className="small-button" onClick={() => setNotice('')}>Dismiss</button></div>}
        {selected && <div className="job-actions"><span>{statusLabels[selected.status]}</span><div className="button-row">
          {['uploading', 'queued', 'processing'].includes(selected.status) && <button className="small-button" onClick={() => void action(selected, 'cancel')}>Cancel</button>}
          {['failed', 'cancelled'].includes(selected.status) && <><button className="small-button" onClick={() => void action(selected, 'retry')}>Retry</button>
            {selected.settings.device === 'cuda' && <button className="small-button" onClick={() => void action(selected, 'retry', 'cpu')}>Use CPU</button>}</>}
          <button className="small-button" disabled={deleting} onClick={() => setDeleteId(selected.id)}>Delete</button>
        </div></div>}
        {deleteId && selected && <div className="delete-confirm" role="alert"><span>Delete this recording and transcript?</span>
          <button className="danger" disabled={deleting} onClick={() => void deleteJob(selected)}>{deleting ? 'Stopping' : 'Delete'}</button><button disabled={deleting} onClick={() => setDeleteId(null)}>Keep file</button></div>}
        {selected?.status === 'ready' ? <Editor key={selected.id} job={selected} registerFlush={registerFlush} />
          : selected ? <div className="job-progress"><div className="progress-heading"><h1>{selected.filename}</h1><span className={`job-state ${selected.status}`}>{statusLabels[selected.status]}</span></div>
            {selected.error ? <div className="job-error" role="alert"><h2>{selected.error.message}</h2><p>{selected.error.action}</p>
              <details><summary>Details</summary><pre>{JSON.stringify(selected.error.details, null, 2)}</pre></details></div>
              : <><p>{upload?.id === selected.id ? 'Uploading source' : selected.phase?.replaceAll('_', ' ') || statusLabels[selected.status]}</p>
                {['uploading', 'processing'].includes(selected.status) && <progress aria-label="Job progress" max="1" value={upload?.id === selected.id ? upload.progress : selected.progress ?? undefined} />}
                {selected.status === 'queued' && <p>{workerReady ? 'Waiting for the worker.' : 'Waiting for a ready worker. Open Runtime details to fix it.'}</p>}
                {selected.status === 'cancelled' && <p>The job stopped. Retry to start again.</p>}
                {selected.status === 'cancelling' && <p>Stopping processing before cleanup.</p>}
              </>}
            {!workerReady && system && <div className="worker-recovery"><h2>{runtimeLabel}</h2><p>Start or restart the CPU worker</p><pre className="command">{system.cpu_command}</pre></div>}
          </div> : <main className="welcome"><div className="welcome-content"><h1>Turn recordings into text</h1>
            <p>Upload audio or video. Edit the transcript with playback, then export subtitles or text.</p>
            <button className="primary" onClick={() => { setLibraryOpen(true); inputRef.current?.click() }}>Upload a recording</button>
            <div className="welcome-formats"><h2>Audio and video</h2><p>WAV MP3 FLAC M4A AAC OGG<br />MP4 MOV MKV WebM AVI</p><h2>Export</h2><p>SRT TXT JSON VTT</p></div>
            <div className="welcome-runtime"><strong>{runtimeLabel}</strong><p>Files and transcripts stay on this computer.</p>
              {!workerReady && system && <><p>Start or restart the CPU worker</p><pre className="command">{system.cpu_command}</pre></>}
            </div>
          </div></main>}
      </div>
    </div>
  </div>
}
