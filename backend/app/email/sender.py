"""Email sending via Resend API.

Handles all transactional email: verification, password reset, story bible ready,
and the 3-email drip sequence for converting free users to paid.

Per blueprint: Use Resend (free tier: 3,000 emails/mo). Email sequences stored
as scheduled tasks in PostgreSQL (EmailEvent table).
"""

from __future__ import annotations

import html
import logging

import resend

from app.config import settings

logger = logging.getLogger(__name__)

FROM_EMAIL = "GhostEditor <noreply@ghosteditor.com>"


def _send(to: str, subject: str, html: str) -> str | None:
    """Send an email via Resend. Returns message ID or None on failure."""
    if not settings.resend_api_key:
        logger.warning(f"Email not sent (no RESEND_API_KEY): to={to}, subject={subject}")
        return None

    resend.api_key = settings.resend_api_key
    try:
        result = resend.Emails.send({
            "from": FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        logger.info(f"Email sent: to={to}, subject={subject}, id={result.get('id')}")
        return result.get("id")
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return None


def send_verification_email(to: str, verification_url: str) -> str | None:
    """Send email verification link after registration."""
    safe_url = html.escape(verification_url, quote=True)
    return _send(
        to=to,
        subject="Verify your GhostEditor account",
        html=f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto;">
            <h2 style="font-size: 1.25rem;">Welcome to GhostEditor</h2>
            <p>Click below to verify your email address and get started:</p>
            <a href="{safe_url}"
               style="display: inline-block; padding: 12px 24px; background: #2563eb; color: white;
                      text-decoration: none; border-radius: 6px; font-weight: 500;">
                Verify Email
            </a>
            <p style="color: #6b7280; font-size: 0.85rem; margin-top: 1.5rem;">
                If you didn't create a GhostEditor account, you can ignore this email.
            </p>
        </div>
        """,
    )


def send_password_reset_email(to: str, reset_url: str) -> str | None:
    """Send password reset link."""
    safe_url = html.escape(reset_url, quote=True)
    return _send(
        to=to,
        subject="Reset your GhostEditor password",
        html=f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto;">
            <h2 style="font-size: 1.25rem;">Password Reset</h2>
            <p>Click below to reset your password:</p>
            <a href="{safe_url}"
               style="display: inline-block; padding: 12px 24px; background: #2563eb; color: white;
                      text-decoration: none; border-radius: 6px; font-weight: 500;">
                Reset Password
            </a>
            <p style="color: #6b7280; font-size: 0.85rem; margin-top: 1.5rem;">
                This link expires in 1 hour. If you didn't request a reset, ignore this email.
            </p>
        </div>
        """,
    )


def send_bible_ready_email(to: str, manuscript_title: str, bible_url: str) -> str | None:
    """Send notification that story bible is ready (retention hook).

    Per blueprint: 'Email delivered: Your GhostEditor story bible is ready'
    """
    safe_title = html.escape(manuscript_title)
    safe_url = html.escape(bible_url, quote=True)
    return _send(
        to=to,
        subject=f"Your story bible for \"{safe_title}\" is ready",
        html=f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto;">
            <h2 style="font-size: 1.25rem;">Your Story Bible is Ready</h2>
            <p>GhostEditor has analyzed the first chapter of <strong>{safe_title}</strong>
               and built your story bible — a structured breakdown of characters, timeline,
               settings, and voice profile.</p>
            <a href="{safe_url}"
               style="display: inline-block; padding: 12px 24px; background: #2563eb; color: white;
                      text-decoration: none; border-radius: 6px; font-weight: 500;">
                View Story Bible
            </a>
            <p style="color: #6b7280; font-size: 0.85rem; margin-top: 1.5rem;">
                Ready for full chapter-by-chapter analysis? Unlock it from your dashboard.
            </p>
        </div>
        """,
    )


def send_drip_email_1(to: str, manuscript_title: str, bible_url: str) -> str | None:
    """Drip email 1 — sent 2 hours after bible generated (no payment).

    Per blueprint: 'Here's what GhostEditor found in your first chapter'
    """
    safe_title = html.escape(manuscript_title)
    safe_url = html.escape(bible_url, quote=True)
    return _send(
        to=to,
        subject=f"Here's what GhostEditor found in \"{safe_title}\"",
        html=f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto;">
            <h2 style="font-size: 1.25rem;">Your First Chapter Analysis</h2>
            <p>GhostEditor built a story bible from Chapter 1 of <strong>{safe_title}</strong>.
               Here's what we found:</p>
            <ul>
                <li>Characters tracked with roles, traits, and relationships</li>
                <li>Timeline events mapped in order</li>
                <li>Voice profile: POV, tense, and tone identified</li>
            </ul>
            <p>This is just the beginning — unlock full analysis to get chapter-by-chapter
               developmental editing feedback on consistency, pacing, and genre conventions.</p>
            <a href="{safe_url}"
               style="display: inline-block; padding: 12px 24px; background: #2563eb; color: white;
                      text-decoration: none; border-radius: 6px; font-weight: 500;">
                View Your Story Bible
            </a>
        </div>
        """,
    )


def send_drip_email_2(to: str, manuscript_title: str, pricing_url: str) -> str | None:
    """Drip email 2 — sent 2 days after bible generated (no payment).

    Per blueprint: '3 things developmental editors check that GhostEditor catches'
    """
    safe_title = html.escape(manuscript_title)
    safe_url = html.escape(pricing_url, quote=True)
    return _send(
        to=to,
        subject="3 things developmental editors check that GhostEditor catches automatically",
        html=f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto;">
            <h2 style="font-size: 1.25rem;">What a $5K Editor Does — and What GhostEditor Catches</h2>
            <p>Developmental editors charge $3,000–$8,000 per manuscript. Here are three things
               they check that GhostEditor automates:</p>
            <ol>
                <li><strong>Character consistency</strong> — Did your protagonist's eye color change
                    between chapters? GhostEditor tracks every detail across your story bible.</li>
                <li><strong>Pacing analysis</strong> — Are your tension arcs building properly?
                    GhostEditor maps scene types and tension flow chapter by chapter.</li>
                <li><strong>Genre conventions</strong> — Is your romance hitting the expected beats?
                    GhostEditor scores each chapter against genre expectations.</li>
            </ol>
            <p>Your story bible for <strong>{safe_title}</strong> is waiting.
               Unlock full analysis for $49 — or $29 with beta code BETA.</p>
            <a href="{safe_url}"
               style="display: inline-block; padding: 12px 24px; background: #2563eb; color: white;
                      text-decoration: none; border-radius: 6px; font-weight: 500;">
                Unlock Full Analysis — $49
            </a>
        </div>
        """,
    )


def send_drip_email_3(to: str, manuscript_title: str, pricing_url: str) -> str | None:
    """Drip email 3 — sent 5 days after bible generated (no payment).

    Per blueprint: 'Your beta discount expires soon'
    """
    safe_title = html.escape(manuscript_title)
    safe_url = html.escape(pricing_url, quote=True)
    return _send(
        to=to,
        subject=f"Your beta discount for \"{safe_title}\" expires soon",
        html=f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto;">
            <h2 style="font-size: 1.25rem;">Last Chance: $29 Beta Pricing</h2>
            <p>Your story bible for <strong>{safe_title}</strong> has been waiting.
               As a beta user, you can unlock full chapter-by-chapter analysis for just $29
               — that's $20 off the regular $49 price.</p>
            <p>Use code <strong>BETA</strong> at checkout.</p>
            <a href="{safe_url}"
               style="display: inline-block; padding: 12px 24px; background: #2563eb; color: white;
                      text-decoration: none; border-radius: 6px; font-weight: 500;">
                Unlock for $29 (code: BETA)
            </a>
            <p style="color: #6b7280; font-size: 0.85rem; margin-top: 1.5rem;">
                This is the last email we'll send about this manuscript. Your story bible
                and any analysis results are always available in your dashboard.
            </p>
        </div>
        """,
    )
