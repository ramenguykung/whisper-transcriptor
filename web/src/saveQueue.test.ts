import { afterEach, describe, expect, it, vi } from 'vitest'
import { SaveQueue } from './saveQueue'
import type { Draft } from './drafts'
import type { Transcript } from './types'

const transcript: Transcript = {
  schema_version: 1, revision: 1, duration_ms: 5000, language: 'th', model: {},
  segments: [{ id: 'a', start_ms: 0, end_ms: 2000, text: 'original' }],
}
const changed = (text: string) => [{ ...transcript.segments[0], text }]
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
afterEach(() => vi.useRealTimers())

describe('serialized autosave', () => {
  it('keeps edits made during a request and saves them with the returned revision', async () => {
    const first = deferred<Transcript>()
    const save = vi.fn().mockReturnValueOnce(first.promise).mockImplementation(async (revision, segments) => ({ ...transcript, revision: revision + 1, segments }))
    let stored: Draft | undefined
    const storage = { write: vi.fn(async draft => { stored = draft }), remove: vi.fn(async () => { stored = undefined }) }
    const queue = new SaveQueue('job', transcript, save, storage, () => {})
    queue.update(changed('first'))
    const flushed = queue.flush()
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    queue.update(changed('second'))
    first.resolve({ ...transcript, revision: 2, segments: changed('first') })
    await flushed
    expect(save.mock.calls[1]).toEqual([2, changed('second')])
    expect(queue.segments).toEqual(changed('second'))
    expect(queue.revision).toBe(3)
    expect(queue.status).toBe('saved')
    expect(stored).toBeUndefined()
    queue.dispose()
  })
  it('preserves a conflict draft and refuses navigation or export flush', async () => {
    const conflict = { problem: { code: 'REVISION_CONFLICT' } }
    let stored: Draft | undefined
    const storage = { write: async (draft: Draft) => { stored = draft }, remove: vi.fn(async () => {}) }
    const save = vi.fn(async () => { throw conflict })
    const queue = new SaveQueue('job', transcript, save, storage, () => {})
    queue.update(changed('local'))
    await expect(queue.flush()).rejects.toBe(conflict)
    expect(stored?.segments).toEqual(changed('local'))
    expect(queue.status).toBe('conflict')
    await expect(queue.flush()).rejects.toThrow('Resolve')
    expect(save).toHaveBeenCalledTimes(1)
    expect(storage.remove).not.toHaveBeenCalled()
    queue.dispose()
  })
  it('restores drafts and detects changed server revisions', () => {
    const storage = { write: vi.fn(async () => {}), remove: vi.fn(async () => {}) }
    const draft = { job_id: 'job', base_revision: 1, segments: changed('draft'), updated_at: '' }
    const queue = new SaveQueue('job', { ...transcript, revision: 2 }, vi.fn(), storage, () => {}, draft)
    expect(queue.status).toBe('conflict')
    expect(queue.segments).toEqual(draft.segments)
    expect(queue.snapshot().base_revision).toBe(1)
    queue.dispose()
  })
  it('retains drafts after network failure and retries without changing revision', async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error('offline')).mockImplementation(async (revision, segments) => ({ ...transcript, revision: revision + 1, segments }))
    let stored: Draft | undefined
    const storage = { write: async (draft: Draft) => { stored = draft }, remove: async () => { stored = undefined } }
    const queue = new SaveQueue('job', transcript, save, storage, () => {})
    queue.update(changed('local'))
    await expect(queue.flush()).rejects.toThrow('offline')
    expect(queue.revision).toBe(1)
    expect(stored?.segments).toEqual(changed('local'))
    await queue.flush()
    expect(queue.status).toBe('saved')
    expect(save.mock.calls[1][0]).toBe(1)
    queue.dispose()
  })
  it('debounces edits for 800 milliseconds', async () => {
    vi.useFakeTimers()
    const save = vi.fn(async (revision, segments) => ({ ...transcript, revision: revision + 1, segments }))
    const queue = new SaveQueue('job', transcript, save, { write: async () => {}, remove: async () => {} }, () => {})
    queue.update(changed('one'))
    await vi.advanceTimersByTimeAsync(700)
    queue.update(changed('two'))
    await vi.advanceTimersByTimeAsync(799)
    expect(save).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.calls[0][1]).toEqual(changed('two'))
    queue.dispose()
  })
  it('saves remotely when local storage fails and warns while edits stay in memory', async () => {
    const save = vi.fn(async (revision, segments) => ({ ...transcript, revision: revision + 1, segments }))
    const unavailable = new Error('storage full')
    const queue = new SaveQueue('job', transcript, save, {
      write: async () => { throw unavailable }, remove: async () => { throw unavailable },
    }, () => {})
    queue.update(changed('local'))
    await queue.flush()
    expect(save).toHaveBeenCalledWith(1, changed('local'))
    expect(queue.status).toBe('saved')
    expect(queue.storageError).toBe(unavailable)
    expect(queue.segments).toEqual(changed('local'))
    queue.dispose()
  })
  it('drains queued draft writes before discard and does not recreate a discarded draft', async () => {
    const write = deferred<void>()
    let stored: Draft | undefined
    const storage = { write: vi.fn(async (draft: Draft) => { await write.promise; stored = draft }), remove: vi.fn(async () => { stored = undefined }) }
    const queue = new SaveQueue('job', transcript, vi.fn(), storage, () => {})
    queue.update(changed('discard me'))
    const discarded = queue.discard()
    await vi.waitFor(() => expect(storage.write).toHaveBeenCalledTimes(1))
    write.resolve()
    await discarded
    queue.dispose()
    await Promise.resolve()
    expect(stored).toBeUndefined()
    expect(queue.dirty).toBe(false)
    expect(storage.write).toHaveBeenCalledTimes(1)
  })
})
