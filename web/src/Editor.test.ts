import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { isValidElement, type ComponentProps, type ReactElement, type ReactNode } from 'react'
import { api } from './api'
import { drafts, type Draft } from './drafts'
import EditorLoader from './Editor'
import SegmentList from './SegmentList'
import { SaveQueue } from './saveQueue'
import Waveform from './Waveform'
import type { Job, Segment, Transcript } from './types'

const hooks = vi.hoisted(() => ({ values: [] as unknown[], index: 0, effects: vi.fn() }))
const wave = vi.hoisted(() => ({ on: vi.fn(), destroy: vi.fn(), getScroll: () => 0 }))
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useEffect: hooks.effects,
  useRef: (current: unknown) => ({ current }),
  useCallback: (callback: unknown) => callback,
  useState: (initial: unknown) => {
    const index = hooks.index++
    if (index >= hooks.values.length) hooks.values[index] = typeof initial === 'function' ? initial() : initial
    return [hooks.values[index], (value: unknown) => {
      hooks.values[index] = typeof value === 'function' ? value(hooks.values[index]) : value
    }]
  },
  useReducer: (reducer: (state: unknown, action: unknown) => unknown, initial: unknown) => {
    const index = hooks.index++
    if (index >= hooks.values.length) hooks.values[index] = initial
    return [hooks.values[index], (action: unknown) => { hooks.values[index] = reducer(hooks.values[index], action) }]
  },
}))
vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof import('./api')>(), api: vi.fn() }))
vi.mock('./drafts', async importOriginal => ({
  ...await importOriginal<typeof import('./drafts')>(),
  drafts: { read: vi.fn(), write: vi.fn().mockResolvedValue(undefined), remove: vi.fn().mockResolvedValue(undefined) },
}))
vi.mock('wavesurfer.js', () => ({ default: { create: () => wave } }))
vi.mock('wavesurfer.js/dist/plugins/regions.esm.js', () => ({ default: { create: () => ({ on: vi.fn() }) } }))
vi.mock('wavesurfer.js/dist/plugins/timeline.esm.js', () => ({ default: { create: () => ({}) } }))

type ElementProps = {
  children?: ReactNode; role?: string; value?: unknown
  ref?: (media: HTMLMediaElement) => void
  onError?: () => void; onLoadedData?: () => void; onClick?: () => void
  onChange?: (event: { target: { value: string } }) => void
  onText?: (id: string, text: string) => void; segments?: Segment[]
}
function find(tree: ReactNode, matches: (element: ReactElement<ElementProps>) => boolean): ReactElement<ElementProps> | undefined {
  for (const node of Array.isArray(tree) ? tree : [tree]) {
    if (!isValidElement<ElementProps>(node)) continue
    if (matches(node)) return node
    const child = find(node.props.children, matches)
    if (child) return child
  }
}
const transcript: Transcript = {
  schema_version: 1, revision: 2, duration_ms: 1000, language: 'th', model: {},
  segments: [{ id: 'a', start_ms: 0, end_ms: 1000, text: 'Saved.' }],
}
const draft: Draft = { job_id: 'job', base_revision: 2, segments: [{ ...transcript.segments[0], text: 'Draft สวัสดี, world! 😀' }], updated_at: '' }
const job: Job = {
  id: 'job', filename: 'IMG_0453.MOV', status: 'ready', phase: 'ready', progress: 1,
  settings: { model: 'base', language: null, device: 'cuda' }, created_at: '', updated_at: '',
  duration_ms: 1000, has_video: true, error: null,
}
const loaded = { transcript, draft, waveform: { duration_ms: 1000, peaks: [0] }, warning: '' }

beforeEach(() => { vi.clearAllMocks(); hooks.values = []; hooks.index = 0; vi.useFakeTimers() })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it.each([true, false])('recovers playback with has_video=%s while retaining drafts and undo history', has_video => {
  const flush = vi.spyOn(SaveQueue.prototype, 'flush'), discard = vi.spyOn(SaveQueue.prototype, 'discard')
  const reload = vi.fn(), registerFlush = vi.fn()
  hooks.values = [loaded, '', 0]
  const child = EditorLoader({ job: { ...job, has_video }, registerFlush }) as ReactElement<ComponentProps<typeof EditorLoader> & typeof loaded & { reload: () => void }>
  const Editor = child.type as (props: typeof child.props) => ReactElement
  hooks.values = []
  const render = () => { hooks.index = 0; return Editor({ ...child.props, reload }) }
  let tree = render()
  const media = { playbackRate: 1, defaultPlaybackRate: 1, load: vi.fn() } as unknown as HTMLMediaElement
  vi.mocked(media.load).mockImplementation(() => { media.playbackRate = media.defaultPlaybackRate })
  find(tree, node => node.type === (has_video ? 'video' : 'audio'))!.props.ref!(media)
  tree = render()
  find(tree, node => node.type === 'select' && node.props.value === 1)!.props.onChange!({ target: { value: '2' } })
  tree = render()
  hooks.effects.mock.calls.find(([, deps]) => deps[0] === media && deps[1] === 2)![0]()
  find(tree, node => node.type === (has_video ? 'video' : 'audio'))!.props.onError!()
  tree = render()
  expect(find(tree, node => node.props.role === 'alert')!.props.children).toContain('Playback failed.')

  find(tree, node => node.type === SegmentList)!.props.onText!('a', 'Edited สวัสดี, world! 😀')
  tree = render()
  find(tree, node => node.type === 'button' && node.props.children === 'Reload playback')!.props.onClick!()
  expect(media.load).toHaveBeenCalledOnce()
  expect(media.playbackRate).toBe(2)
  expect(media.defaultPlaybackRate).toBe(2)
  expect(find(tree, node => node.type === SegmentList)!.props.segments![0].text).toBe('Edited สวัสดี, world! 😀')
  expect(discard).not.toHaveBeenCalled()
  expect(flush).not.toHaveBeenCalled()
  expect(drafts.remove).not.toHaveBeenCalled()
  expect(api).not.toHaveBeenCalled()
  expect(reload).not.toHaveBeenCalled()

  find(tree, node => node.type === (has_video ? 'video' : 'audio'))!.props.onLoadedData!()
  tree = render()
  expect(find(tree, node => node.type === 'button' && node.props.children === 'Reload playback')).toBeUndefined()
  find(tree, node => node.type === 'button' && node.props.children === 'Undo')!.props.onClick!()
  expect(find(render(), node => node.type === SegmentList)!.props.segments).toEqual(draft.segments)
})

it('clears the waveform playback error on media recovery and removes its listener', () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const media = new EventTarget() as HTMLMediaElement
  const removeListener = vi.spyOn(media, 'removeEventListener')
  const props = { data: loaded.waveform, media, segments: draft.segments, selected: [], currentMs: 0, onSelect: vi.fn(), onTiming: vi.fn() }
  Waveform(props)
  const cleanup = hooks.effects.mock.calls[0][0]()
  wave.on.mock.calls.find(([event]) => event === 'error')![1](new Error('PIPELINE_ERROR'))
  hooks.index = 0
  expect(find(Waveform(props), node => node.props.role === 'alert')!.props.children).toBe('PIPELINE_ERROR')
  media.dispatchEvent(new Event('loadeddata'))
  hooks.index = 0
  expect(find(Waveform(props), node => node.props.role === 'alert')).toBeUndefined()
  cleanup()
  expect(removeListener).toHaveBeenCalledWith('loadeddata', expect.any(Function))
  expect(wave.destroy).toHaveBeenCalledOnce()
})
