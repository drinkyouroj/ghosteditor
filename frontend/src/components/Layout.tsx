import { Link, Outlet, useNavigate } from 'react-router-dom'
import { logout, type User } from '../api/client'
import './Layout.css'

interface Props {
  user: User | null
  onLogout: () => void
}

export function Layout({ user, onLogout }: Props) {
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    onLogout()
    navigate('/login')
  }

  return (
    <div className="layout">
      <header className="header">
        <Link to="/dashboard" className="logo">GhostEditor</Link>
        <nav className="nav">
          {user && (
            <>
              <Link to="/dashboard">Dashboard</Link>
              <Link to="/upload">Upload</Link>
              <Link to="/settings">Settings</Link>
              <span className="nav-email">{user.email}</span>
              <button onClick={handleLogout} className="btn-link">Log out</button>
            </>
          )}
        </nav>
      </header>
      <main className="main">
        <Outlet />
      </main>
      <footer className="footer">
        <Link to="/terms">Terms of Service</Link>
        <Link to="/privacy">Privacy Policy</Link>
      </footer>
    </div>
  )
}
