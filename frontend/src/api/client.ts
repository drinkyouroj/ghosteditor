const BASE = ''

export const TIMEOUT_DEFAULT = 30_000
export const TIMEOUT_UPLOAD = 120_000

let refreshPromise: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  try {
    await fetchWithTimeout(`${BASE}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    })
    return true
  } catch {
    return false
  }
}

async function fetchWithTimeout(
  url: string,
  init?: RequestInit & { timeout?: number },
): Promise<Response> {
  const { timeout = TIMEOUT_DEFAULT, ...fetchInit } = init ?? {}
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    return await fetch(url, { ...fetchInit, signal: controller.signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError(0, 'Request timed out. Please check your connection and try again.')
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

async function request<T>(path: string, init?: RequestInit, _retried = false): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    if (res.status === 401 && !_retried) {
      if (!refreshPromise) {
        refreshPromise = refreshAccessToken().finally(() => { refreshPromise = null })
      }
      const refreshed = await refreshPromise
      if (refreshed) {
        return request<T>(path, init, true)
      }
      window.location.href = '/login'
      throw new ApiError(401, 'Session expired')
    }
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
  document_type: 'fiction' | 'nonfiction'
  nonfiction_format: string | null
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
  updated_at: string
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

export interface UploadOptions {
  document_type?: 'fiction' | 'nonfiction'
  genre?: string
  nonfiction_format?: string
}

export async function uploadManuscript(file: File, title: string, options?: UploadOptions, _retried = false): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('title', title)
  if (options?.document_type) form.append('document_type', options.document_type)
  if (options?.genre) form.append('genre', options.genre)
  if (options?.nonfiction_format) form.append('nonfiction_format', options.nonfiction_format)

  const res = await fetchWithTimeout('/manuscripts/upload', {
    method: 'POST',
    credentials: 'include',
    body: form,
    timeout: TIMEOUT_UPLOAD,
  })
  if (!res.ok) {
    if (res.status === 401 && !_retried) {
      if (!refreshPromise) {
        refreshPromise = refreshAccessToken().finally(() => { refreshPromise = null })
      }
      const refreshed = await refreshPromise
      if (refreshed) {
        return uploadManuscript(file, title, options, true)
      }
      window.location.href = '/login'
      throw new ApiError(401, 'Session expired')
    }
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, body.detail ?? 'Upload failed')
  }
  return res.json() as Promise<UploadResult>
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

// --- Argument Map (Nonfiction) ---

export interface ArgumentThread {
  id: string
  claim: string
  status: 'open' | 'supported' | 'unresolved' | 'abandoned'
  first_seen_section: number
}

export interface EvidenceEntry {
  type: string
  section: number
  summary: string
  supports_claim_id: string | null
}

export interface NonfictionVoiceProfile {
  register: 'academic' | 'conversational' | 'journalistic' | 'authoritative' | 'intimate'
  pov: 'first' | 'second' | 'third' | 'mixed'
  notable_patterns: string[]
}

export interface StructuralMarkers {
  has_explicit_thesis: boolean
  has_conclusion: boolean | null
  section_count: number
}

export interface ArgumentMapData {
  central_thesis: string | null
  claimed_audience: string | null
  argument_threads: ArgumentThread[]
  evidence_log: EvidenceEntry[]
  voice_profile: NonfictionVoiceProfile
  structural_markers: StructuralMarkers
  evidence_log_condensed?: boolean
}

export interface ArgumentMap extends ArgumentMapData {
  manuscript_id: string
  version: number
  warnings: string[]
  updated_at: string
}

interface ArgumentMapResponse {
  manuscript_id: string
  version: number
  argument_map: ArgumentMapData
  warnings: string[]
  updated_at: string
}

export async function getArgumentMap(manuscriptId: string): Promise<ArgumentMap> {
  const res = await request<ArgumentMapResponse>(`/argument-map/${manuscriptId}`)
  return {
    ...res.argument_map,
    manuscript_id: res.manuscript_id,
    version: res.version,
    warnings: res.warnings,
    updated_at: res.updated_at,
  }
}

// --- Nonfiction Feedback ---

export interface NonfictionDocumentSummary {
  overall_assessment: string
  thesis_clarity_score: 'weak' | 'developing' | 'clear' | 'strong'
  argument_coherence: 'fragmented' | 'inconsistent' | 'mostly_coherent' | 'coherent'
  evidence_density: 'sparse' | 'uneven' | 'adequate' | 'strong'
  tone_consistency: 'inconsistent' | 'mostly_consistent' | 'consistent'
  top_strengths: string[]
  top_priorities: string[]
  format_specific_notes: string | null
}

export interface NonfictionFeedback extends ManuscriptFeedback {
  document_summary: NonfictionDocumentSummary | null
}

export async function getNonfictionFeedback(manuscriptId: string, filters?: { severity?: string; issue_type?: string }): Promise<NonfictionFeedback> {
  const params = new URLSearchParams()
  if (filters?.severity) params.set('severity', filters.severity)
  if (filters?.issue_type) params.set('issue_type', filters.issue_type)
  const qs = params.toString()
  // Backend returns "sections" and "sections_analyzed/sections_total" — normalize
  // to "chapters" and "chapters_analyzed/chapters_total" for the shared FeedbackPage
  const raw = await request<Record<string, unknown>>(`/argument-map/${manuscriptId}/feedback${qs ? `?${qs}` : ''}`)
  const summary = raw.summary as Record<string, unknown> ?? {}
  return {
    manuscript_id: raw.manuscript_id as string,
    title: raw.title as string,
    genre: (raw.nonfiction_format as string) ?? null,
    status: raw.status as string,
    summary: {
      total_issues: (summary.total_issues as number) ?? 0,
      critical: (summary.critical as number) ?? 0,
      warning: (summary.warning as number) ?? 0,
      note: (summary.note as number) ?? 0,
      chapters_analyzed: (summary.sections_analyzed as number) ?? 0,
      chapters_total: (summary.sections_total as number) ?? 0,
    },
    chapters: ((raw.sections as Record<string, unknown>[]) ?? []).map((section) => {
      const issues = ((section.issues as Record<string, unknown>[]) ?? []).map((issue) => ({
        type: (issue.dimension as string) ?? (issue.type as string) ?? '',
        severity: (issue.severity as string) ?? 'note',
        chapter_location: (issue.location as string) ?? (issue.chapter_location as string) ?? '',
        description: (issue.description as string) ?? '',
        original_text: (issue.original_text as string) ?? null,
        suggestion: (issue.suggestion as string) ?? '',
      }))
      return {
        ...section,
        issues,
      } as ChapterFeedback
    }),
    document_summary: (raw.document_summary as NonfictionDocumentSummary) ?? null,
  }
}
