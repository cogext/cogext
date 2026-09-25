"""Signup notifications — email alerts when a new account is created.

Kept separate from ``app.core.notifications`` (the Slack kill-switch module)
so the two concerns stay independent.
"""
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import settings

logger = logging.getLogger(__name__)


class Notifier:
    def _send_email(self, subject: str, html: str) -> None:
        """Send email via SMTP. Never raises — logs on failure."""
        if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
            logger.warning("SMTP credentials not configured, skipping email")
            return
        if not settings.NOTIFY_EMAIL:
            logger.warning("NOTIFY_EMAIL not configured, skipping email")
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_USER
            msg["To"] = settings.NOTIFY_EMAIL
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.send_message(msg)

            logger.info("Signup notification sent to %s", settings.NOTIFY_EMAIL)
        except Exception as e:
            logger.error("Failed to send signup notification: %s", e)

    async def notify_signup(
        self,
        email: str,
        account_id: str,
        key_id: str,
        request_ip: str | None = None,
        country: str | None = None,
    ) -> None:
        """
        Fire signup notification. Never raises — signup must not break.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        key_prefix = key_id[:8] if key_id else "unknown"

        subject = f"New COGEXT signup: {email}"

        html = f"""
        <html>
        <body style="font-family: -apple-system, system-ui, sans-serif; 
                     background: #F7F7F7; padding: 24px;">
          <div style="max-width: 600px; margin: 0 auto; background: white; 
                      border-radius: 12px; padding: 32px; 
                      border: 1px solid #E7E7E3;">
            <h1 style="color: #111111; font-size: 20px; margin: 0 0 24px 0;">
              New COGEXT signup
            </h1>
            
            <p style="font-size: 24px; font-weight: 600; color: #6366F1; 
                      margin: 0 0 24px 0; font-family: monospace;">
              {email}
            </p>
            
            <table style="width: 100%; border-collapse: collapse; 
                          color: #555; font-size: 14px;">
              <tr>
                <td style="padding: 8px 0; color: #8A8A8A;">Time</td>
                <td style="padding: 8px 0;">{timestamp}</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; color: #8A8A8A;">Account ID</td>
                <td style="padding: 8px 0; font-family: monospace; 
                           font-size: 13px;">{account_id}</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; color: #8A8A8A;">Key prefix</td>
                <td style="padding: 8px 0; font-family: monospace;">{key_prefix}...</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; color: #8A8A8A;">IP</td>
                <td style="padding: 8px 0;">{request_ip or "unknown"}</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; color: #8A8A8A;">Country</td>
                <td style="padding: 8px 0;">{country or "unknown"}</td>
              </tr>
            </table>
            
            <a href="mailto:{email}" 
               style="display: inline-block; margin-top: 32px; 
                      background: #6366F1; color: white; 
                      padding: 12px 24px; border-radius: 8px; 
                      text-decoration: none; font-weight: 600;">
              Reply to this user →
            </a>
          </div>
        </body>
        </html>
        """

        self._send_email(subject, html)


notifier = Notifier()
