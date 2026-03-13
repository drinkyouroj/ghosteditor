import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { deleteAccount, ApiError } from '../api/client'
import './SettingsPage.css'

interface Props {
  onLogout: () => void
}

export function SettingsPage({ onLogout }: Props) {
  const navigate = useNavigate()
  const [showConfirm, setShowConfirm] = useState(false)
  const [confirmText, setConfirmText] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')

  const handleDelete = async () => {
    if (confirmText !== 'DELETE') return

    setDeleting(true)
    setError('')
    try {
      await deleteAccount()
      onLogout()
      navigate('/login')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to delete account')
      setDeleting(false)
    }
  }

  return (
    <div className="settings-page">
      <h1>Account Settings</h1>

      <section className="settings-section">
        <h2>Data & Privacy</h2>
        <p className="settings-desc">
          Your manuscripts are never used to train AI models. Files are encrypted at rest.
          You can delete your account and all associated data at any time.
        </p>
      </section>

      <section className="settings-section danger-zone">
        <h2>Danger Zone</h2>

        {!showConfirm ? (
          <div className="danger-action">
            <div>
              <strong>Delete account</strong>
              <p>Permanently delete your account, all manuscripts, story bibles, and analysis results.
                This action cannot be undone.</p>
            </div>
            <button className="btn-danger-lg" onClick={() => setShowConfirm(true)}>
              Delete my account
            </button>
          </div>
        ) : (
          <div className="delete-confirm">
            <p className="confirm-warning">
              This will permanently delete:
            </p>
            <ul className="confirm-list">
              <li>Your account and login credentials</li>
              <li>All uploaded manuscript files (removed from storage)</li>
              <li>All story bibles and analysis results</li>
              <li>All job history</li>
            </ul>
            <p className="confirm-instruction">
              Type <strong>DELETE</strong> to confirm:
            </p>
            <input
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="DELETE"
              className="confirm-input"
              autoFocus
            />
            {error && <p className="error-text">{error}</p>}
            <div className="confirm-actions">
              <button
                className="btn-danger-lg"
                onClick={handleDelete}
                disabled={confirmText !== 'DELETE' || deleting}
              >
                {deleting ? 'Deleting...' : 'Permanently delete everything'}
              </button>
              <button
                className="btn-cancel"
                onClick={() => { setShowConfirm(false); setConfirmText('') }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
