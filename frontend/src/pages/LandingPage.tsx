import { Link } from 'react-router-dom'
import './LandingPage.css'

export function LandingPage() {
  return (
    <div className="landing">
      {/* Header */}
      <header className="landing-header">
        <span className="landing-logo">GhostEditor</span>
        <nav className="landing-nav">
          <a href="#features">Features</a>
          <a href="#pricing">Pricing</a>
          <Link to="/login">Log in</Link>
          <Link to="/register" className="btn-primary btn-sm">Try Free</Link>
        </nav>
      </header>

      {/* Hero */}
      <section className="hero">
        <div className="hero-content">
          <h1>AI Developmental Editing for Self-Published Authors</h1>
          <p className="hero-subtitle">
            Upload your manuscript. Get a structured story bible and chapter-by-chapter feedback
            on consistency, pacing, and genre conventions — for $49 instead of $5,000.
          </p>
          <div className="hero-cta">
            <Link to="/register" className="btn-primary btn-lg">
              Try Free — No Credit Card
            </Link>
            <span className="hero-note">Free story bible from Chapter 1</span>
          </div>
        </div>
      </section>

      {/* Social Proof */}
      <section className="social-proof">
        <p>Developmental editing typically costs <strong>$3,000–$8,000</strong> per manuscript.</p>
        <p>GhostEditor catches the same structural issues at a fraction of the cost.</p>
      </section>

      {/* Features */}
      <section id="features" className="features">
        <h2>What GhostEditor Does</h2>
        <div className="feature-grid">
          <div className="feature-card">
            <div className="feature-icon">&#128214;</div>
            <h3>Story Bible Generation</h3>
            <p>
              Upload your manuscript and get an organized breakdown of every character,
              relationship, setting, timeline event, and world rule — automatically.
            </p>
          </div>
          <div className="feature-card">
            <div className="feature-icon">&#128269;</div>
            <h3>Consistency Checking</h3>
            <p>
              GhostEditor reads every chapter against your story bible and flags contradictions:
              changed eye colors, timeline impossibilities, forgotten character traits.
            </p>
          </div>
          <div className="feature-card">
            <div className="feature-icon">&#128200;</div>
            <h3>Pacing Analysis</h3>
            <p>
              Scene-by-scene tension mapping, character presence tracking, and chapter-level
              pacing feedback to keep your readers turning pages.
            </p>
          </div>
          <div className="feature-card">
            <div className="feature-icon">&#127942;</div>
            <h3>Genre Convention Scoring</h3>
            <p>
              8 genre templates (romance, thriller, fantasy, mystery, sci-fi, literary, horror,
              historical) score each chapter on expected reader beats.
            </p>
          </div>
        </div>
      </section>

      {/* How It Works */}
      <section className="how-it-works">
        <h2>How It Works</h2>
        <div className="steps">
          <div className="step">
            <div className="step-number">1</div>
            <h3>Upload Your Manuscript</h3>
            <p>DOCX, PDF, or TXT. Up to 100,000 words. Drag and drop.</p>
          </div>
          <div className="step-arrow">&#8594;</div>
          <div className="step">
            <div className="step-number">2</div>
            <h3>Get Your Free Story Bible</h3>
            <p>Characters, timeline, settings, voice profile — organized from Chapter 1.</p>
          </div>
          <div className="step-arrow">&#8594;</div>
          <div className="step">
            <div className="step-number">3</div>
            <h3>Unlock Full Analysis</h3>
            <p>Pay $49 and get chapter-by-chapter developmental feedback in minutes.</p>
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="landing-pricing">
        <h2>Simple Pricing</h2>
        <div className="pricing-row">
          <div className="price-card">
            <h3>Per Manuscript</h3>
            <div className="price">$49</div>
            <p className="price-desc">one-time</p>
            <ul>
              <li>Full story bible</li>
              <li>Chapter-by-chapter analysis</li>
              <li>Consistency, pacing, genre checks</li>
              <li>Up to 100K words</li>
            </ul>
            <Link to="/register" className="btn-primary">Get Started</Link>
          </div>
          <div className="price-card price-card-highlight">
            <div className="price-label">Best Value</div>
            <h3>Monthly</h3>
            <div className="price">$79<span>/mo</span></div>
            <p className="price-desc">unlimited manuscripts</p>
            <ul>
              <li>Everything in per-manuscript</li>
              <li>Unlimited manuscripts</li>
              <li>Cancel anytime</li>
            </ul>
            <Link to="/register" className="btn-primary">Get Started</Link>
          </div>
        </div>
        <p className="beta-note">
          Beta users: enter code <strong>BETA</strong> at checkout for $20 off your first manuscript.
        </p>
      </section>

      {/* Trust */}
      <section className="trust">
        <h2>Your Manuscripts Are Safe</h2>
        <div className="trust-grid">
          <div className="trust-item">
            <strong>Never used for AI training</strong>
            <p>Your manuscripts are analyzed via Anthropic's commercial API and never used to train models.</p>
          </div>
          <div className="trust-item">
            <strong>Delete anytime</strong>
            <p>One click removes your manuscript, story bible, and all analysis from our servers.</p>
          </div>
          <div className="trust-item">
            <strong>You own everything</strong>
            <p>Full copyright retention. We make no IP claims on your work or our analysis output.</p>
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="final-cta">
        <h2>Ready to level up your manuscript?</h2>
        <p>Upload Chapter 1 and get your free story bible in under 2 minutes.</p>
        <Link to="/register" className="btn-primary btn-lg">Try Free — No Credit Card</Link>
      </section>

      {/* Footer */}
      <footer className="landing-footer">
        <div className="landing-footer-links">
          <Link to="/terms">Terms of Service</Link>
          <Link to="/privacy">Privacy Policy</Link>
        </div>
        <p className="landing-footer-copy">GhostEditor</p>
      </footer>
    </div>
  )
}
