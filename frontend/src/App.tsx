import { Routes, Route, Navigate } from 'react-router-dom'
import { useState, useEffect, useCallback } from 'react'
import { Layout } from './components/Layout'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { DashboardPage } from './pages/DashboardPage'
import { UploadPage } from './pages/UploadPage'
import { ManuscriptPage } from './pages/ManuscriptPage'
import { BiblePage } from './pages/BiblePage'
import { FeedbackPage } from './pages/FeedbackPage'
import { SettingsPage } from './pages/SettingsPage'
import { TermsPage } from './pages/TermsPage'
import { PrivacyPage } from './pages/PrivacyPage'
import { PricingPage } from './pages/PricingPage'
import { LandingPage } from './pages/LandingPage'
import { getMe, type User } from './api/client'

export function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const checkAuth = useCallback(async () => {
    try {
      const me = await getMe()
      setUser(me)
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    checkAuth()
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        checkAuth()
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [checkAuth])

  if (loading) {
    return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading...</div>
  }

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/dashboard" /> : <LoginPage onLogin={setUser} />} />
      <Route path="/register" element={user ? <Navigate to="/dashboard" /> : <RegisterPage />} />
      <Route path="/terms" element={<TermsPage />} />
      <Route path="/privacy" element={<PrivacyPage />} />
      <Route path="/" element={user ? <Navigate to="/dashboard" /> : <LandingPage />} />
      <Route element={<Layout user={user} onLogout={() => setUser(null)} />}>
        <Route path="/dashboard" element={user ? <DashboardPage /> : <Navigate to="/login" />} />
        <Route path="/upload" element={user ? <UploadPage /> : <Navigate to="/login" />} />
        <Route path="/manuscripts/:id" element={user ? <ManuscriptPage /> : <Navigate to="/login" />} />
        <Route path="/manuscripts/:id/bible" element={user ? <BiblePage /> : <Navigate to="/login" />} />
        <Route path="/manuscripts/:id/feedback" element={user ? <FeedbackPage /> : <Navigate to="/login" />} />
        <Route path="/manuscripts/:id/pricing" element={user ? <PricingPage /> : <Navigate to="/login" />} />
        <Route path="/settings" element={user ? <SettingsPage onLogout={() => setUser(null)} /> : <Navigate to="/login" />} />
      </Route>
    </Routes>
  )
}
