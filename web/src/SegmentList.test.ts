import { beforeEach, expect, it, vi } from 'vitest'
import type { ComponentProps, ReactElement } from 'react'
import SegmentList from './SegmentList'

const hooks = vi.hoisted(() => ({ setWindow: vi.fn(), useEffect: vi.fn() }))
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useEffect: hooks.useEffect,
  useRef: () => ({ current: null }),
  useState: (initial: unknown) => [initial, hooks.setWindow],
}))

const props: ComponentProps<typeof SegmentList> = {
  segments: [{ id: 'a', start_ms: 0, end_ms: 1000, text: 'สวัสดี, world! 😀' }],
  currentMs: 0, follow: false, selected: [],
  onSelect: vi.fn(), onSeek: vi.fn(), onText: vi.fn(), onTiming: vi.fn(), onCursor: vi.fn(),
}

beforeEach(() => vi.clearAllMocks())

it('keeps the scroll position after React clears the event target', () => {
  const list = SegmentList(props) as ReactElement<{ onScroll: (event: unknown) => void }>
  const event: { currentTarget: { scrollTop: number } | null } = { currentTarget: { scrollTop: 592 } }
  list.props.onScroll(event)
  event.currentTarget = null

  const update = hooks.setWindow.mock.calls[0][0]
  const previous = { top: 0, height: 500 }
  expect(update(previous)).toEqual({ top: 592, height: 500 })
  expect(update(previous)).toEqual({ top: 592, height: 500 })
  expect(previous).toEqual({ top: 0, height: 500 })
  expect(props.onText).not.toHaveBeenCalled()
})

it('does not observe a missing scroll container for an empty transcript', () => {
  SegmentList({ ...props, segments: [] })
  expect(hooks.useEffect.mock.calls[0][1]).toEqual([false])
  expect(() => hooks.useEffect.mock.calls[0][0]()).not.toThrow()
})
