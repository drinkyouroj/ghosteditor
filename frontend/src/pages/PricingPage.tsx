import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { createCheckoutSession, ApiError } from '../api/client'
import './PricingPage.css'

export function PricingPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [loading, setLoading] = useState<string | null>(null)
  const [error, setError] = useState('')

  const handleCheckout = async (mode: 'payment' | 'subscription') => {
    if (!id) return
    setLoading(mode)
    setError('')
    try {
      const session = await createCheckoutSession(id, mode)
      window.location.href = session.url
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start checkout')
      setLoading(null)
    }
  }

  return (
    <div className="pricing-page">
      <h1>Unlock Full Analysis</h1>
      <p className="pricing-subtitle">
        Your story bible is ready. Pay once or subscribe to analyze all chapters
        with developmental editing feedback.
      </p>

      {error && <div className="pricing-error">{error}</div>}

      <div className="pricing-cards">
        <div className="pricing-card">
          <div className="pricing-card-header">
            <h2>Per Manuscript</h2>
            <div className="pricing-amount">
              <span className="pricing-dollar">$</span>
              <span className="pricing-number">49</span>
            </div>
            <p className="pricing-period">one-time payment</p>
          </div>
          <ul className="pricing-features">
            <li>Full chapter-by-chapter analysis</li>
            <li>Consistency checking against story bible</li>
            <li>Pacing analysis per chapter</li>
            <li>Genre convention scoring</li>
            <li>Up to 100K words</li>
          </ul>
          <button
            className="btn btn-primary pricing-btn"
            onClick={() => handleCheckout('payment')}
            disabled={loading === 'payment'}
          >
            {loading === 'payment' ? 'Redirecting...' : 'Pay $49'}
          </button>
          <p className="pricing-note">Have a beta code? Enter it at checkout.</p>
        </div>

        <div className="pricing-card pricing-card-featured">
          <div className="pricing-badge">Best Value</div>
          <div className="pricing-card-header">
            <h2>Monthly Subscription</h2>
            <div className="pricing-amount">
              <span className="pricing-dollar">$</span>
              <span className="pricing-number">79</span>
              <span className="pricing-interval">/mo</span>
            </div>
            <p className="pricing-period">cancel anytime</p>
          </div>
          <ul className="pricing-features">
            <li>Everything in per-manuscript</li>
            <li>Unlimited manuscripts</li>
            <li>Priority processing</li>
            <li>Active writer discount</li>
          </ul>
          <button
            className="btn btn-primary pricing-btn"
            onClick={() => handleCheckout('subscription')}
            disabled={loading === 'subscription'}
          >
            {loading === 'subscription' ? 'Redirecting...' : 'Subscribe $79/mo'}
          </button>
        </div>
      </div>

      <button className="btn-back" onClick={() => navigate(`/manuscripts/${id}`)}>
        Back to manuscript
      </button>
    </div>
  )
}
