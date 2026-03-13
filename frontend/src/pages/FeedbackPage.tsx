import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getManuscriptFeedback,
  type ManuscriptFeedback,
  type ChapterFeedback,
  type Issue,
  ApiError,
} from '../api/client'
import './FeedbackPage.css'

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'severity-critical',
  warning: 'severity-warning',
  note: 'severity-note',
}

const TYPE_LABELS: Record<string, string> = {
  consistency: 'Consistency',
  pacing: 'Pacing',
  character: 'Character',
  plot: 'Plot',
  voice: 'Voice',
  worldbuilding: 'Worldbuilding',
  genre_convention: 'Genre',
}

export function FeedbackPage() {
  const { id } = useParams<{ id: string }>()
  const [feedback, setFeedback] = useState<ManuscriptFeedback | null>(null)
  const [error, setError] = useState('')
  const [activeChapter, setActiveChapter] = useState(0)
  const [severityFilter, setSeverityFilter] = useState<string>('')
  const [typeFilter, setTypeFilter] = useState<string>('')
  const [expandedIssue, setExpandedIssue] = useState<number | null>(null)

  useEffect(() => {
    if (!id) return
    getManuscriptFeedback(id)
      .then((data) => {
        setFeedback(data)
        // Default to first analyzed chapter
        const firstAnalyzed = data.chapters.findIndex((ch) => ch.status === 'analyzed')
        if (firstAnalyzed >= 0) setActiveChapter(firstAnalyzed)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load feedback'))
  }, [id])

  if (error) return <p className="error-text">{error}</p>
  if (!feedback) return <p>Loading analysis results...</p>

  const chapter = feedback.chapters[activeChapter]
  const filteredIssues = chapter
    ? chapter.issues.filter((i) => {
        if (severityFilter && i.severity !== severityFilter) return false
        if (typeFilter && i.type !== typeFilter) return false
        return true
      })
    : []

  return (
    <div className="feedback-page">
      <div className="feedback-header">
        <div>
          <Link to={`/manuscripts/${id}`} className="back-link">Back to manuscript</Link>
          <h1>{feedback.title}</h1>
          {feedback.genre && <span className="feedback-genre">{feedback.genre}</span>}
        </div>
      </div>

      <SummaryBar summary={feedback.summary} />

      <div className="feedback-layout">
        <div className="chapter-tabs-sidebar">
          <h3>Chapters</h3>
          {feedback.chapters.map((ch, idx) => (
            <button
              key={ch.chapter_id}
              className={`chapter-tab ${idx === activeChapter ? 'active' : ''} ${ch.status !== 'analyzed' ? 'pending' : ''}`}
              onClick={() => {
                setActiveChapter(idx)
                setExpandedIssue(null)
              }}
            >
              <span className="ch-num">Ch. {ch.chapter_number}</span>
              <span className="ch-title">{ch.title ?? 'Untitled'}</span>
              {ch.status === 'analyzed' && (
                <span className="ch-counts">
                  {ch.issue_counts.critical > 0 && (
                    <span className="count-critical">{ch.issue_counts.critical}</span>
                  )}
                  {ch.issue_counts.warning > 0 && (
                    <span className="count-warning">{ch.issue_counts.warning}</span>
                  )}
                  {ch.issue_counts.note > 0 && (
                    <span className="count-note">{ch.issue_counts.note}</span>
                  )}
                </span>
              )}
              {ch.status === 'analyzing' && <span className="ch-analyzing">analyzing...</span>}
              {ch.status === 'error' && <span className="ch-error">error</span>}
            </button>
          ))}
        </div>

        <div className="chapter-detail">
          {chapter ? (
            <ChapterDetail
              chapter={chapter}
              issues={filteredIssues}
              severityFilter={severityFilter}
              typeFilter={typeFilter}
              onSeverityChange={setSeverityFilter}
              onTypeChange={setTypeFilter}
              expandedIssue={expandedIssue}
              onToggleIssue={setExpandedIssue}
            />
          ) : (
            <p>Select a chapter to view feedback.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function SummaryBar({ summary }: { summary: ManuscriptFeedback['summary'] }) {
  return (
    <div className="summary-bar">
      <div className="summary-stat">
        <span className="stat-value">{summary.chapters_analyzed}</span>
        <span className="stat-label">of {summary.chapters_total} analyzed</span>
      </div>
      <div className="summary-stat">
        <span className="stat-value">{summary.total_issues}</span>
        <span className="stat-label">issues found</span>
      </div>
      <div className="summary-stat">
        <span className="stat-value stat-critical">{summary.critical}</span>
        <span className="stat-label">critical</span>
      </div>
      <div className="summary-stat">
        <span className="stat-value stat-warning">{summary.warning}</span>
        <span className="stat-label">warnings</span>
      </div>
      <div className="summary-stat">
        <span className="stat-value stat-note">{summary.note}</span>
        <span className="stat-label">notes</span>
      </div>
    </div>
  )
}

function ChapterDetail({
  chapter,
  issues,
  severityFilter,
  typeFilter,
  onSeverityChange,
  onTypeChange,
  expandedIssue,
  onToggleIssue,
}: {
  chapter: ChapterFeedback
  issues: Issue[]
  severityFilter: string
  typeFilter: string
  onSeverityChange: (v: string) => void
  onTypeChange: (v: string) => void
  expandedIssue: number | null
  onToggleIssue: (v: number | null) => void
}) {
  if (chapter.status !== 'analyzed') {
    return (
      <div className="chapter-pending">
        <p>
          {chapter.status === 'analyzing'
            ? 'This chapter is currently being analyzed...'
            : chapter.status === 'error'
              ? 'Analysis failed for this chapter.'
              : 'This chapter has not been analyzed yet.'}
        </p>
      </div>
    )
  }

  return (
    <div>
      <div className="chapter-detail-header">
        <h2>
          Chapter {chapter.chapter_number}
          {chapter.title && <span className="ch-detail-title"> — {chapter.title}</span>}
        </h2>
        {chapter.word_count && (
          <span className="ch-detail-words">{chapter.word_count.toLocaleString()} words</span>
        )}
      </div>

      {/* Pacing summary */}
      {chapter.pacing && <PacingSection pacing={chapter.pacing} />}

      {/* Genre notes */}
      {chapter.genre_notes && <GenreSection notes={chapter.genre_notes} />}

      {/* Issues */}
      <div className="issues-section">
        <div className="issues-header">
          <h3>Issues ({issues.length})</h3>
          <div className="issue-filters">
            <select
              value={severityFilter}
              onChange={(e) => onSeverityChange(e.target.value)}
            >
              <option value="">All severities</option>
              <option value="critical">Critical</option>
              <option value="warning">Warning</option>
              <option value="note">Note</option>
            </select>
            <select
              value={typeFilter}
              onChange={(e) => onTypeChange(e.target.value)}
            >
              <option value="">All types</option>
              <option value="consistency">Consistency</option>
              <option value="pacing">Pacing</option>
              <option value="character">Character</option>
              <option value="plot">Plot</option>
              <option value="voice">Voice</option>
              <option value="worldbuilding">Worldbuilding</option>
              <option value="genre_convention">Genre</option>
            </select>
          </div>
        </div>

        {issues.length === 0 ? (
          <p className="no-issues">
            {severityFilter || typeFilter
              ? 'No issues match the current filters.'
              : 'No issues found in this chapter.'}
          </p>
        ) : (
          <div className="issues-list">
            {issues.map((issue, idx) => (
              <IssueCard
                key={idx}
                issue={issue}
                expanded={expandedIssue === idx}
                onToggle={() => onToggleIssue(expandedIssue === idx ? null : idx)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function IssueCard({
  issue,
  expanded,
  onToggle,
}: {
  issue: Issue
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <div
      className={`issue-card ${SEVERITY_COLORS[issue.severity] ?? ''} ${expanded ? 'expanded' : ''}`}
      onClick={onToggle}
    >
      <div className="issue-row">
        <span className={`issue-severity ${SEVERITY_COLORS[issue.severity] ?? ''}`}>
          {issue.severity}
        </span>
        <span className="issue-type">{TYPE_LABELS[issue.type] ?? issue.type}</span>
        <span className="issue-location">{issue.chapter_location}</span>
        <p className="issue-desc">{issue.description}</p>
      </div>

      {expanded && (
        <div className="issue-detail">
          {issue.original_text && (
            <div className="issue-quote">
              <span className="quote-label">Original text:</span>
              <blockquote>{issue.original_text}</blockquote>
            </div>
          )}
          {issue.suggestion && (
            <div className="issue-suggestion">
              <span className="suggestion-label">Suggestion:</span>
              <p>{issue.suggestion}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function PacingSection({ pacing }: { pacing: ChapterFeedback['pacing'] }) {
  if (!pacing) return null

  return (
    <div className="pacing-section">
      <h3>Pacing</h3>
      <div className="pacing-grid">
        <div className="pacing-item">
          <span className="pacing-label">Scenes</span>
          <span className="pacing-value">{pacing.scene_count}</span>
        </div>
        <div className="pacing-item">
          <span className="pacing-label">Tension</span>
          <span className={`pacing-value tension-${pacing.tension_arc}`}>{pacing.tension_arc}</span>
        </div>
        {pacing.scene_types.length > 0 && (
          <div className="pacing-item pacing-wide">
            <span className="pacing-label">Scene types</span>
            <div className="pacing-tags">
              {pacing.scene_types.map((t) => (
                <span key={t} className="pacing-tag">{t}</span>
              ))}
            </div>
          </div>
        )}
        {pacing.characters_present.length > 0 && (
          <div className="pacing-item pacing-wide">
            <span className="pacing-label">Characters present</span>
            <div className="pacing-tags">
              {pacing.characters_present.map((c) => (
                <span key={c} className="pacing-tag char-tag">{c}</span>
              ))}
            </div>
          </div>
        )}
      </div>
      {pacing.chapter_summary && (
        <p className="pacing-summary">{pacing.chapter_summary}</p>
      )}
    </div>
  )
}

function GenreSection({ notes }: { notes: ChapterFeedback['genre_notes'] }) {
  if (!notes) return null

  return (
    <div className="genre-section">
      <h3>
        Genre Fit
        <span className={`genre-score genre-${notes.genre_fit_score}`}>{notes.genre_fit_score}</span>
      </h3>
      <div className="genre-conventions">
        {notes.conventions_met.length > 0 && (
          <div className="conv-group">
            <span className="conv-label conv-met">Met</span>
            <ul>
              {notes.conventions_met.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </div>
        )}
        {notes.conventions_missed.length > 0 && (
          <div className="conv-group">
            <span className="conv-label conv-missed">Missed</span>
            <ul>
              {notes.conventions_missed.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
