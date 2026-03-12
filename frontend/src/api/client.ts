const BASE = ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, body.detail ?? 'Request failed')
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

// --- Auth ---

export interface User {
  id: string
  email: string
  is_provisional: boolean
  email_verified: boolean
}

export function register(email: string) {
  return request<{ message: string }>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export function login(email: string, password: string) {
  return request<User>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export function completeRegistration(password: string, tos_accepted: boolean) {
  return request<User>('/auth/complete-registration', {
    method: 'POST',
    body: JSON.stringify({ password, tos_accepted }),
  })
}

export function getMe() {
  return request<User>('/auth/me')
}

export function logout() {
  return request<{ message: string }>('/auth/logout', { method: 'POST' })
}

// --- Manuscripts ---

export interface Manuscript {
  id: string
  title: string
  genre: string | null
  status: string
  payment_status: string
  chapter_count: number | null
  word_count_est: number | null
  created_at: string
}

export interface ChapterSummary {
  id: string
  chapter_number: number
  title: string | null
  word_count: number | null
  status: string
}

export interface ManuscriptDetail extends Manuscript {
  chapters: ChapterSummary[]
}

export interface UploadResult {
  manuscript_id: string
  status: string
  job_id: string
}

export interface JobStatus {
  id: string
  status: string
  progress_pct: number
  current_step: string | null
  error_message: string | null
}

export function listManuscripts() {
  return request<Manuscript[]>('/manuscripts')
}

export function getManuscript(id: string) {
  return request<ManuscriptDetail>(`/manuscripts/${id}`)
}

export function deleteManuscript(id: string) {
  return request<void>(`/manuscripts/${id}`, { method: 'DELETE' })
}

export function uploadManuscript(file: File, title: string, genre?: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('title', title)
  if (genre) form.append('genre', genre)

  return fetch('/manuscripts/upload', {
    method: 'POST',
    credentials: 'include',
    body: form,
  }).then(async (res) => {
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, body.detail ?? 'Upload failed')
    }
    return res.json() as Promise<UploadResult>
  })
}

export function getJobStatus(jobId: string) {
  return request<JobStatus>(`/manuscripts/jobs/${jobId}`)
}

// --- Story Bible ---

export interface StoryBible {
  manuscript_id: string
  version: number
  bible: Record<string, unknown>
  updated_at: string
}

export function getStoryBible(manuscriptId: string) {
  return request<StoryBible>(`/bible/${manuscriptId}`)
}
