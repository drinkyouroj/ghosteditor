import './LegalPage.css'

export function PrivacyPage() {
  return (
    <div className="legal-page">
      <h1>Privacy Policy</h1>
      <p className="legal-effective">Effective Date: March 13, 2026</p>

      <section>
        <h2>1. Information We Collect</h2>
        <h3>Account Information</h3>
        <p>When you create an account, we collect your email address and a hashed password.</p>
        <h3>Manuscript Data</h3>
        <p>
          When you upload a manuscript, we store the original file and extracted text for
          the purpose of analysis. We also store generated analysis results (story bibles,
          chapter feedback, issue reports).
        </p>
        <h3>Usage Data</h3>
        <p>
          We collect basic usage data including timestamps of uploads, analyses performed,
          and page visits for service improvement and debugging.
        </p>
      </section>

      <section>
        <h2>2. How We Use Your Information</h2>
        <ul>
          <li><strong>Manuscript analysis:</strong> Your uploaded text is sent to the Anthropic Claude API to generate story bibles and editorial feedback</li>
          <li><strong>Account management:</strong> Your email is used for authentication, email verification, and password reset</li>
          <li><strong>Service communication:</strong> We may send transactional emails related to your uploads and account</li>
        </ul>
      </section>

      <section>
        <h2>3. AI Processing</h2>
        <p>
          <strong>Your manuscripts are never used to train AI models.</strong> We use the
          Anthropic Claude API under their commercial API terms, which explicitly state that
          API inputs are not used for model training or improvement.
        </p>
        <p>
          Manuscript text is transmitted to Anthropic's servers via encrypted connection
          (TLS) for analysis and is not retained by Anthropic after the API response is
          returned.
        </p>
      </section>

      <section>
        <h2>4. Data Storage and Security</h2>
        <ul>
          <li>Files are stored in AWS S3 with server-side encryption (AES-256)</li>
          <li>Database records are stored in PostgreSQL with encryption at rest</li>
          <li>Passwords are hashed using bcrypt (never stored in plaintext)</li>
          <li>Authentication tokens are stored in httpOnly, Secure cookies</li>
          <li>All data transmission uses HTTPS/TLS encryption</li>
        </ul>
      </section>

      <section>
        <h2>5. Data Retention</h2>
        <ul>
          <li><strong>Active accounts:</strong> Data is retained as long as your account is active</li>
          <li><strong>Deleted manuscripts:</strong> Files are removed from S3 immediately upon deletion. Database records are purged within 30 days</li>
          <li><strong>Deleted accounts:</strong> All data (files, analysis results, account info) is deleted. S3 files are removed immediately; database records are purged within 30 days</li>
        </ul>
      </section>

      <section>
        <h2>6. Data Sharing</h2>
        <p>We do not sell, rent, or share your personal information or manuscript data with third parties, except:</p>
        <ul>
          <li><strong>Anthropic (Claude API):</strong> Manuscript text is sent for AI analysis under their API data processing terms</li>
          <li><strong>AWS:</strong> Files are stored in S3; AWS does not access file contents</li>
          <li><strong>Legal requirements:</strong> We may disclose data if required by law or legal process</li>
        </ul>
      </section>

      <section>
        <h2>7. Your Rights</h2>
        <p>You have the right to:</p>
        <ul>
          <li><strong>Access:</strong> View all data associated with your account</li>
          <li><strong>Delete:</strong> Delete individual manuscripts or your entire account at any time</li>
          <li><strong>Export:</strong> Download your story bibles and analysis results</li>
          <li><strong>Correction:</strong> Update your email address and password</li>
        </ul>
      </section>

      <section>
        <h2>8. Cookies</h2>
        <p>
          We use essential cookies only: authentication tokens (httpOnly, Secure) required
          for the service to function. We do not use tracking cookies, analytics cookies,
          or third-party advertising cookies.
        </p>
      </section>

      <section>
        <h2>9. Changes to This Policy</h2>
        <p>
          We may update this privacy policy. Changes will be posted on this page with an
          updated effective date. Material changes will be communicated via email.
        </p>
      </section>

      <section>
        <h2>Contact</h2>
        <p>
          Privacy questions? Contact us at privacy@ghosteditor.app
        </p>
      </section>
    </div>
  )
}
