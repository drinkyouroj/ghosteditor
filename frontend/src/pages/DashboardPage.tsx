import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { listManuscripts, deleteManuscript, type Manuscript, ApiError } from '../api/client'
import Spinner from '../components/Spinner'
import './DashboardPage.css'

const STATUS_LABELS: Record<string, string> = {
  uploading: 'Uploading...',
  extracting: 'Extracting text...',
  bible_generating: 'Building story bible...',
  bible_complete: 'Story bible ready',
  analyzing: 'Analyzing chapters...',
  complete: 'Complete',
  error: 'Error',
}

const ERROR_HELP: Record<string, string> = {
  error: 'Something went wrong during processing. You can delete this manuscript and try uploading again.',
}

export function DashboardPage() {
  const [manuscripts, setManuscripts] = useState<Manuscript[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchManuscripts = useCallback(() => {
    setLoading(true)
    listManuscripts()
      .then(setManuscripts)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load manuscripts'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    fetchManuscripts()
  }, [fetchManuscripts])

  const handleDelete = async (id: string, title: string) => {
    if (!confirm(`Delete "${title}" and all its data?`)) return
    try {
      await deleteManuscript(id)
      setManuscripts((prev) => prev.filter((m) => m.id !== id))
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Delete failed')
    }
  }

  if (loading) return <Spinner text="Loading manuscripts..." />
  if (error) return (
    <div className="error-card">
      <p className="error-text">{error}</p>
      <button onClick={() => { setError(null); fetchManuscripts(); }} className="btn-retry">
        Try Again
      </button>
    </div>
  )

  return (
    <div>
      <div className="dashboard-header">
        <h1>Your Manuscripts</h1>
        <Link to="/upload" className="btn-primary">Upload manuscript</Link>
      </div>

      {manuscripts.length === 0 ? (
        <div className="empty-state">
          <h2>No manuscripts yet</h2>
          <p>Upload your first manuscript to get started with AI-powered developmental editing.</p>
          <Link to="/upload" className="btn-primary">Upload Manuscript</Link>
        </div>
      ) : (
        <div className="manuscript-list">
          {manuscripts.map((m) => (
            <div key={m.id} className="manuscript-card">
              <div className="manuscript-info">
                <Link to={`/manuscripts/${m.id}`} className="manuscript-title">
                  {m.title}
                </Link>
                <div className="manuscript-meta">
                  {m.genre && <span>{m.genre}</span>}
                  {m.word_count_est && <span>{m.word_count_est.toLocaleString()} words</span>}
                  {m.chapter_count && <span>{m.chapter_count} chapters</span>}
                  <span className={`status status-${m.status}`}>
                    {STATUS_LABELS[m.status] ?? m.status}
                  </span>
                </div>
              </div>
              <div className="manuscript-actions">
                {m.status === 'complete' && (
                  <Link to={`/manuscripts/${m.id}/feedback`} className="btn-small btn-feedback">
                    View Feedback
                  </Link>
                )}
                {(m.status === 'complete' || m.status === 'bible_complete' || m.status === 'analyzing') && (
                  <Link to={`/manuscripts/${m.id}/bible`} className="btn-small">
                    Story Bible
                  </Link>
                )}
                {m.status === 'error' && (
                  <span className="error-hint" title={ERROR_HELP.error}>
                    {ERROR_HELP.error}
                  </span>
                )}
                <button
                  onClick={() => handleDelete(m.id, m.title)}
                  className="btn-small btn-danger"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
