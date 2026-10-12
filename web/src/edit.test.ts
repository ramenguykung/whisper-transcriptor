import { describe, expect, it } from 'vitest'
import { editReducer, formatTime, mergeSegments, parseTime, retime, splitSegment, validateSegments } from './edit'
import type { Segment } from './types'

const segments: Segment[] = [
  { id: 'a', start_ms: 0, end_ms: 2500, text: 'สวัสดี, world!' },
  { id: 'b', start_ms: 2000, end_ms: 4000, text: 'Next\nline.' },
]

describe('transcript edits', () => {
  it('splits exactly at cursor and playhead, preserving Unicode and punctuation', () => {
    const split = splitSegment(segments, 'a', 7, 1000, 'new')
    expect(split.map(segment => segment.id)).toEqual(['a', 'new', 'b'])
    expect(split[0].text + split[1].text).toBe(segments[0].text)
    expect(split[0].end_ms).toBe(1000)
    expect(split[1].start_ms).toBe(1000)
    expect(() => splitSegment(segments, 'a', 2, 0, 'new')).toThrow('playback')
    expect(() => splitSegment(segments, 'a', 0, 1000, 'new')).toThrow('cursor')
    expect(() => splitSegment([{ ...segments[0], text: 'A😀B' }], 'a', 2, 1000, 'new')).toThrow('characters')
  })
  it('merges consecutive overlap with outer boundaries and a newline', () => {
    expect(mergeSegments(segments, 'a', 'b')).toEqual([
      { ...segments[0], end_ms: 4000, text: 'สวัสดี, world!\nNext\nline.' },
    ])
    expect(() => mergeSegments(segments, 'b', 'a')).toThrow('consecutive')
  })
  it('accepts overlaps and reorders timing edits without changing IDs', () => {
    expect(() => validateSegments(segments, 4000)).not.toThrow()
    expect(retime(segments, 'a', 2100, 3000, 4000).map(segment => segment.id)).toEqual(['b', 'a'])
    expect(() => retime(segments, 'a', 1000, 5000, 4000)).toThrow('inside')
    expect(() => retime(segments, 'a', 1000.5, 2000, 4000)).toThrow('inside')
    expect(() => validateSegments([...segments, segments[0]], 4000)).toThrow('unique')
  })
  it('undo and redo preserve original values; new edits drop redo', () => {
    const state = { segments, past: [], future: [] }
    const merged = mergeSegments(segments, 'a', 'b')
    const edited = editReducer(state, { type: 'replace', segments: merged })
    const undone = editReducer(edited, { type: 'undo' })
    expect(undone.segments).toBe(segments)
    expect(editReducer(undone, { type: 'redo' }).segments).toBe(merged)
    expect(editReducer(undone, { type: 'replace', segments: segments.slice() }).future).toEqual([])
  })
  it('parses unambiguous milliseconds and rejects malformed times', () => {
    expect(parseTime(formatTime(7_199_999))).toBe(7_199_999)
    expect(parseTime('00:60:00.000')).toBeNull()
    expect(parseTime('1.5')).toBeNull()
    expect(formatTime(0)).toBe('00:00:00.000')
  })
})
