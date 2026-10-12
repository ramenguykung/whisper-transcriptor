import type { Segment } from './types'

export function formatTime(milliseconds: number) {
  const value = Math.max(0, Math.round(milliseconds))
  const hours = Math.floor(value / 3_600_000)
  const minutes = Math.floor(value / 60_000) % 60
  const seconds = Math.floor(value / 1000) % 60
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(value % 1000).padStart(3, '0')}`
}

export function parseTime(value: string): number | null {
  const match = /^(\d{1,3}):(\d{2}):(\d{2})\.(\d{3})$/.exec(value.trim())
  if (!match || Number(match[2]) > 59 || Number(match[3]) > 59) return null
  return Number(match[1]) * 3_600_000 + Number(match[2]) * 60_000 + Number(match[3]) * 1000 + Number(match[4])
}

export function validateSegments(segments: Segment[], duration: number) {
  const ids = new Set<string>()
  let previous = -1
  for (const segment of segments) {
    if (!segment.id || ids.has(segment.id)) throw new Error('Segment IDs must be unique.')
    ids.add(segment.id)
    if (!Number.isInteger(segment.start_ms) || !Number.isInteger(segment.end_ms)
      || segment.start_ms < 0 || segment.end_ms <= segment.start_ms || segment.end_ms > duration) {
      throw new Error('Use times inside the recording, with end after start.')
    }
    if (segment.start_ms < previous) throw new Error('Segments must be ordered by start time.')
    previous = segment.start_ms
  }
}

export function retime(segments: Segment[], id: string, start: number, end: number, duration: number) {
  const next = segments.map(segment => segment.id === id ? { ...segment, start_ms: start, end_ms: end } : segment)
    .sort((a, b) => a.start_ms - b.start_ms)
  validateSegments(next, duration)
  return next
}

export function splitSegment(segments: Segment[], id: string, cursor: number, playhead: number, newId: string) {
  const index = segments.findIndex(segment => segment.id === id)
  const segment = segments[index]
  if (!segment || cursor <= 0 || cursor >= segment.text.length) throw new Error('Place the text cursor inside the segment.')
  const before = segment.text.charCodeAt(cursor - 1), after = segment.text.charCodeAt(cursor)
  if (before >= 0xD800 && before <= 0xDBFF && after >= 0xDC00 && after <= 0xDFFF) {
    throw new Error('Place the text cursor between characters.')
  }
  const time = Math.round(playhead)
  if (time <= segment.start_ms || time >= segment.end_ms) throw new Error('Place playback inside the segment.')
  const next = [...segments.slice(0, index),
    { ...segment, end_ms: time, text: segment.text.slice(0, cursor) },
    { ...segment, id: newId, start_ms: time, text: segment.text.slice(cursor) },
    ...segments.slice(index + 1)]
  return next.sort((a, b) => a.start_ms - b.start_ms)
}

export function mergeSegments(segments: Segment[], firstId: string, secondId: string) {
  const index = segments.findIndex(segment => segment.id === firstId)
  const first = segments[index], second = segments[index + 1]
  if (!first || !second || second.id !== secondId) throw new Error('Select two consecutive segments.')
  return [...segments.slice(0, index), {
    ...first, start_ms: Math.min(first.start_ms, second.start_ms), end_ms: Math.max(first.end_ms, second.end_ms),
    text: `${first.text}\n${second.text}`,
  }, ...segments.slice(index + 2)]
}

export type EditState = { segments: Segment[]; past: Segment[][]; future: Segment[][] }
export type EditAction = { type: 'replace'; segments: Segment[] } | { type: 'undo' | 'redo' }
export function editReducer(state: EditState, action: EditAction): EditState {
  if (action.type === 'replace') {
    if (action.segments === state.segments) return state
    return { segments: action.segments, past: [...state.past.slice(-99), state.segments], future: [] }
  }
  if (action.type === 'undo') {
    if (!state.past.length) return state
    return { segments: state.past[state.past.length - 1], past: state.past.slice(0, -1), future: [state.segments, ...state.future] }
  }
  if (!state.future.length) return state
  return { segments: state.future[0], past: [...state.past, state.segments], future: state.future.slice(1) }
}
