import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getManuscriptFeedback,
  type ManuscriptFeedback,
  type NonfictionFeedback,
  type NonfictionDocumentSummary,
  type ChapterFeedback,
  type Issue,
  ApiError,
} from '../api/client'
import Spinner from '../components/Spinner'
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

const NONFICTION_TYPE_LABELS: Record<string, string> = {
  argument: 'Argument',
  evidence: 'Evidence',
  clarity: 'Clarity',
  structure: 'Structure',
  tone: 'Tone',
}

const NONFICTION_DIMENSION_FILTERS = [
  { value: '', label: 'All dimensions' },
  { value: 'argument', label: 'Argument' },
  { value: 'evidence', label: 'Evidence' },
  { value: 'clarity', label: 'Clarity' },
  { value: 'structure', label: 'Structure' },
  { value: 'tone', label: 'Tone' },
]

const SECTION_DETECTION_LABELS: Record<string, string> = {
  headers: 'Detected by headers',
  auto: 'Auto-chunked',
}

type NonfictionProgressStep = 'analyzing_sections' | 'generating_summary' | 'complete'

function getNonfictionProgressStep(feedback: ManuscriptFeedback): NonfictionProgressStep {
  if (feedback.status === 'complete') return 'complete'
  const allAnalyzed = feedback.chapters.every((ch) => ch.status === 'analyzed')
  if (allAnalyzed) return 'generating_summary'
  return 'analyzing_sections'
}

