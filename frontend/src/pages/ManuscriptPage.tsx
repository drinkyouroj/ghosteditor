import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getManuscript, type ManuscriptDetail, ApiError } from '../api/client'
import './ManuscriptPage.css'

export function ManuscriptPage() {
  const { id } = useParams<{ id: string }>()
  const [manuscript, setManuscript] = useState<ManuscriptDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!id) return
    getManuscript(id)
      .then(setManuscript)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load'))
  }, [id])

  if (error) return <p className="error-text">{error}</p>
  if (!manuscript) return <p>Loading...</p>

  return (
    <div>
      <div className="ms-header">
        <div>
          <h1>{manuscript.title}</h1>
          <div className="ms-meta">
            {manuscript.genre && <span>{manuscript.genre}</span>}
            {manuscript.word_count_est && (
              <span>{manuscript.word_count_est.toLocaleString()} words</span>
            )}
            <span className={`status status-${manuscript.status}`}>{manuscript.status}</span>
          </div>
        </div>
        {(manuscript.status === 'complete' || manuscript.status === 'generating_bible') && (
          <Link to={`/manuscripts/${id}/bible`} className="btn-primary">
            View Story Bible
          </Link>
        )}
      </div>

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
                <tr key={ch.id}>
                  <td>{ch.chapter_number}</td>
                  <td>{ch.title ?? 'Untitled'}</td>
                  <td>{ch.word_count?.toLocaleString() ?? '-'}</td>
                  <td className={`status status-${ch.status}`}>{ch.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
