import { afterEach, describe, expect, it, vi } from 'vitest'
import { editorLock } from './drafts'

function lockManager(delivery: Promise<void> = Promise.resolve()) {
  const held = new Set<string>()
  const request = vi.fn(async (name: string, options: { ifAvailable: boolean }, callback: (lock: { name: string } | null) => Promise<void>) => {
    expect(options.ifAvailable).toBe(true)
    await delivery
    if (held.has(name)) return callback(null)
    held.add(name)
    try { return await callback({ name }) } finally { held.delete(name) }
  })
  return { request, held }
}

afterEach(() => vi.unstubAllGlobals())

describe('exclusive transcript editor', () => {
  it('blocks the same job until release while allowing a different job', async () => {
    const manager = lockManager()
    vi.stubGlobal('navigator', { locks: manager })
    const first = editorLock('one')
    await first.ready
    expect(manager.held.size).toBe(1)

    const second = editorLock('one')
    await expect(second.ready).rejects.toThrow('This transcript is open in another tab. Close it and Reload.')
    const other = editorLock('two')
    await other.ready
    expect(manager.held.size).toBe(2)

    first.release()
    first.release()
    await manager.request.mock.results[0].value
    expect(manager.held.size).toBe(1)
    const reopened = editorLock('one')
    await reopened.ready
    expect(manager.held.size).toBe(2)
    other.release()
    reopened.release()
    await Promise.all([manager.request.mock.results[2].value, manager.request.mock.results[3].value])
    expect(manager.held.size).toBe(0)
  })

  it('allows immediate reload after releasing an acquired lock', async () => {
    const manager = lockManager()
    vi.stubGlobal('navigator', { locks: manager })
    const first = editorLock('job')
    await first.ready
    first.release()
    const reopened = editorLock('job')
    await reopened.ready
    reopened.release()
    await Promise.all(manager.request.mock.results.map(result => result.value))
    expect(manager.held.size).toBe(0)
  })

  it('allows immediate remount after cleanup before submitting the lock request', async () => {
    const manager = lockManager()
    vi.stubGlobal('navigator', { locks: manager })
    const cancelled = editorLock('job')
    const rejected = expect(cancelled.ready).rejects.toMatchObject({ name: 'AbortError' })
    cancelled.release()
    cancelled.release()
    const reopened = editorLock('job')
    await rejected
    await reopened.ready
    expect(manager.request).toHaveBeenCalledTimes(1)
    reopened.release()
    await manager.request.mock.results[0].value
    expect(manager.held.size).toBe(0)
  })

  it('cancels after submission but before acquisition without retaining a lock', async () => {
    let deliver!: () => void
    const delivery = new Promise<void>(resolve => { deliver = resolve })
    const manager = lockManager(delivery)
    vi.stubGlobal('navigator', { locks: manager })
    const cancelled = editorLock('job')
    let ready = false
    void cancelled.ready.then(() => { ready = true }, () => {})
    await Promise.resolve()
    expect(manager.request).toHaveBeenCalledTimes(1)
    expect(ready).toBe(false)
    const rejected = expect(cancelled.ready).rejects.toMatchObject({ name: 'AbortError' })
    cancelled.release()
    cancelled.release()
    await rejected
    expect(manager.held.size).toBe(0)

    deliver()
    await manager.request.mock.results[0].value
    expect(manager.held.size).toBe(0)
    const reopened = editorLock('job')
    await reopened.ready
    reopened.release()
    await manager.request.mock.results[1].value
    expect(manager.held.size).toBe(0)
  })

  it('refuses editing with recovery guidance when Web Locks are unavailable', async () => {
    vi.stubGlobal('navigator', {})
    const unavailable = editorLock('job')
    await expect(unavailable.ready).rejects.toThrow(/browser/i)
    await expect(unavailable.ready).rejects.toThrow(/Reload/)
    expect(() => { unavailable.release(); unavailable.release() }).not.toThrow()
  })
})
