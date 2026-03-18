import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getArgumentMap, type ArgumentMap, type ArgumentThread, type EvidenceEntry, ApiError } from '../api/client'
import Spinner from '../components/Spinner'
import './ArgumentMapPage.css'

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
          Structure ({argMap.structural_markers?.length ?? 0})
        </button>
      </div>

      <div className="argmap-content">
        {tab === 'thesis' && <ThesisTab thesis={argMap.central_thesis} />}
        {tab === 'arguments' && <ArgumentsTab threads={argMap.argument_threads ?? []} />}
        {tab === 'evidence' && (
          <EvidenceTab
            entries={argMap.evidence_log ?? []}
            condensed={argMap.evidence_log_condensed}
          />
        )}
        {tab === 'voice' && <VoiceTab profile={argMap.voice_profile} />}
        {tab === 'structure' && <StructureTab markers={argMap.structural_markers ?? []} />}
      </div>
    </div>
  )
}

function ThesisTab({ thesis }: { thesis: string }) {
  return (
    <div className="thesis-display">
      <div className="thesis-card">
        <h3>Central Thesis</h3>
        <p className="thesis-text">{thesis}</p>
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
      {threads.map((thread, i) => (
        <div key={i} className="argument-card">
          <div className="argument-header">
            <span className={`thread-status ${THREAD_STATUS_COLORS[thread.status] ?? ''}`}>
              {THREAD_STATUS_LABELS[thread.status] ?? thread.status}
            </span>
            <span className="argument-section">Section {thread.introduced_section}</span>
            {thread.evidence_ids.length > 0 && (
              <span className="argument-evidence-count">
                {thread.evidence_ids.length} evidence
              </span>
            )}
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
                <th>Linked Claim</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td>
                    <span className="evidence-type-badge">{entry.type}</span>
                  </td>
                  <td>{entry.section}</td>
                  <td>{entry.summary}</td>
                  <td className="evidence-claim">{entry.linked_claim}</td>
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
    <div className="voice-card">
      <h3>Voice Profile</h3>
      <dl className="voice-dl">
        <dt>POV</dt><dd>{profile.pov}</dd>
        <dt>Tense</dt><dd>{profile.tense}</dd>
        <dt>Tone</dt><dd>{profile.tone}</dd>
        <dt>Register</dt><dd>{profile.register}</dd>
        {profile.style_notes && <><dt>Style Notes</dt><dd>{profile.style_notes}</dd></>}
      </dl>
    </div>
  )
}

function StructureTab({ markers }: { markers: ArgumentMap['structural_markers'] }) {
  if (!markers || markers.length === 0) {
    return <p className="empty-tab">No structural markers found.</p>
  }

  return (
    <div className="structure-list">
      {markers.map((marker, i) => (
        <div key={i} className="structure-card">
          <div className="structure-header">
            <span className="structure-type-badge">{marker.type}</span>
            <span className="structure-section">Section {marker.section}</span>
          </div>
          <p>{marker.description}</p>
        </div>
      ))}
    </div>
  )
}
