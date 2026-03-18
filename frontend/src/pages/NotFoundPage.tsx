import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div style={{ textAlign: 'center', padding: '4rem 2rem' }}>
      <h1 style={{ fontSize: '3rem', marginBottom: '0.5rem', color: 'var(--color-text-secondary)' }}>404</h1>
      <p style={{ fontSize: '1.1rem', marginBottom: '1.5rem', color: 'var(--color-text-secondary)' }}>Page not found</p>
      <Link to="/dashboard">Back to Dashboard</Link>
    </div>
  )
}
