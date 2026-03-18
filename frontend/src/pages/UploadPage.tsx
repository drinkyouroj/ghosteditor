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
  'Detecting sections': 'Detecting sections...',
  'Generating story bible': 'Building story bible...',
  'Generating argument map': 'Building argument map...',
}

type DocumentType = 'fiction' | 'nonfiction'

const NONFICTION_FORMATS = [
  { value: 'academic', label: 'Academic Paper', description: 'Thesis-driven research with citations and formal structure' },
  { value: 'personal_essay', label: 'Personal Essay / Memoir', description: 'First-person narrative exploring personal experience' },
  { value: 'journalism', label: 'Journalism / Reporting', description: 'Investigative or explanatory reporting on real events' },
  { value: 'self_help', label: 'Self-Help / How-To', description: 'Practical guidance with actionable advice for the reader' },
  { value: 'business', label: 'Business / Professional', description: 'Strategy, leadership, or industry analysis for professionals' },
] as const

export function UploadPage() {
  const navigate = useNavigate()
  const fileRef = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const [documentType, setDocumentType] = useState<DocumentType>('fiction')
  const [genre, setGenre] = useState('')
  const [nonfictionFormat, setNonfictionFormat] = useState('')
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
      const result = await uploadManuscript(file, title, {
        document_type: documentType,
        genre: documentType === 'fiction' ? genre || undefined : undefined,
        nonfiction_format: documentType === 'nonfiction' ? nonfictionFormat || undefined : undefined,
      })
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

        if (status.status === 'completed') {
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

  const subtitleText = documentType === 'nonfiction'
    ? 'Upload your manuscript to get an argument map and developmental analysis.'
    : 'Upload your manuscript to get a story bible and developmental analysis.'

  return (
    <div>
      <h1>Upload Manuscript</h1>
      <p className="upload-subtitle">
        {subtitleText}{' '}
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
              placeholder={documentType === 'nonfiction' ? 'My Research Paper' : 'My Great Novel'}
            />
          </label>

          <fieldset className="document-type-fieldset">
            <legend>Document type</legend>
            <div className="document-type-options">
              <label className={`document-type-radio ${documentType === 'fiction' ? 'selected' : ''}`}>
                <input
                  type="radio"
                  name="documentType"
                  value="fiction"
                  checked={documentType === 'fiction'}
                  onChange={() => setDocumentType('fiction')}
                />
                <span className="radio-label">Fiction / Novel</span>
              </label>
              <label className={`document-type-radio ${documentType === 'nonfiction' ? 'selected' : ''}`}>
                <input
                  type="radio"
                  name="documentType"
                  value="nonfiction"
                  checked={documentType === 'nonfiction'}
                  onChange={() => setDocumentType('nonfiction')}
                />
                <span className="radio-label">Nonfiction / Essay</span>
              </label>
            </div>
          </fieldset>

          {documentType === 'fiction' ? (
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
          ) : (
            <label>
              Format (optional)
              <select value={nonfictionFormat} onChange={(e) => setNonfictionFormat(e.target.value)}>
                <option value="">Select format...</option>
                {NONFICTION_FORMATS.map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
              {nonfictionFormat && (
                <span className="format-description">
                  {NONFICTION_FORMATS.find((f) => f.value === nonfictionFormat)?.description}
                </span>
              )}
            </label>
          )}

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
