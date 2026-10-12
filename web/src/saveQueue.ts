import type { Draft } from './drafts'
import type { Segment, Transcript } from './types'

export type SaveStatus = 'saved' | 'pending' | 'saving' | 'error' | 'conflict'
type Storage = { write: (draft: Draft) => Promise<unknown>; remove: (id: string) => Promise<unknown> }

export class SaveQueue {
  revision: number
  segments: Segment[]
  status: SaveStatus = 'saved'
  error: unknown = null
  storageError: unknown = null
  private version = 0
  private savedVersion = 0
  private timer: ReturnType<typeof setTimeout> | undefined
  private inflight: Promise<void> | undefined
  private writes: Promise<unknown> = Promise.resolve()
  private conflict = false

  constructor(
    private id: string,
    transcript: Transcript,
    private save: (revision: number, segments: Segment[]) => Promise<Transcript>,
    private storage: Storage,
    private changed: (queue: SaveQueue) => void,
    draft?: Draft,
  ) {
    this.revision = transcript.revision
    this.segments = draft?.segments ?? transcript.segments
    if (draft) {
      this.version = 1
      this.conflict = draft.base_revision !== transcript.revision
      this.status = this.conflict ? 'conflict' : 'pending'
      if (this.conflict) this.revision = draft.base_revision
    }
  }

  get dirty() { return this.version !== this.savedVersion }
  snapshot(): Draft {
    return { job_id: this.id, base_revision: this.revision, segments: this.segments, updated_at: new Date().toISOString() }
  }
  private notify(status: SaveStatus, error: unknown = null) {
    this.status = status; this.error = error; this.changed(this)
  }
  private persist() {
    const draft = this.snapshot()
    this.writes = this.writes.catch(() => {}).then(() => this.storage.write(draft))
      .catch(error => { this.storageError = error; this.changed(this) })
    return this.writes
  }
  update(segments: Segment[]) {
    this.segments = segments; this.version++
    this.notify(this.conflict ? 'conflict' : 'pending')
    void this.persist().catch(error => this.notify(this.conflict ? 'conflict' : 'error', error))
    this.schedule()
  }
  schedule() {
    clearTimeout(this.timer)
    if (!this.conflict && this.dirty) this.timer = setTimeout(() => { void this.flush().catch(() => {}) }, 800)
  }
  async flush(): Promise<void> {
    clearTimeout(this.timer)
    if (this.conflict) throw new Error('Resolve the save conflict first. Download the draft before reloading.')
    if (this.inflight) { await this.inflight; return this.flush() }
    if (!this.dirty) return
    const version = this.version, segments = this.segments
    this.notify('saving')
    this.inflight = (async () => {
      try {
        await this.persist()
        const result = await this.save(this.revision, segments)
        this.revision = result.revision; this.savedVersion = version
        if (this.dirty) await this.persist()
        else {
          this.writes = this.writes.catch(() => {}).then(() => this.storage.remove(this.id))
            .catch(error => { this.storageError = error; this.changed(this) })
          await this.writes
        }
        this.notify(this.dirty ? 'pending' : 'saved')
      } catch (error) {
        this.conflict = (error as { problem?: { code?: string } })?.problem?.code === 'REVISION_CONFLICT'
        this.notify(this.conflict ? 'conflict' : 'error', error)
        throw error
      } finally { this.inflight = undefined }
    })()
    await this.inflight
    if (this.dirty) return this.flush()
  }
  async discard() {
    clearTimeout(this.timer)
    await this.inflight?.catch(() => {})
    await this.writes
    await this.storage.remove(this.id)
    this.savedVersion = this.version
  }
  dispose() {
    clearTimeout(this.timer)
    // A pending draft stays durable even when navigation tears down the editor.
    if (this.dirty) void this.persist().catch(() => {})
  }
}
