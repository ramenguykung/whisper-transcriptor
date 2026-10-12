import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { api, download, message } from './api'
import { drafts, editorLock, type Draft } from './drafts'
import { editReducer, formatTime, mergeSegments, retime, splitSegment, validateSegments } from './edit'
import { SaveQueue, type SaveStatus } from './saveQueue'
import SegmentList from './SegmentList'
import Waveform from './Waveform'
import type { Job, Transcript, Waveform as WaveformData } from './types'

type Props = { job: Job; registerFlush: (flush: (() => Promise<void>) | null) => void }
type Loaded = { transcript: Transcript; waveform: WaveformData; draft?: Draft; warning: string }

export default function EditorLoader(props: Props) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState('')
  const [generation, setGeneration] = useState(0)
  useEffect(() => {
    let disposed = false
    const lock = editorLock(props.job.id)
    setLoaded(null); setError('')
    void (async () => {
      try {
        await lock.ready
        const [transcript, waveform] = await Promise.all([
          api<Transcript>(`/jobs/${props.job.id}/transcript`), api<WaveformData>(`/jobs/${props.job.id}/waveform`),
        ])
        let draft: Draft | undefined, warning = ''
        try {
          draft = await drafts.read(props.job.id)
          if (draft) validateSegments(draft.segments, transcript.duration_ms)
        } catch {
          warning = 'Local drafts are unavailable. Keep this tab open until edits save.'
          draft = undefined
        }
        if (!disposed) setLoaded({ transcript, waveform, draft, warning })
      } catch (failure) { if (!disposed) setError(message(failure)) }
    })()
    return () => { disposed = true; lock.release() }
  }, [props.job.id, generation])
  if (error) return <div className="load-state"><p role="alert">{error}</p><button onClick={() => setGeneration(value => value + 1)}>Reload</button></div>
  if (!loaded) return <div className="editor-loading" aria-busy="true"><div /><div /><p>Loading transcript</p></div>
  return <Editor key={generation} {...props} {...loaded} reload={() => setGeneration(value => value + 1)} />
}

