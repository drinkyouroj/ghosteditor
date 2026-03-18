import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getArgumentMap, type ArgumentMap, type ArgumentThread, type EvidenceEntry, ApiError } from '../api/client'
import Spinner from '../components/Spinner'
import './ArgumentMapPage.css'

function DriftWarningBanner({ warnings }: { warnings: string[] }) {
  const [dismissed, setDismissed] = useState(false)
  if (dismissed || warnings.length === 0) return null

  return (
    <div className="drift-warning-banner">
      <div className="drift-warning-content">
        {warnings.map((w, i) => (
          <p key={i} className="drift-warning-text">{w}</p>
        ))}
      </div>
      <button className="drift-warning-dismiss" onClick={() => setDismissed(true)}>
        Dismiss
      </button>
    </div>
  )
}

const THREAD_STATUS_COLORS: Record<string, string> = {
  open: 'thread-open',
  supported: 'thread-supported',
  unresolved: 'thread-unresolved',
  abandoned: 'thread-abandoned',
}

const THREAD_STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  supported: 'Supported',
  unresolved: 'Unresolved',
  abandoned: 'Abandoned',
}

type Tab = 'thesis' | 'arguments' | 'evidence' | 'voice' | 'structure'

export function ArgumentMapPage() {
  const { id } = useParams<{ id: string }>()
  const [argMap, setArgMap] = useState<ArgumentMap | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('thesis')

  const fetchArgMap = useCallback(() => {
    if (!id) return
    getArgumentMap(id)
      .then(setArgMap)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load argument map'))
  }, [id])

  useEffect(() => {
    fetchArgMap()
  }, [fetchArgMap])

  if (error) return (
    <div className="error-card">
      <p className="error-text">{error}</p>
      <button onClick={() => { setError(null); fetchArgMap(); }} className="btn-retry">
        Try Again
      </button>
    </div>
  )
  if (!argMap) return <Spinner text="Loading argument map..." />

  const warnings: string[] = argMap.warnings ?? []

  return (
    <div>
      <div className="argmap-header">
        <div>
          <Link to={`/manuscripts/${id}`} className="back-link">Back to manuscript</Link>
          <h1>Argument Map</h1>
          <p className="argmap-meta">
            Version {argMap.version} &middot; Updated {new Date(argMap.updated_at).toLocaleDateString()}
          </p>
        </div>
      </div>

      <DriftWarningBanner warnings={warnings} />

      <div className="argmap-tabs">
        <button className={tab === 'thesis' ? 'active' : ''} onClick={() => setTab('thesis')}>
          Central Thesis
        </button>
        <button className={tab === 'arguments' ? 'active' : ''} onClick={() => setTab('arguments')}>
          Arguments ({argMap.argument_threads?.length ?? 0})
        </button>
        <button className={tab === 'evidence' ? 'active' : ''} onClick={() => setTab('evidence')}>
          Evidence ({argMap.evidence_log?.length ?? 0})
        </button>
        <button className={tab === 'voice' ? 'active' : ''} onClick={() => setTab('voice')}>
          Voice
        </button>
        <button className={tab === 'structure' ? 'active' : ''} onClick={() => setTab('structure')}>
          Structure
        </button>
      </div>

      <div className="argmap-content">
        {tab === 'thesis' && <ThesisTab thesis={argMap.central_thesis} />}
        {tab === 'arguments' && <ArgumentsTab threads={argMap.argument_threads ?? []} />}
        {tab === 'evidence' && (
          <EvidenceTab
            entries={argMap.evidence_log ?? []}
            condensed={argMap.evidence_log_condensed ?? false}
          />
        )}
        {tab === 'voice' && <VoiceTab profile={argMap.voice_profile} />}
        {tab === 'structure' && <StructureTab markers={argMap.structural_markers} />}
      </div>
    </div>
  )
}

function ThesisTab({ thesis }: { thesis: string | null }) {
  return (
    <div className="thesis-display">
      <div className="thesis-card">
        <h3>Central Thesis</h3>
        <p className="thesis-text">{thesis ?? 'No central thesis identified yet.'}</p>
      </div>
    </div>
  )
}

function ArgumentsTab({ threads }: { threads: ArgumentThread[] }) {
  if (threads.length === 0) {
    return <p className="empty-tab">No argument threads found.</p>
  }

  return (
    <div className="arguments-list">
      {threads.map((thread) => (
        <div key={thread.id} className="argument-card">
          <div className="argument-header">
            <span className={`thread-status ${THREAD_STATUS_COLORS[thread.status] ?? ''}`}>
              {THREAD_STATUS_LABELS[thread.status] ?? thread.status}
            </span>
            <span className="argument-section">Section {thread.first_seen_section}</span>
          </div>
          <p className="argument-claim">{thread.claim}</p>
        </div>
      ))}
    </div>
  )
}

function EvidenceTab({ entries, condensed }: { entries: EvidenceEntry[]; condensed: boolean }) {
  return (
    <div>
      {condensed && (
        <div className="evidence-condensed-notice">
          Evidence log was condensed due to volume. Showing summarized entries.
        </div>
      )}
      {entries.length === 0 ? (
        <p className="empty-tab">No evidence entries found.</p>
      ) : (
        <div className="evidence-table-wrapper">
          <table className="evidence-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Section</th>
                <th>Summary</th>
                <th>Supports Claim</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, idx) => (
                <tr key={idx}>
                  <td>
                    <span className="evidence-type-badge">{entry.type}</span>
                  </td>
                  <td>{entry.section}</td>
                  <td>{entry.summary}</td>
                  <td className="evidence-claim">{entry.supports_claim_id ?? '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function VoiceTab({ profile }: { profile: ArgumentMap['voice_profile'] }) {
  if (!profile) return <p className="empty-tab">No voice profile available.</p>

  return (
    <div>
      <div className="voice-card">
        <h3>Voice Profile</h3>
        <dl className="voice-dl">
          <dt>Register</dt><dd>{profile.register}</dd>
          <dt>POV</dt><dd>{profile.pov}</dd>
        </dl>
      </div>
      {profile.notable_patterns.length > 0 && (
        <div className="voice-card">
          <h3>Notable Patterns</h3>
          <ul className="rules-list">
            {profile.notable_patterns.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

function StructureTab({ markers }: { markers: ArgumentMap['structural_markers'] }) {
  if (!markers) return <p className="empty-tab">No structural markers available.</p>

  return (
    <div className="structure-summary-card">
      <div className="structure-card">
        <h3>Document Structure</h3>
        <dl className="voice-dl">
          <dt>Explicit Thesis</dt>
          <dd>
            <span className={`structure-indicator ${markers.has_explicit_thesis ? 'indicator-yes' : 'indicator-no'}`}>
              {markers.has_explicit_thesis ? 'Yes' : 'No'}
            </span>
          </dd>
          <dt>Conclusion</dt>
          <dd>
            <span className={`structure-indicator ${markers.has_conclusion === true ? 'indicator-yes' : markers.has_conclusion === false ? 'indicator-no' : 'indicator-unknown'}`}>
              {markers.has_conclusion === true ? 'Yes' : markers.has_conclusion === false ? 'No' : 'Unknown'}
            </span>
          </dd>
          <dt>Section Count</dt>
          <dd>{markers.section_count}</dd>
        </dl>
      </div>
    </div>
  )
}