export function FeedbackPage() {
  const { id } = useParams<{ id: string }>()
  const [feedback, setFeedback] = useState<ManuscriptFeedback | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeChapter, setActiveChapter] = useState(0)
  const [severityFilter, setSeverityFilter] = useState<string>('')
  const [typeFilter, setTypeFilter] = useState<string>('')
  const [expandedIssue, setExpandedIssue] = useState<number | null>(null)

  const fetchFeedback = useCallback(() => {
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

  useEffect(() => {
    fetchFeedback()
  }, [fetchFeedback])

  if (error) return (
    <div className="error-card">
      <p className="error-text">{error}</p>
      <button onClick={() => { setError(null); fetchFeedback(); }} className="btn-retry">
        Try Again
      </button>
    </div>
  )
  if (!feedback) return <Spinner text="Loading analysis results..." />

  // Detect nonfiction mode from response shape
  const nfFeedback = feedback as NonfictionFeedback
  const isNonfiction = nfFeedback.document_summary != null

  const chapter = feedback.chapters[activeChapter]
  const filteredIssues = chapter
    ? chapter.issues.filter((i) => {
        if (severityFilter && i.severity !== severityFilter) return false
        if (typeFilter && i.type !== typeFilter) return false
        return true
      })
    : []

  const typeLabels = isNonfiction ? { ...TYPE_LABELS, ...NONFICTION_TYPE_LABELS } : TYPE_LABELS
  const sidebarLabel = isNonfiction ? 'Sections' : 'Chapters'
  const itemPrefix = isNonfiction ? 'Sec.' : 'Ch.'

  return (
    <div className="feedback-page">
      <div className="feedback-header">
        <div>
          <Link to={`/manuscripts/${id}`} className="back-link">Back to manuscript</Link>
          <h1>{feedback.title}</h1>
          {feedback.genre && <span className="feedback-genre">{feedback.genre}</span>}
        </div>
      </div>

      {/* Nonfiction progress indicator */}
      {isNonfiction && feedback.status !== 'complete' && (
        <NonfictionProgressIndicator step={getNonfictionProgressStep(feedback)} />
      )}

      {/* Nonfiction document summary */}
      {isNonfiction && nfFeedback.document_summary && (
        <NonfictionSummaryPanel summary={nfFeedback.document_summary} />
      )}

      <SummaryBar summary={feedback.summary} />

      <div className="feedback-layout">
        <div className="chapter-tabs-sidebar">
          <h3>{sidebarLabel}</h3>
          {feedback.chapters.map((ch, idx) => (
            <button
              key={ch.chapter_id}
              className={`chapter-tab ${idx === activeChapter ? 'active' : ''} ${ch.status !== 'analyzed' ? 'pending' : ''}`}
              onClick={() => {
                setActiveChapter(idx)
                setExpandedIssue(null)
              }}
            >
              <span className="ch-num">{itemPrefix} {ch.chapter_number}</span>
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
              isNonfiction={isNonfiction}
              typeLabels={typeLabels}
            />
          ) : (
            <p>Select a {isNonfiction ? 'section' : 'chapter'} to view feedback.</p>
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
  isNonfiction = false,
  typeLabels = TYPE_LABELS,
}: {
  chapter: ChapterFeedback
  issues: Issue[]
  severityFilter: string
  typeFilter: string
  onSeverityChange: (v: string) => void
  onTypeChange: (v: string) => void
  expandedIssue: number | null
  onToggleIssue: (v: number | null) => void
  isNonfiction?: boolean
  typeLabels?: Record<string, string>
}) {
  const unitLabel = isNonfiction ? 'Section' : 'Chapter'

  if (chapter.status !== 'analyzed') {
    return (
      <div className="chapter-pending">
        <p>
          {chapter.status === 'analyzing'
            ? `This ${unitLabel.toLowerCase()} is currently being analyzed...`
            : chapter.status === 'error'
              ? `Analysis failed for this ${unitLabel.toLowerCase()}.`
              : `This ${unitLabel.toLowerCase()} has not been analyzed yet.`}
        </p>
      </div>
    )
  }

  // Section detection method for nonfiction
  const sectionDetection = isNonfiction
    ? ((chapter as ChapterFeedback & { detection_method?: string }).detection_method ?? 'auto')
    : null

  return (
    <div>
      <div className="chapter-detail-header">
        <div>
          <h2>
            {unitLabel} {chapter.chapter_number}
            {chapter.title && <span className="ch-detail-title"> -- {chapter.title}</span>}
          </h2>
          {isNonfiction && sectionDetection && (
            <span className={`detection-badge detection-${sectionDetection}`}>
              {SECTION_DETECTION_LABELS[sectionDetection] ?? sectionDetection}
            </span>
          )}
        </div>
        {chapter.word_count && (
          <span className="ch-detail-words">{chapter.word_count.toLocaleString()} words</span>
        )}
      </div>

      {/* Pacing summary (fiction only) */}
      {!isNonfiction && chapter.pacing && <PacingSection pacing={chapter.pacing} />}

      {/* Genre notes (fiction only) */}
      {!isNonfiction && chapter.genre_notes && <GenreSection notes={chapter.genre_notes} />}

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
            {isNonfiction ? (
              <div className="nf-dimension-tabs">
                {NONFICTION_DIMENSION_FILTERS.map((d) => (
                  <button
                    key={d.value}
                    className={`nf-dim-tab ${typeFilter === d.value ? 'active' : ''}`}
                    onClick={() => onTypeChange(d.value)}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
            ) : (
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
            )}
          </div>
        </div>

        {issues.length === 0 ? (
          <p className="no-issues">
            {severityFilter || typeFilter
              ? 'No issues match the current filters.'
              : `No issues found in this ${unitLabel.toLowerCase()}.`}
          </p>
        ) : (
          <div className="issues-list">
            {issues.map((issue, idx) => (
              <IssueCard
                key={idx}
                issue={issue}
                expanded={expandedIssue === idx}
                onToggle={() => onToggleIssue(expandedIssue === idx ? null : idx)}
                typeLabels={typeLabels}
              />
            ))}
          </div>
        )}

        {/* Issue cap indicator */}
        {(chapter as ChapterFeedback & { issues_capped?: boolean }).issues_capped && (
          <p className="issues-capped-note">
            Showing top 15 issues by severity. Additional issues were found but truncated.
          </p>
        )}
      </div>
    </div>
  )
}

function IssueCard({
  issue,
  expanded,
  onToggle,
  typeLabels = TYPE_LABELS,
}: {
  issue: Issue
  expanded: boolean
  onToggle: () => void
  typeLabels?: Record<string, string>
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
        <span className="issue-type">{typeLabels[issue.type] ?? issue.type}</span>
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

/* --- Nonfiction-specific components --- */

function NonfictionProgressIndicator({ step }: { step: NonfictionProgressStep }) {
  const steps: { key: NonfictionProgressStep; label: string }[] = [
    { key: 'analyzing_sections', label: 'Analyzing sections' },
    { key: 'generating_summary', label: 'Generating summary' },
    { key: 'complete', label: 'Complete' },
  ]

  const currentIdx = steps.findIndex((s) => s.key === step)

  return (
    <div className="nf-progress">
      {steps.map((s, idx) => (
        <div
          key={s.key}
          className={`nf-progress-step ${idx <= currentIdx ? 'active' : ''} ${idx === currentIdx ? 'current' : ''}`}
        >
          <span className="nf-progress-dot">{idx < currentIdx ? '\u2713' : idx + 1}</span>
          <span className="nf-progress-label">{s.label}</span>
          {idx < steps.length - 1 && <span className="nf-progress-connector" />}
        </div>
      ))}
    </div>
  )
}

const SCORE_LEVEL_CLASS: Record<string, string> = {
  // thesis_clarity_score levels
  strong: 'score-high',
  clear: 'score-high',
  developing: 'score-mid',
  weak: 'score-low',
  // argument_coherence levels
  coherent: 'score-high',
  mostly_coherent: 'score-high',
  inconsistent: 'score-mid',
  fragmented: 'score-low',
  // evidence_density levels
  adequate: 'score-high',
  uneven: 'score-mid',
  sparse: 'score-low',
  // tone_consistency levels
  consistent: 'score-high',
  mostly_consistent: 'score-mid',
  // 'inconsistent' already covered above
}

const SCORE_DISPLAY_LABELS: Record<string, string> = {
  weak: 'Weak',
  developing: 'Developing',
  clear: 'Clear',
  strong: 'Strong',
  fragmented: 'Fragmented',
  inconsistent: 'Inconsistent',
  mostly_coherent: 'Mostly Coherent',
  coherent: 'Coherent',
  sparse: 'Sparse',
  uneven: 'Uneven',
  adequate: 'Adequate',
  mostly_consistent: 'Mostly Consistent',
  consistent: 'Consistent',
}

// Maps categorical values to a 4-step (or 3-step) position for the progress indicator
const SCORE_STEP_MAP: Record<string, { step: number; total: number }> = {
  weak: { step: 1, total: 4 },
  developing: { step: 2, total: 4 },
  clear: { step: 3, total: 4 },
  strong: { step: 4, total: 4 },
  fragmented: { step: 1, total: 4 },
  inconsistent: { step: 2, total: 4 },
  mostly_coherent: { step: 3, total: 4 },
  coherent: { step: 4, total: 4 },
  sparse: { step: 1, total: 4 },
  uneven: { step: 2, total: 4 },
  adequate: { step: 3, total: 4 },
  // 'strong' already mapped above, shared for evidence_density
  mostly_consistent: { step: 2, total: 3 },
  consistent: { step: 3, total: 3 },
  // 'inconsistent' already mapped above, shared for tone_consistency
}

function NonfictionSummaryPanel({ summary }: { summary: NonfictionDocumentSummary }) {
  const scores: { key: keyof Pick<NonfictionDocumentSummary, 'thesis_clarity_score' | 'argument_coherence' | 'evidence_density' | 'tone_consistency'>; label: string }[] = [
    { key: 'thesis_clarity_score', label: 'Thesis Clarity' },
    { key: 'argument_coherence', label: 'Argument Coherence' },
    { key: 'evidence_density', label: 'Evidence Density' },
    { key: 'tone_consistency', label: 'Tone Consistency' },
  ]

  return (
    <div className="nf-summary-panel">
      <h3>Document Summary</h3>
      <p className="nf-assessment">{summary.overall_assessment}</p>

      <div className="nf-scores">
        {scores.map((s) => {
          const value = summary[s.key]
          const levelClass = SCORE_LEVEL_CLASS[value] ?? 'score-mid'
          const displayLabel = SCORE_DISPLAY_LABELS[value] ?? value
          const stepInfo = SCORE_STEP_MAP[value] ?? { step: 1, total: 4 }

          return (
            <div key={s.key} className="nf-score-item">
              <span className="nf-score-label">{s.label}</span>
              <div className="nf-score-steps">
                {Array.from({ length: stepInfo.total }, (_, i) => (
                  <span
                    key={i}
                    className={`nf-score-step-dot ${i < stepInfo.step ? levelClass : ''}`}
                  />
                ))}
              </div>
              <span className={`nf-score-badge ${levelClass}`}>{displayLabel}</span>
            </div>
          )
        })}
      </div>

      {summary.top_strengths.length > 0 && (
        <div className="nf-tags-group">
          <span className="nf-tags-label">Strengths</span>
          <div className="nf-tags">
            {summary.top_strengths.map((s, i) => (
              <span key={i} className="nf-tag nf-tag-strength">{s}</span>
            ))}
          </div>
        </div>
      )}

      {summary.top_priorities.length > 0 && (
        <div className="nf-tags-group">
          <span className="nf-tags-label">Priorities</span>
          <div className="nf-tags">
            {summary.top_priorities.map((p, i) => (
              <span key={i} className="nf-tag nf-tag-priority">{p}</span>
            ))}
          </div>
        </div>
      )}

      {summary.format_specific_notes && (
        <div className="nf-format-notes">
          <span className="nf-format-notes-label">Format Notes</span>
          <p>{summary.format_specific_notes}</p>
        </div>
      )}
    </div>
  )
}