function Editor({ job, transcript, waveform, draft, warning, reload, registerFlush }: Props & Loaded & { reload: () => void }) {
  const [history, dispatch] = useReducer(editReducer, { segments: draft?.segments ?? transcript.segments, past: [], future: [] })
  const historyRef = useRef(history)
  historyRef.current = history
  const [saveState, setSaveState] = useState<{ status: SaveStatus; error: unknown; storageError: unknown }>({
    status: draft ? draft.base_revision === transcript.revision ? 'pending' : 'conflict' : 'saved', error: null, storageError: null,
  })
  const mounted = useRef(true)
  const [queue] = useState(() => new SaveQueue(job.id, transcript,
    (revision, segments) => api<Transcript>(`/jobs/${job.id}/transcript`, {
      method: 'PUT', body: JSON.stringify({ expected_revision: revision, segments }),
    }), drafts, value => { if (mounted.current) setSaveState({ status: value.status, error: value.error, storageError: value.storageError }) }, draft))
  const [media, setMedia] = useState<HTMLMediaElement | null>(null)
  const [time, setTime] = useState(0)
  const [rate, setRate] = useState(1)
  const [follow, setFollow] = useState(true)
  const [selected, setSelected] = useState<string[]>([])
  const [cursor, setCursor] = useState({ id: '', position: 0 })
  const [notice, setNotice] = useState('')
  const [exportFormat, setExportFormat] = useState('srt')
  const [exporting, setExporting] = useState(false)
  const [confirmReload, setConfirmReload] = useState(false)
  useEffect(() => {
    mounted.current = true
    registerFlush(() => queue.flush())
    queue.schedule()
    const unload = (event: BeforeUnloadEvent) => {
      if (queue.dirty) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', unload)
    return () => { mounted.current = false; queue.dispose(); registerFlush(null); window.removeEventListener('beforeunload', unload) }
  }, [queue, registerFlush])
  useEffect(() => { if (media) media.playbackRate = rate }, [media, rate])

  function apply(action: Parameters<typeof editReducer>[1]) {
    const next = editReducer(historyRef.current, action)
    historyRef.current = next
    dispatch(action)
    if (next.segments !== queue.segments) queue.update(next.segments)
    setNotice('')
  }
  const select = useCallback((id: string, extend: boolean) => {
    setSelected(previous => extend ? previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id] : [id])
  }, [])
  function seek(milliseconds: number) { if (media) { media.currentTime = milliseconds / 1000; setTime(milliseconds) } }
  function timing(id: string, start: number, end: number) {
    const original = historyRef.current.segments.find(segment => segment.id === id)
    if (original?.start_ms === start && original.end_ms === end) return
    apply({ type: 'replace', segments: retime(historyRef.current.segments, id, start, end, transcript.duration_ms) })
  }
  function split() {
    try {
      const id = selected.length === 1 ? selected[0] : ''
      if (id !== cursor.id) throw new Error('Place the text cursor inside the selected segment.')
      apply({ type: 'replace', segments: splitSegment(historyRef.current.segments, id, cursor.position, time, crypto.randomUUID()) })
    } catch (error) { setNotice(message(error)) }
  }
  function merge() {
    try {
      const ordered = historyRef.current.segments.filter(segment => selected.includes(segment.id))
      if (ordered.length !== 2) throw new Error('Select two consecutive segments.')
      apply({ type: 'replace', segments: mergeSegments(historyRef.current.segments, ordered[0].id, ordered[1].id) })
      setSelected([ordered[0].id])
    } catch (error) { setNotice(message(error)) }
  }
  function downloadDraft() {
    download(`${job.filename}.draft.json`, new Blob([JSON.stringify(queue.snapshot(), null, 2)], { type: 'application/json' }))
  }
  async function discardAndReload() {
    try { await queue.discard(); setConfirmReload(false); reload() } catch (error) { setNotice(message(error)) }
  }
  async function exportFile() {
    setExporting(true); setNotice('')
    try {
      await queue.flush()
      const response = await fetch(`/api/jobs/${job.id}/export?format=${exportFormat}`)
      if (!response.ok) throw new Error('Export failed. Retry.')
      const name = job.filename.replace(/\.[^.]+$/, '') + `.${exportFormat}`
      download(name, await response.blob())
    } catch (error) { setNotice(message(error)) } finally { setExporting(false) }
  }

  const statusLabel = { saved: 'Saved', pending: 'Unsaved', saving: 'Saving', conflict: 'Save conflict', error: 'Save failed' }[saveState.status]
  return <main className="editor" onKeyDown={event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault(); apply({ type: event.shiftKey ? 'redo' : 'undo' })
    }
  }}>
    <header className="workspace-header">
      <div className="source-heading"><h1 title={job.filename}>{job.filename}</h1>
        <span className="source-meta">{formatTime(transcript.duration_ms)} <span>{transcript.language ?? 'Auto'} language</span></span>
      </div>
      <div className="workspace-actions">
        <span className={`save-state ${saveState.status}`} role="status">{statusLabel}</span>
        <label className="visually-hidden" htmlFor="export-format">Export format</label>
        <select id="export-format" value={exportFormat} onChange={event => setExportFormat(event.target.value)}>
          {['srt', 'txt', 'json', 'vtt'].map(format => <option key={format} value={format}>{format.toUpperCase()}</option>)}
        </select>
        <button className="primary" onClick={() => void exportFile()} disabled={exporting || saveState.status === 'conflict'}>{exporting ? 'Exporting' : 'Export'}</button>
      </div>
    </header>
    {(saveState.status === 'conflict' || saveState.status === 'error' || Boolean(warning) || Boolean(saveState.storageError)) && <div className="save-alert" role="alert">
      <div><strong>{saveState.status === 'conflict' ? 'The transcript changed elsewhere.' : saveState.status === 'error' ? 'Edits have not saved.' : warning || 'Local drafts are unavailable. Keep this tab open until edits save.'}</strong>
        <p>{saveState.status === 'conflict' ? 'Your draft is kept here. Download it before reloading.' : saveState.status === 'error' ? message(saveState.error) : ''}</p></div>
      <div className="button-row">
        <button onClick={downloadDraft}>Download draft</button>
        {saveState.status === 'error' && <button onClick={() => void queue.flush().catch(error => setNotice(message(error)))}>Retry save</button>}
        {saveState.status === 'conflict' && <button onClick={() => setConfirmReload(true)}>Reload</button>}
      </div>
      {confirmReload && <div className="confirm-inline"><span>Discard this local draft and load the saved transcript?</span>
        <button onClick={() => void discardAndReload()}>Discard and reload</button><button onClick={() => setConfirmReload(false)}>Keep draft</button></div>}
    </div>}
    {notice && <div className="notice" role="alert">{notice}<button className="small-button" onClick={() => setNotice('')}>Dismiss</button></div>}
    <div className="workspace-grid">
      <section className="playback-panel" aria-label="Playback">
        <div className="panel-label"><h2>Playback</h2><span>{job.has_video ? 'Video' : 'Audio'}</span></div>
        <div className={`media-surface ${job.has_video ? 'video-surface' : 'audio-surface'}`}>
          {!job.has_video && <div className="audio-label"><strong>{job.filename}</strong><span>Audio recording</span></div>}
          {job.has_video
            ? <video ref={setMedia} src={`/api/jobs/${job.id}/media`} controls preload="metadata" playsInline
              onTimeUpdate={event => setTime(Math.round(event.currentTarget.currentTime * 1000))}
              onSeeked={event => setTime(Math.round(event.currentTarget.currentTime * 1000))}
              onError={() => setNotice('Playback failed. Check the job details or retry transcription.')} />
            : <audio ref={setMedia} src={`/api/jobs/${job.id}/media`} controls preload="metadata"
              onTimeUpdate={event => setTime(Math.round(event.currentTarget.currentTime * 1000))}
              onSeeked={event => setTime(Math.round(event.currentTarget.currentTime * 1000))}
              onError={() => setNotice('Playback failed. Check the job details or retry transcription.')} />}
        </div>
        <div className="playback-controls"><label>Speed <select value={rate} onChange={event => setRate(Number(event.target.value))}>
          {[0.5, 0.75, 1, 1.25, 1.5, 2].map(value => <option key={value} value={value}>{value}x</option>)}</select></label>
          <span className="mono">{formatTime(time)}</span>
          <button className="small-button" onClick={() => seek(Math.max(0, time - 5000))}>Back 5s</button>
          <button className="small-button" onClick={() => seek(Math.min(transcript.duration_ms, time + 5000))}>Forward 5s</button>
        </div>
        <div className="playback-notes"><p>Select a segment to seek. Correct its text and timing while listening.</p>
          <details><summary>Transcript details</summary><pre>{JSON.stringify({ schema_version: transcript.schema_version, revision: queue.revision, model: transcript.model }, null, 2)}</pre></details>
        </div>
      </section>
      <section className="transcript-panel" aria-label="Transcript">
        <div className="transcript-toolbar"><h2>Transcript <span>{history.segments.length}</span></h2>
          <label className="follow-control"><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} />Follow playback</label>
        </div>
        <div className="edit-toolbar">
          <button className="small-button" onClick={() => apply({ type: 'undo' })} disabled={!history.past.length} title="Ctrl Z">Undo</button>
          <button className="small-button" onClick={() => apply({ type: 'redo' })} disabled={!history.future.length} title="Ctrl Shift Z">Redo</button>
          <button className="small-button" onClick={split} disabled={selected.length !== 1}>Split</button>
          <button className="small-button" onClick={merge} disabled={selected.length !== 2}>Merge</button>
          <span>Split at cursor and playback</span>
        </div>
        <SegmentList segments={history.segments} currentMs={time} follow={follow} selected={selected} onSelect={select} onSeek={seek}
          onText={(id, text) => apply({ type: 'replace', segments: historyRef.current.segments.map(segment => segment.id === id ? { ...segment, text } : segment) })}
          onTiming={timing} onCursor={(id, position) => setCursor({ id, position })} />
      </section>
      {media && <Waveform data={waveform} media={media} segments={history.segments} selected={selected} currentMs={time} onSelect={select} onTiming={timing} />}
    </div>
  </main>
}
