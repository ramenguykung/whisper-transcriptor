import type { Job, Problem } from './types'

export class ApiError extends Error {
  constructor(public status: number, public problem: Problem) { super(problem.message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  })
  if (!response.ok) {
    let problem: Problem
    try { problem = await response.json() } catch {
      problem = { code: 'HTTP_ERROR', message: `Request failed (${response.status})`, action: 'Retry', details: null }
    }
    throw new ApiError(response.status, problem)
  }
  return response.status === 204 ? undefined as T : response.json()
}

export function uploadSource(id: string, file: File, onProgress: (progress: number) => void) {
  const request = new XMLHttpRequest()
  const promise = new Promise<Job>((resolve, reject) => {
    request.open('PUT', `/api/jobs/${id}/source`)
    request.setRequestHeader('Content-Type', 'application/octet-stream')
    request.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded / event.total) }
    request.onload = () => {
      let body: Job | Problem
      try { body = JSON.parse(request.responseText) } catch {
        reject(new Error('Upload failed. Retry the upload.')); return
      }
      if (request.status >= 200 && request.status < 300) resolve(body as Job)
      else reject(new ApiError(request.status, body as Problem))
    }
    request.onerror = () => reject(new Error('Upload interrupted. Retry the upload.'))
    request.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'))
    request.send(file)
  })
  return { promise, abort: () => request.abort() }
}

export function message(error: unknown) {
  return error instanceof Error ? error.message : 'Request failed. Retry.'
}

export function download(name: string, content: Blob) {
  const url = URL.createObjectURL(content)
  const link = document.createElement('a')
  link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
