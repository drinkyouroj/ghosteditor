import { useState, useRef, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadManuscript, getJobStatus, ApiError } from '../api/client'
import './UploadPage.css'

const ACCEPTED = '.docx,.txt,.pdf'
const POLL_INTERVAL = 5000

const STEP_LABELS: Record<string, string> = {
  'Queued for text extraction': 'Uploading file...',
  'Extracting text': 'Extracting text...',
  'Detecting chapters': 'Detecting chapters...',
  'Generating story bible': 'Building story bible...',
}

export function UploadPage() {
  const navigate = useNavigate()
  const fileRef = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const [genre, setGenre] = useState('')
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [jobStep, setJobStep] = useState('')
  const [jobProgress, setJobProgress] = useState(0)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setError('Please select a file')
      return
    }

    setError('')
    setUploading(true)
    setJobStep('Uploading file...')

    try {
      const result = await uploadManuscript(file, title, genre || undefined)
      pollJob(result.job_id, result.manuscript_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload failed')
      setUploading(false)
      setJobStep('')
    }
  }

  const pollJob = (jobId: string, manuscriptId: string) => {
    const interval = setInterval(async () => {
      try {
        const status = await getJobStatus(jobId)
        setJobStep(STEP_LABELS[status.current_step ?? ''] ?? status.current_step ?? '')
        setJobProgress(status.progress_pct)

        if (status.status === 'complete') {
          clearInterval(interval)
          navigate(`/manuscripts/${manuscriptId}`)
        } else if (status.status === 'failed') {
          clearInterval(interval)
          setError(status.error_message ?? 'Processing failed')
          setUploading(false)
          setJobStep('')
        }
      } catch {
        // keep polling on transient errors
      }
    }, POLL_INTERVAL)
  }

  return (
    <div>
      <h1>Upload Manuscript</h1>
      <p className="upload-subtitle">
        Upload your manuscript to get a story bible and developmental analysis.
        Accepted formats: .docx, .txt, .pdf (max 10MB).
      </p>

      {uploading ? (
        <div className="upload-progress">
          <div className="progress-step">{jobStep}</div>
          <div className="progress-bar-track">
            <div className="progress-bar-fill" style={{ width: `${jobProgress}%` }} />
          </div>
          <p className="progress-note">This may take a minute. You can close this tab and come back.</p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="upload-form">
          {error && <div className="auth-error">{error}</div>}
          <label>
            Title
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              placeholder="My Great Novel"
            />
          </label>
          <label>
            Genre (optional)
            <select value={genre} onChange={(e) => setGenre(e.target.value)}>
              <option value="">Select genre...</option>
              <option value="romance">Romance</option>
              <option value="fantasy">Fantasy / Science Fiction</option>
              <option value="mystery">Mystery / Thriller</option>
              <option value="literary">Literary Fiction</option>
              <option value="horror">Horror</option>
              <option value="historical">Historical Fiction</option>
              <option value="ya">Young Adult</option>
              <option value="other">Other</option>
            </select>
          </label>
          <label>
            Manuscript file
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPTED}
              required
            />
          </label>
          <button type="submit" className="btn-primary">Upload and analyze</button>
        </form>
      )}
    </div>
  )
}
