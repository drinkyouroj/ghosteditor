import { useEffect, useState, useRef } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import { getManuscript, type ManuscriptDetail, ApiError } from '../api/client'
import './ManuscriptPage.css'

const STATUS_LABELS: Record<string, string> = {
  uploading: 'Uploading...',
  extracting: 'Extracting text...',
  bible_generating: 'Building story bible...',
  bible_complete: 'Story bible ready',
  analyzing: 'Analyzing chapters...',
  complete: 'Analysis complete',
  error: 'Error',
}

const CHAPTER_STATUS_LABELS: Record<string, string> = {
  uploaded: 'Pending',
  extracting: 'Extracting...',
  extracted: 'Extracted',
  analyzing: 'Analyzing...',
  analyzed: 'Complete',
  error: 'Error',
}

const PROCESSING_STATUSES = ['uploading', 'extracting', 'bible_generating', 'analyzing']
const POLL_INTERVAL = 5000

export function ManuscriptPage() {
  const { id } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const [manuscript, setManuscript] = useState<ManuscriptDetail | null>(null)
  const [error, setError] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const paymentStatus = searchParams.get('payment')

  const fetchManuscript = () => {
    if (!id) return
    getManuscript(id)
      .then(setManuscript)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load'))
  }

  useEffect(() => {
    fetchManuscript()
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [id])

  // Auto-refresh while manuscript is processing
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current)

    if (manuscript && PROCESSING_STATUSES.includes(manuscript.status)) {
      pollRef.current = setInterval(fetchManuscript, POLL_INTERVAL)
    }

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [manuscript?.status])

  if (error) return <p className="error-text">{error}</p>
  if (!manuscript) return <p>Loading...</p>

  const isProcessing = PROCESSING_STATUSES.includes(manuscript.status)
  const analyzedCount = manuscript.chapters.filter((ch) => ch.status === 'analyzed').length
  const totalChapters = manuscript.chapters.length

  return (
    <div>
      <div className="ms-header">
        <div>
          <Link to="/dashboard" className="back-link">Back to dashboard</Link>
          <h1>{manuscript.title}</h1>
          <div className="ms-meta">
            {manuscript.genre && <span>{manuscript.genre}</span>}
            {manuscript.word_count_est && (
              <span>{manuscript.word_count_est.toLocaleString()} words</span>
            )}
            {totalChapters > 0 && <span>{totalChapters} chapters</span>}
          </div>
        </div>
        <div className="ms-actions">
          {manuscript.status === 'complete' && (
            <Link to={`/manuscripts/${id}/feedback`} className="btn-primary">
              View Feedback
            </Link>
          )}
          {(manuscript.status === 'complete' || manuscript.status === 'bible_complete' || manuscript.status === 'analyzing') && (
            <Link to={`/manuscripts/${id}/bible`} className="btn-secondary">
              Story Bible
            </Link>
          )}
        </div>
      </div>

      {/* Payment success banner */}
      {paymentStatus === 'success' && (
        <div className="ms-payment-success">
          Payment successful! Your manuscript is now being analyzed.
        </div>
      )}

      {/* Paywall prompt — bible complete but unpaid */}
      {manuscript.status === 'bible_complete' && manuscript.payment_status === 'unpaid' && (
        <div className="ms-paywall">
          <h3>Your story bible is ready!</h3>
          <p>
            Unlock full chapter-by-chapter developmental editing analysis:
            consistency checks, pacing feedback, and genre convention scoring.
          </p>
          <Link to={`/manuscripts/${id}/pricing`} className="btn-primary">
            Unlock Full Analysis
          </Link>
        </div>
      )}

      {/* Progress indicator for processing manuscripts */}
      {isProcessing && (
        <div className="ms-progress">
          <div className="progress-status">
            <span className="progress-dot" />
            <span>{STATUS_LABELS[manuscript.status] ?? manuscript.status}</span>
          </div>
          {manuscript.status === 'analyzing' && totalChapters > 0 && (
            <div className="progress-detail">
              <div className="progress-bar-track">
                <div
                  className="progress-bar-fill"
                  style={{ width: `${Math.round((analyzedCount / totalChapters) * 100)}%` }}
                />
              </div>
              <span className="progress-count">
                {analyzedCount} of {totalChapters} chapters analyzed
              </span>
            </div>
          )}
        </div>
      )}

      {/* Error state */}
      {manuscript.status === 'error' && (
        <div className="ms-error">
          <p>Something went wrong during processing.</p>
          <p className="error-help">
            You can delete this manuscript and try uploading again. If the problem persists,
            check that your file is a valid .docx, .txt, or .pdf document.
          </p>
        </div>
      )}

      {manuscript.chapters.length > 0 && (
        <div className="chapter-list">
          <h2>Chapters</h2>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Title</th>
                <th>Words</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {manuscript.chapters.map((ch) => (
                <tr key={ch.id} className={ch.status === 'error' ? 'row-error' : ''}>
                  <td>{ch.chapter_number}</td>
                  <td>{ch.title ?? 'Untitled'}</td>
                  <td>{ch.word_count?.toLocaleString() ?? '-'}</td>
                  <td>
                    <span className={`ch-status ch-status-${ch.status}`}>
                      {CHAPTER_STATUS_LABELS[ch.status] ?? ch.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
