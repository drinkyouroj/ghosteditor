import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getStoryBible, type StoryBible, ApiError } from '../api/client'
import Spinner from '../components/Spinner'
import './BiblePage.css'

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

interface Character {
  name: string
  aliases?: string[]
  description: string
  role: string
  traits?: string[]
  physical?: { age?: string; gender?: string; appearance?: string }
  relationships?: { to: string; type: string }[]
}

interface TimelineEvent {
  event: string
  chapter: number
  date_in_story?: string
  characters_involved?: string[]
}

interface Setting {
  name: string
  description: string
  chapter_introduced: number
}

interface VoiceProfile {
  pov: string
  tense: string
  tone: string
  style_notes?: string
}

interface PlotThread {
  thread: string
  status: string
  introduced_chapter: number
}

interface BibleData {
  characters: Character[]
  timeline: TimelineEvent[]
  settings: Setting[]
  world_rules: string[]
  voice_profile: VoiceProfile
  plot_threads: PlotThread[]
}

export function BiblePage() {
  const { id } = useParams<{ id: string }>()
  const [bible, setBible] = useState<StoryBible | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'characters' | 'timeline' | 'settings' | 'voice' | 'plot'>('characters')

  const fetchBible = useCallback(() => {
    if (!id) return
    getStoryBible(id)
      .then(setBible)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load'))
  }, [id])

  useEffect(() => {
    fetchBible()
  }, [fetchBible])

  if (error) return (
    <div className="error-card">
      <p className="error-text">{error}</p>
      <button onClick={() => { setError(null); fetchBible(); }} className="btn-retry">
        Try Again
      </button>
    </div>
  )
  if (!bible) return <Spinner text="Loading story bible..." />

  const data = bible.bible as unknown as BibleData
  const warnings: string[] = (bible as unknown as { warnings?: string[] }).warnings ?? []

  return (
    <div>
      <div className="bible-header">
        <div>
          <Link to={`/manuscripts/${id}`} className="back-link">Back to manuscript</Link>
          <h1>Story Bible</h1>
          <p className="bible-meta">Version {bible.version} &middot; Updated {new Date(bible.updated_at).toLocaleDateString()}</p>
        </div>
      </div>

      <DriftWarningBanner warnings={warnings} />

      <div className="bible-tabs">
        <button className={tab === 'characters' ? 'active' : ''} onClick={() => setTab('characters')}>
          Characters ({data.characters?.length ?? 0})
        </button>
        <button className={tab === 'timeline' ? 'active' : ''} onClick={() => setTab('timeline')}>
          Timeline ({data.timeline?.length ?? 0})
        </button>
        <button className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')}>
          Settings ({data.settings?.length ?? 0})
        </button>
        <button className={tab === 'voice' ? 'active' : ''} onClick={() => setTab('voice')}>
          Voice
        </button>
        <button className={tab === 'plot' ? 'active' : ''} onClick={() => setTab('plot')}>
          Plot Threads ({data.plot_threads?.length ?? 0})
        </button>
      </div>

      <div className="bible-content">
        {tab === 'characters' && <CharactersTab characters={data.characters ?? []} />}
        {tab === 'timeline' && <TimelineTab events={data.timeline ?? []} />}
        {tab === 'settings' && <SettingsTab settings={data.settings ?? []} />}
        {tab === 'voice' && <VoiceTab profile={data.voice_profile} rules={data.world_rules ?? []} />}
        {tab === 'plot' && <PlotTab threads={data.plot_threads ?? []} />}
      </div>
    </div>
  )
}

function CharactersTab({ characters }: { characters: Character[] }) {
  const grouped = {
    protagonist: characters.filter((c) => c.role === 'protagonist'),
    supporting: characters.filter((c) => c.role === 'supporting'),
    minor: characters.filter((c) => c.role === 'minor' || c.role === 'mentioned'),
  }

  const renderGroup = (title: string, chars: Character[]) => {
    if (chars.length === 0) return null
    return (
      <div className="char-group">
        <h3>{title}</h3>
        {chars.map((c) => (
          <div key={c.name} className="char-card">
            <div className="char-name">
              {c.name}
              {c.aliases && c.aliases.length > 0 && (
                <span className="char-aliases"> ({c.aliases.join(', ')})</span>
              )}
            </div>
            <p className="char-desc">{c.description}</p>
            {c.traits && c.traits.length > 0 && (
              <div className="char-traits">
                {c.traits.map((t) => <span key={t} className="trait-tag">{t}</span>)}
              </div>
            )}
            {c.relationships && c.relationships.length > 0 && (
              <div className="char-rels">
                {c.relationships.map((r) => (
                  <span key={`${r.to}-${r.type}`} className="rel-tag">{r.type} of {r.to}</span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div>
      {renderGroup('Protagonists', grouped.protagonist)}
      {renderGroup('Supporting Characters', grouped.supporting)}
      {renderGroup('Minor / Mentioned', grouped.minor)}
    </div>
  )
}

function TimelineTab({ events }: { events: TimelineEvent[] }) {
  return (
    <div className="timeline-list">
      {events.map((ev, i) => (
        <div key={i} className="timeline-item">
          <div className="timeline-dot" />
          <div>
            <p className="timeline-event">{ev.event}</p>
            <div className="timeline-meta">
              <span>Ch. {ev.chapter}</span>
              {ev.date_in_story && <span>{ev.date_in_story}</span>}
              {ev.characters_involved && <span>{ev.characters_involved.join(', ')}</span>}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function SettingsTab({ settings }: { settings: Setting[] }) {
  return (
    <div className="settings-list">
      {settings.map((s) => (
        <div key={s.name} className="setting-card">
          <h3>{s.name}</h3>
          <p>{s.description}</p>
          <span className="setting-ch">Introduced Ch. {s.chapter_introduced}</span>
        </div>
      ))}
    </div>
  )
}

function VoiceTab({ profile, rules }: { profile: VoiceProfile; rules: string[] }) {
  return (
    <div>
      <div className="voice-card">
        <h3>Voice Profile</h3>
        <dl className="voice-dl">
          <dt>POV</dt><dd>{profile.pov}</dd>
          <dt>Tense</dt><dd>{profile.tense}</dd>
          <dt>Tone</dt><dd>{profile.tone}</dd>
          {profile.style_notes && <><dt>Style Notes</dt><dd>{profile.style_notes}</dd></>}
        </dl>
      </div>
      {rules.length > 0 && (
        <div className="voice-card">
          <h3>World Rules</h3>
          <ul className="rules-list">
            {rules.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

function PlotTab({ threads }: { threads: PlotThread[] }) {
  return (
    <div className="plot-list">
      {threads.map((t, i) => (
        <div key={i} className="plot-card">
          <div className="plot-header">
            <span className={`plot-status plot-${t.status}`}>{t.status}</span>
            <span className="plot-ch">Introduced Ch. {t.introduced_chapter}</span>
          </div>
          <p>{t.thread}</p>
        </div>
      ))}
    </div>
  )
}
