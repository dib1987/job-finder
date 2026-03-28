"""
Unified Email Tool — Gmail SMTP sender for the Job Finder agent.

This module consolidates the SMTP logic previously split between
tools/notifier_tool.py (agent pipeline) and the email-reporter skill
(Claude Code interactive use) into a single, consistent implementation.

.env variables (canonical):
    EMAIL_FROM          Gmail address to send from
    EMAIL_APP_PASSWORD  Gmail App Password (16 chars, not your Gmail password)
    EMAIL_TO            Address to receive reports

Fallback (deprecated, kept for one-run transition):
    SMTP_EMAIL, SMTP_APP_PASSWORD, NOTIFY_EMAIL

Setup guide:
    1. Enable 2FA on Gmail
    2. Google Account → Security → App passwords → Generate for "Mail"
    3. Set EMAIL_FROM, EMAIL_APP_PASSWORD, EMAIL_TO in .env

Usage:
    from tools.email_tool import send_job_report, send_email

    # High-level: send the agent's run summary
    send_job_report(run_summary)

    # Low-level: send any HTML email
    send_email("My Subject", "<h1>Hello</h1>")
"""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("job_finder.email")


# ── Credential loading ─────────────────────────────────────────────────────────

def _load_credentials() -> tuple[str, str, str]:
    """
    Load email credentials from environment.
    Reads EMAIL_FROM/EMAIL_APP_PASSWORD/EMAIL_TO first.
    Falls back to legacy SMTP_EMAIL/SMTP_APP_PASSWORD/NOTIFY_EMAIL with a warning.
    Returns (from_addr, password, to_addr) or ("", "", "") if unconfigured.
    """
    from_addr = os.getenv("EMAIL_FROM", "")
    password  = os.getenv("EMAIL_APP_PASSWORD", "")
    to_addr   = os.getenv("EMAIL_TO", "")

    # Fallback to legacy vars (deprecated)
    if not from_addr:
        legacy = os.getenv("SMTP_EMAIL", "")
        if legacy:
            logger.warning(
                "SMTP_EMAIL is deprecated. Rename to EMAIL_FROM in .env."
            )
            from_addr = legacy
    if not password:
        legacy = os.getenv("SMTP_APP_PASSWORD", "")
        if legacy:
            password = legacy
    if not to_addr:
        legacy = os.getenv("NOTIFY_EMAIL", from_addr)
        if legacy:
            to_addr = legacy

    return from_addr, password, to_addr


