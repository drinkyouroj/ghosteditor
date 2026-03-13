import './LegalPage.css'

export function TermsPage() {
  return (
    <div className="legal-page">
      <h1>Terms of Service</h1>
      <p className="legal-effective">Effective Date: March 13, 2026</p>

      <section>
        <h2>1. Acceptance of Terms</h2>
        <p>
          By creating an account or using GhostEditor, you agree to these Terms of Service.
          If you do not agree, do not use the service.
        </p>
      </section>

      <section>
        <h2>2. Service Description</h2>
        <p>
          GhostEditor is an AI-powered developmental editing tool for fiction manuscripts.
          The service analyzes uploaded manuscripts to generate story bibles and identify
          potential issues including character inconsistencies, pacing problems, and
          genre convention gaps.
        </p>
      </section>

      <section>
        <h2>3. Intellectual Property</h2>
        <p>
          <strong>You retain full copyright and ownership of all manuscripts you upload.</strong> GhostEditor
          does not claim any rights to your content. Analysis results (story bibles, feedback,
          issue reports) are derivative works based on your content and belong to you.
        </p>
      </section>

      <section>
        <h2>4. AI and Data Use</h2>
        <p>
          <strong>Your manuscripts are never used to train, fine-tune, or improve any AI model.</strong> We
          use the Anthropic Claude API to analyze your text. Per Anthropic's API terms, content
          submitted through the API is not used for model training.
        </p>
        <p>
          Your manuscript text is sent to the Claude API solely for the purpose of generating
          your analysis. It is not stored by Anthropic after processing.
        </p>
      </section>

      <section>
        <h2>5. Data Storage and Security</h2>
        <p>
          Uploaded files are stored encrypted at rest using AWS S3 server-side encryption.
          Database records are stored in PostgreSQL with encryption at rest.
          Authentication uses secure, httpOnly cookies with no client-side token storage.
        </p>
      </section>

      <section>
        <h2>6. Account Deletion</h2>
        <p>
          You may delete your account at any time from the Settings page. When you delete
          your account:
        </p>
        <ul>
          <li>All manuscript files are immediately removed from storage</li>
          <li>All story bibles, analysis results, and associated data are deleted</li>
          <li>Your account credentials are permanently removed</li>
          <li>Database records are purged within 30 days</li>
        </ul>
      </section>

      <section>
        <h2>7. Acceptable Use</h2>
        <p>You agree not to:</p>
        <ul>
          <li>Upload content you do not own or have rights to</li>
          <li>Attempt to extract or reverse-engineer the AI prompts or analysis logic</li>
          <li>Use the service to generate content that violates any laws</li>
          <li>Attempt to circumvent rate limits or usage restrictions</li>
        </ul>
      </section>

      <section>
        <h2>8. Limitation of Liability</h2>
        <p>
          GhostEditor provides automated analysis suggestions and is not a substitute for
          professional editorial services. Analysis results may contain inaccuracies.
          We are not liable for any decisions made based on the service's output.
        </p>
      </section>

      <section>
        <h2>9. Service Availability</h2>
        <p>
          We strive for high availability but do not guarantee uninterrupted service.
          We may modify, suspend, or discontinue the service with reasonable notice.
        </p>
      </section>

      <section>
        <h2>10. Changes to Terms</h2>
        <p>
          We may update these terms. Continued use after changes constitutes acceptance.
          Material changes will be communicated via email.
        </p>
      </section>

      <section>
        <h2>Contact</h2>
        <p>
          Questions about these terms? Contact us at support@ghosteditor.app
        </p>
      </section>
    </div>
  )
}
