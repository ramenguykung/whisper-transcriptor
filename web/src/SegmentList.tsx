import { useEffect, useRef, useState } from 'react'
import { formatTime, parseTime } from './edit'
import type { Segment } from './types'

const ROW_HEIGHT = 148
type Props = {
  segments: Segment[]
  currentMs: number
  follow: boolean
  selected: string[]
  onSelect: (id: string, extend: boolean) => void
  onSeek: (time: number) => void
  onText: (id: string, text: string) => void
  onTiming: (id: string, start: number, end: number) => void
  onCursor: (id: string, cursor: number) => void
}

function SegmentRow({ segment, index, active, selected, onSelect, onSeek, onText, onTiming, onCursor }: {
  segment: Segment; index: number; active: boolean; selected: boolean
} & Omit<Props, 'segments' | 'currentMs' | 'follow' | 'selected'>) {
  const [start, setStart] = useState(formatTime(segment.start_ms))
  const [end, setEnd] = useState(formatTime(segment.end_ms))
  const [error, setError] = useState('')
  useEffect(() => { setStart(formatTime(segment.start_ms)); setEnd(formatTime(segment.end_ms)); setError('') }, [segment.start_ms, segment.end_ms])
  function commit() {
    const startMs = parseTime(start), endMs = parseTime(end)
    if (startMs === null || endMs === null) { setError('Use HH:MM:SS.mmm'); return }
    try { onTiming(segment.id, startMs, endMs); setError('') } catch (error) { setError((error as Error).message) }
  }
  return <div className={`segment-row${active ? ' active' : ''}${selected ? ' selected' : ''}`}
    style={{ top: index * ROW_HEIGHT, height: ROW_HEIGHT }} role="group" aria-label={`Segment ${index + 1}`}>
    <div className="segment-top">
      <label className="segment-select">
        <input type="checkbox" checked={selected} onChange={() => onSelect(segment.id, true)} aria-label={`Select segment ${index + 1}`} />
        <span>{index + 1}</span>
      </label>
      <input className="time-field" value={start} aria-label={`Segment ${index + 1} start`} aria-invalid={Boolean(error)}
        spellCheck={false} onChange={event => setStart(event.target.value)} onBlur={commit}
        onKeyDown={event => { if (event.key === 'Enter') { event.currentTarget.blur() } }} />
      <span className="time-divider">to</span>
      <input className="time-field" value={end} aria-label={`Segment ${index + 1} end`} aria-invalid={Boolean(error)}
        spellCheck={false} onChange={event => setEnd(event.target.value)} onBlur={commit}
        onKeyDown={event => { if (event.key === 'Enter') { event.currentTarget.blur() } }} />
      <button className="small-button" onClick={() => { onSelect(segment.id, false); onSeek(segment.start_ms) }} aria-label={`Seek to segment ${index + 1}`}>Seek</button>
    </div>
    <textarea aria-label={`Segment ${index + 1} text`} value={segment.text} rows={3}
      onFocus={event => { onSelect(segment.id, false); onCursor(segment.id, event.currentTarget.selectionStart) }}
      onSelect={event => onCursor(segment.id, event.currentTarget.selectionStart)}
      onChange={event => { onText(segment.id, event.target.value); onCursor(segment.id, event.target.selectionStart) }} />
    <span className="row-error" role={error ? 'alert' : undefined}>{error}</span>
  </div>
}

export default function SegmentList(props: Props) {
  const container = useRef<HTMLDivElement>(null)
  const [window, setWindow] = useState({ top: 0, height: 500 })
  const activeIndex = props.segments.findIndex(segment => segment.start_ms <= props.currentMs && segment.end_ms > props.currentMs)
  useEffect(() => {
    const element = container.current!
    const observer = new ResizeObserver(() => setWindow(value => ({ ...value, height: element.clientHeight })))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (!props.follow || activeIndex < 0 || document.activeElement?.closest('.segment-row')) return
    const element = container.current!
    const position = activeIndex * ROW_HEIGHT
    if (position < element.scrollTop || position + ROW_HEIGHT > element.scrollTop + element.clientHeight) {
      element.scrollTo({ top: Math.max(0, position - element.clientHeight / 3) })
    }
  }, [activeIndex, props.follow])
  const first = Math.max(0, Math.floor(window.top / ROW_HEIGHT) - 3)
  const last = Math.min(props.segments.length, Math.ceil((window.top + window.height) / ROW_HEIGHT) + 3)
  if (!props.segments.length) return <div className="transcript-empty">No speech found. Export the empty transcript or try another model.</div>
  return <div className="segment-list" ref={container} tabIndex={0} aria-label="Transcript segments"
    onScroll={event => setWindow(value => ({ ...value, top: event.currentTarget.scrollTop }))}>
    <div className="segment-spacer" style={{ height: props.segments.length * ROW_HEIGHT }}>
      {props.segments.slice(first, last).map((segment, offset) => <SegmentRow key={segment.id} {...props}
        segment={segment} index={first + offset} active={segment.start_ms <= props.currentMs && segment.end_ms > props.currentMs}
        selected={props.selected.includes(segment.id)} />)}
    </div>
  </div>
}