# ── Low-level sender ───────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str, plain_body: str = "") -> bool:
    """
    Send an HTML email via Gmail SMTP (SSL port 465).

    Args:
        subject:    Email subject line.
        html_body:  HTML content string.
        plain_body: Optional plain text fallback (auto-generated if empty).

    Returns:
        True on success, False on any failure (non-blocking — never raises).
    """
    from_addr, password, to_addr = _load_credentials()

    if not from_addr or not password:
        logger.warning(
            "Email not configured. Set EMAIL_FROM and EMAIL_APP_PASSWORD in .env."
        )
        return False

    if not plain_body:
        plain_body = "This email requires an HTML-capable email client."

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Job Agent <{from_addr}>"
        msg["To"]      = to_addr
        msg.attach(MIMEText(plain_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_string())

        logger.info(f"Email sent to {to_addr} — subject: {subject!r}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Gmail authentication failed. Check EMAIL_APP_PASSWORD in .env. "
            "Must be a 16-character App Password, not your Gmail password."
        )
        return False

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


# ── Job report builder ─────────────────────────────────────────────────────────

def send_job_report(run_summary: dict) -> bool:
    """
    Build and send the agent's daily run summary email.

    run_summary schema:
    {
      "timestamp":               "2026-03-28T09:00:00",
      "jobs_scanned":            30,
      "matches_above_threshold": 5,
      "approved_count":          3,
      "applied_count":           2,
      "failed_count":            0,
      "manual_apply_count":      1,
      "pending_approval_count":  2,
      "top_matches": [
        {"title": "...", "company": "...", "score": 84, "url": "..."},
        ...
      ],
      "research_highlights": [   # optional — added by phase_research
        {
          "company":            "Acme Corp",
          "sponsorship_signal": "likely_yes",
          "verdict":            "proceed",
          "verdict_reason":     "Strong H1B sponsor historically."
        },
        ...
      ]
    }
    """
    subject   = _build_subject(run_summary)
    html_body = _build_job_report_html(run_summary)
    plain_body = _build_plain(run_summary)
    return send_email(subject, html_body, plain_body)


# ── HTML builder ───────────────────────────────────────────────────────────────

def _build_subject(s: dict) -> str:
    date_str = s.get("timestamp", datetime.now().isoformat())[:10]
    applied  = s.get("applied_count", 0)
    pending  = s.get("pending_approval_count", 0)
    return f"Job Agent Report {date_str} — {applied} applied, {pending} pending review"


def _build_job_report_html(s: dict) -> str:
    date_str = s.get("timestamp", datetime.now().isoformat())[:10]
    scanned  = s.get("jobs_scanned", 0)
    matches  = s.get("matches_above_threshold", 0)
    applied  = s.get("applied_count", 0)
    pending  = s.get("pending_approval_count", 0)
    failed   = s.get("failed_count", 0)
    manual   = s.get("manual_apply_count", 0)

    top_matches_html = _build_top_matches_html(s.get("top_matches", []))
    research_html    = _build_research_html(s.get("research_highlights", []))
    alerts_html      = _build_alerts_html(pending, manual, failed)

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 660px; margin: 0 auto; color: #333; }}
    .header {{ background: #0077B5; padding: 24px; border-radius: 8px 8px 0 0; }}
    .header h1 {{ color: white; margin: 0; font-size: 20px; }}
    .header p  {{ color: #cce5f6; margin: 4px 0 0; font-size: 13px; }}
    .body {{ background: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; border-top: none; }}
    .section {{ margin-bottom: 24px; }}
    .section h2 {{ font-size: 15px; color: #333; border-bottom: 2px solid #0077B5; padding-bottom: 6px; margin-bottom: 12px; }}
    .stats td {{ text-align: center; padding: 12px; }}
    .stat-num {{ font-size: 28px; font-weight: bold; }}
    .stat-lbl {{ color: #666; font-size: 12px; }}
    table.results {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    table.results th {{ background: #f1f3f5; padding: 8px 12px; text-align: left; color: #666; }}
    table.results td {{ padding: 8px 12px; border-bottom: 1px solid #eee; vertical-align: top; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; color: white; }}
    .excellent {{ background: #28a745; }}
    .good      {{ background: #ffc107; color: #333; }}
    .partial   {{ background: #fd7e14; }}
    .poor      {{ background: #dc3545; }}
    .sponsor-yes {{ background: #28a745; }}
    .sponsor-no  {{ background: #dc3545; }}
    .sponsor-unk {{ background: #6c757d; }}
    .alert {{ padding: 10px 14px; border-radius: 6px; margin-bottom: 8px; font-size: 13px; }}
    .alert-warn {{ background: #fff3cd; border-left: 4px solid #ffc107; color: #856404; }}
    .alert-info {{ background: #d1ecf1; border-left: 4px solid #0c5460; color: #0c5460; }}
    .alert-err  {{ background: #f8d7da; border-left: 4px solid #dc3545; color: #721c24; }}
    .footer {{ background: #dee2e6; padding: 12px; border-radius: 0 0 8px 8px; text-align: center; }}
    .footer p {{ color: #666; font-size: 11px; margin: 0; }}
    a {{ color: #0077B5; text-decoration: none; }}
  </style>
</head>
<body>

  <div class="header">
    <h1>Job Agent Daily Report</h1>
    <p>{date_str}</p>
  </div>

  <div class="body">

    <!-- Stats row -->
    <div class="section">
      <table class="stats" width="100%"><tr>
        <td><div class="stat-num" style="color:#333;">{scanned}</div><div class="stat-lbl">Scanned</div></td>
        <td><div class="stat-num" style="color:#0077B5;">{matches}</div><div class="stat-lbl">Matched</div></td>
        <td><div class="stat-num" style="color:#28a745;">{applied}</div><div class="stat-lbl">Applied</div></td>
        <td><div class="stat-num" style="color:#ffc107;">{pending}</div><div class="stat-lbl">Awaiting Review</div></td>
      </tr></table>
    </div>

    <!-- Top matches -->
    {top_matches_html}

    <!-- Research highlights (only shown if research phase ran) -->
    {research_html}

    <!-- Alerts -->
    {alerts_html}

  </div>

  <div class="footer">
    <p>Job Agent &mdash; personal AI application assistant &mdash; {date_str}</p>
  </div>

</body>
</html>"""


def _score_badge(score: int) -> str:
    if score >= 85:
        css, label = "excellent", "Excellent"
    elif score >= 70:
        css, label = "good", "Good"
    elif score >= 50:
        css, label = "partial", "Partial"
    else:
        css, label = "poor", "Poor"
    return f'<span class="badge {css}">{score} — {label}</span>'


def _sponsor_badge(signal: str) -> str:
    if signal == "likely_yes":
        return '<span class="badge sponsor-yes">Sponsors H1B</span>'
    elif signal == "likely_no":
        return '<span class="badge sponsor-no">No Sponsorship</span>'
    return '<span class="badge sponsor-unk">Sponsorship Unknown</span>'


def _build_top_matches_html(top_matches: list) -> str:
    if not top_matches:
        return ""
    rows = ""
    for job in top_matches[:5]:
        url   = job.get("url", "#") or "#"
        title = job.get("title", "Unknown")
        comp  = job.get("company", "")
        score = job.get("score", 0)
        rows += f"""
        <tr>
          <td>
            <a href="{url}"><strong>{title}</strong></a><br>
            <span style="color:#666; font-size:12px;">{comp}</span>
          </td>
          <td style="text-align:center;">{_score_badge(score)}</td>
        </tr>"""
    return f"""
    <div class="section">
      <h2>Top Matches</h2>
      <table class="results">
        <tr><th>Position</th><th style="text-align:center;">Match Score</th></tr>
        {rows}
      </table>
    </div>"""


def _build_research_html(highlights: list) -> str:
    if not highlights:
        return ""
    rows = ""
    for r in highlights[:5]:
        company = r.get("company", "")
        signal  = r.get("sponsorship_signal", "unknown")
        verdict = r.get("verdict", "unknown")
        reason  = r.get("verdict_reason", "")
        verdict_color = {"proceed": "#28a745", "caution": "#fd7e14", "avoid": "#dc3545"}.get(verdict, "#6c757d")
        rows += f"""
        <tr>
          <td><strong>{company}</strong></td>
          <td>{_sponsor_badge(signal)}</td>
          <td><span style="color:{verdict_color}; font-weight:bold;">{verdict.upper()}</span><br>
              <span style="font-size:11px; color:#666;">{reason}</span></td>
        </tr>"""
    return f"""
    <div class="section">
      <h2>Company Research (H1B &amp; Culture)</h2>
      <table class="results">
        <tr><th>Company</th><th>Sponsorship</th><th>Verdict &amp; Reason</th></tr>
        {rows}
      </table>
    </div>"""


def _build_alerts_html(pending: int, manual: int, failed: int) -> str:
    html = ""
    if pending > 0:
        html += f'<div class="alert alert-warn"><strong>{pending} jobs awaiting your approval.</strong></div>'
    if manual > 0:
        html += f'<div class="alert alert-info"><strong>{manual} jobs require manual application</strong> (no Easy Apply). Check your tracker.</div>'
    if failed > 0:
        html += f'<div class="alert alert-err"><strong>{failed} applications failed.</strong> Check logs/ for details.</div>'
    return f'<div class="section">{html}</div>' if html else ""


def _build_plain(s: dict) -> str:
    date_str = s.get("timestamp", datetime.now().isoformat())[:10]
    lines = [
        f"Job Agent Daily Report — {date_str}",
        "=" * 50,
        f"Jobs Scanned:        {s.get('jobs_scanned', 0)}",
        f"Matches Found:       {s.get('matches_above_threshold', 0)}",
        f"Applied:             {s.get('applied_count', 0)}",
        f"Awaiting Review:     {s.get('pending_approval_count', 0)}",
        f"Manual Apply Needed: {s.get('manual_apply_count', 0)}",
        "",
        "Top Matches:",
    ]
    for job in s.get("top_matches", [])[:5]:
        lines.append(f"  [{job.get('score', 0)}/100] {job.get('title', '')} @ {job.get('company', '')}")
        lines.append(f"    {job.get('url', '')}")
        lines.append("")

    highlights = s.get("research_highlights", [])
    if highlights:
        lines.append("Company Research:")
        for r in highlights[:5]:
            lines.append(
                f"  {r.get('company','')} — {r.get('sponsorship_signal','unknown')} — "
                f"{r.get('verdict','').upper()}: {r.get('verdict_reason','')}"
            )
        lines.append("")

    pending = s.get("pending_approval_count", 0)
    if pending > 0:
        lines.append(f"{pending} jobs need your approval:")
        lines.append("  python agent.py --phase approve")

    return "\n".join(lines)
