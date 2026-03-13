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

export function deleteAccount() {
  return request<{ message: string }>('/auth/account', { method: 'DELETE' })
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

// --- Analysis ---

export function startAnalysis(manuscriptId: string) {
  return request<{ message: string; chapters_queued: number }>(`/manuscripts/${manuscriptId}/analyze`, {
    method: 'POST',
  })
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

// --- Analysis Feedback ---

export interface Issue {
  type: string
  severity: string
  chapter_location: string
  description: string
  original_text: string | null
  suggestion: string
}

export interface PacingAnalysis {
  scene_count: number
  scene_types: string[]
  tension_arc: string
  characters_present: string[]
  chapter_summary: string
}

export interface GenreNotes {
  conventions_met: string[]
  conventions_missed: string[]
  genre_fit_score: string
}

export interface ChapterFeedback {
  chapter_id: string
  chapter_number: number
  title: string | null
  word_count: number | null
  status: string
  issues: Issue[]
  issue_counts: { critical: number; warning: number; note: number }
  pacing: PacingAnalysis | null
  genre_notes: GenreNotes | null
}

export interface FeedbackSummary {
  total_issues: number
  critical: number
  warning: number
  note: number
  chapters_analyzed: number
  chapters_total: number
}

export interface ManuscriptFeedback {
  manuscript_id: string
  title: string
  genre: string | null
  status: string
  summary: FeedbackSummary
  chapters: ChapterFeedback[]
}

export function getManuscriptFeedback(manuscriptId: string, filters?: { severity?: string; issue_type?: string }) {
  const params = new URLSearchParams()
  if (filters?.severity) params.set('severity', filters.severity)
  if (filters?.issue_type) params.set('issue_type', filters.issue_type)
  const qs = params.toString()
  return request<ManuscriptFeedback>(`/bible/${manuscriptId}/feedback${qs ? `?${qs}` : ''}`)
}

// --- Stripe Payments ---

export interface CheckoutSession {
  url: string
  session_id: string
}

export interface Subscription {
  status: string
  current_period_end: string | null
  cancel_at_period_end: boolean
}

export function createCheckoutSession(manuscriptId: string, mode: 'payment' | 'subscription' = 'payment') {
  return request<CheckoutSession>('/stripe/create-checkout-session', {
    method: 'POST',
    body: JSON.stringify({ manuscript_id: manuscriptId, mode }),
  })
}

export function getSubscription() {
  return request<Subscription>('/stripe/subscription')
}

export function cancelSubscription() {
  return request<{ message: string }>('/stripe/cancel-subscription', { method: 'POST' })
}
