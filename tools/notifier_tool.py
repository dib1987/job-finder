"""
Email Notifier — Daily run summary via Gmail SMTP

Sends a formatted email summarizing each agent run:
  - How many jobs were scanned
  - How many matched the threshold
  - How many were applied to
  - How many need manual review (approval pending or manual apply)

Setup (one-time):
  1. Enable 2FA on your Gmail account
  2. Go to: Google Account → Security → App passwords
  3. Generate an app password for "Mail"
  4. Set in .env:
       SMTP_EMAIL=you@gmail.com
       SMTP_APP_PASSWORD=xxxx_xxxx_xxxx_xxxx
       NOTIFY_EMAIL=you@example.com

SaaS migration: replace smtplib with SendGrid API — same send_report() interface.

Usage:
    from tools.notifier_tool import send_report
    send_report(run_summary)
"""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("job_finder.notifier")


def send_report(run_summary: dict) -> bool:
    """
    Send the daily run summary email.

    run_summary schema:
    {
      "timestamp": "2026-03-27T09:00:00",
      "jobs_scanned": 30,
      "matches_above_threshold": 5,
      "approved_count": 3,
      "applied_count": 2,
      "failed_count": 0,
      "manual_apply_count": 1,
      "pending_approval_count": 2,
      "top_matches": [
        {"title": "...", "company": "...", "score": 84, "url": "..."},
        ...
      ]
    }

    Returns True on success, False on failure (non-blocking).
    """
    smtp_email    = os.getenv("SMTP_EMAIL", "")
    smtp_password = os.getenv("SMTP_APP_PASSWORD", "")
    notify_email  = os.getenv("NOTIFY_EMAIL", smtp_email)

    if not smtp_email or not smtp_password:
        logger.warning(
            "Email not configured. Set SMTP_EMAIL and SMTP_APP_PASSWORD in .env to enable notifications."
        )
        return False

    subject, html_body, plain_body = _build_email(run_summary)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = smtp_email
        msg["To"]      = notify_email
        msg.attach(MIMEText(plain_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_email, smtp_password)
            server.sendmail(smtp_email, notify_email, msg.as_string())

        logger.info(f"Daily report sent to {notify_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def _build_email(s: dict) -> tuple[str, str, str]:
    """Build subject line, HTML body, and plain text body."""
    date_str = s.get("timestamp", datetime.now().isoformat())[:10]
    applied  = s.get("applied_count", 0)
    matches  = s.get("matches_above_threshold", 0)
    scanned  = s.get("jobs_scanned", 0)
    pending  = s.get("pending_approval_count", 0)
    manual   = s.get("manual_apply_count", 0)

    subject = f"Job Agent Report {date_str} — {applied} applied, {pending} pending your review"

    # Top matches section
    top_matches_html = ""
    for job in s.get("top_matches", [])[:5]:
        score_color = "#28a745" if job["score"] >= 80 else "#ffc107" if job["score"] >= 65 else "#dc3545"
        top_matches_html += f"""
        <tr>
          <td style="padding:6px 12px; border-bottom:1px solid #eee;">
            <a href="{job.get('url','#')}" style="color:#0077B5; text-decoration:none; font-weight:bold;">
              {job['title']}
            </a><br>
            <span style="color:#666; font-size:13px;">{job['company']}</span>
          </td>
          <td style="padding:6px 12px; border-bottom:1px solid #eee; text-align:center;">
            <span style="background:{score_color}; color:white; padding:2px 8px; border-radius:12px; font-size:13px;">
              {job['score']}
            </span>
          </td>
        </tr>"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #333;">

  <div style="background: #0077B5; padding: 20px; border-radius: 8px 8px 0 0;">
    <h1 style="color: white; margin: 0; font-size: 20px;">Job Agent Daily Report</h1>
    <p style="color: #cce5f6; margin: 4px 0 0;">{date_str}</p>
  </div>

  <div style="background: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; border-top: none;">

    <table width="100%" style="margin-bottom: 20px;">
      <tr>
        <td style="text-align:center; padding:12px;">
          <div style="font-size:28px; font-weight:bold; color:#333;">{scanned}</div>
          <div style="color:#666; font-size:13px;">Jobs Scanned</div>
        </td>
        <td style="text-align:center; padding:12px;">
          <div style="font-size:28px; font-weight:bold; color:#0077B5;">{matches}</div>
          <div style="color:#666; font-size:13px;">Matches Found</div>
        </td>
        <td style="text-align:center; padding:12px;">
          <div style="font-size:28px; font-weight:bold; color:#28a745;">{applied}</div>
          <div style="color:#666; font-size:13px;">Applied</div>
        </td>
        <td style="text-align:center; padding:12px;">
          <div style="font-size:28px; font-weight:bold; color:#ffc107;">{pending}</div>
          <div style="color:#666; font-size:13px;">Awaiting Review</div>
        </td>
      </tr>
    </table>

    {f'''
    <h2 style="font-size:16px; color:#333; border-bottom:2px solid #0077B5; padding-bottom:8px;">
      Top Matches
    </h2>
    <table width="100%" style="border-collapse:collapse; margin-bottom:20px;">
      <tr style="background:#f1f3f5;">
        <th style="padding:8px 12px; text-align:left; font-size:13px; color:#666;">Position</th>
        <th style="padding:8px 12px; text-align:center; font-size:13px; color:#666;">Score</th>
      </tr>
      {top_matches_html}
    </table>
    ''' if top_matches_html else ''}

    {f'<p style="color:#e67e22;"><strong>⚠ {pending} jobs await your approval.</strong> Run: <code>python agent.py --phase approve</code></p>' if pending > 0 else ''}
    {f'<p style="color:#3498db;"><strong>📋 {manual} jobs require manual application</strong> (no Easy Apply). Check your tracker.</p>' if manual > 0 else ''}

  </div>

  <div style="background:#dee2e6; padding:12px; border-radius:0 0 8px 8px; text-align:center;">
    <p style="color:#666; font-size:12px; margin:0;">
      Job Agent — personal AI application assistant
    </p>
  </div>

</body>
</html>"""

    plain_body = f"""Job Agent Daily Report — {date_str}
{'='*50}
Jobs Scanned:        {scanned}
Matches Found:       {matches}
Applied:             {applied}
Awaiting Your Review:{pending}
Manual Apply Needed: {manual}

Top Matches:
"""
    for job in s.get("top_matches", [])[:5]:
        plain_body += f"  [{job['score']}/100] {job['title']} @ {job['company']}\n"
        plain_body += f"    {job.get('url','')}\n\n"

    if pending > 0:
        plain_body += f"\n{pending} jobs need your approval:\n  python agent.py --phase approve\n"

    return subject, html_body, plain_body
