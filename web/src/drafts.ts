import type { Segment } from './types'

export type Draft = { job_id: string; base_revision: number; segments: Segment[]; updated_at: string }

export function editorLock(id: string) {
  let released = false
  let releaseHeld!: () => void
  let rejectReady!: (reason: unknown) => void
  const held = new Promise<void>(resolve => { releaseHeld = resolve })
  const ready = new Promise<void>((resolve, reject) => {
    rejectReady = reject
    // StrictMode cleanup cancels its first effect before a lock request is submitted.
    void Promise.resolve().then(() => {
      if (released) return
      if (!globalThis.navigator?.locks) throw new Error('Editing is unavailable in this browser. Use a current browser and Reload.')
      return navigator.locks.request(`whisper-editor:${id}`, { ifAvailable: true }, async lock => {
        if (released) return
        if (!lock) throw new Error('This transcript is open in another tab. Close it and Reload.')
        resolve()
        await held
      })
    }).catch(reject)
  })
  return {
    ready,
    release: () => {
      released = true
      releaseHeld()
      rejectReady(new DOMException('Editor closed', 'AbortError'))
    },
  }
}

let database: Promise<IDBDatabase> | undefined
function open() {
  database ??= new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('whisper-drafts', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'job_id' })
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => { database = undefined; reject(request.error) }
  })
  return database
}

async function transaction<T>(mode: IDBTransactionMode, operation: (store: IDBObjectStore) => IDBRequest<T>) {
  const db = await open()
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction('drafts', mode)
    const request = operation(tx.objectStore('drafts'))
    tx.oncomplete = () => resolve(request.result)
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error ?? new Error('Draft storage interrupted.'))
  })
}

export const drafts = {
  read: (id: string) => transaction<Draft | undefined>('readonly', store => store.get(id)),
  write: (draft: Draft) => transaction('readwrite', store => store.put(draft)),
  remove: (id: string) => transaction('readwrite', store => store.delete(id)),
}
