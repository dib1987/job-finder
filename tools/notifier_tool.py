"""
Email Notifier — DEPRECATED shim.

All email logic has moved to tools/email_tool.py which provides a unified
backend used by both the agent pipeline and the email-reporter Claude skill.

This file is kept for backward compatibility. Do not add new logic here.
"""


def send_report(run_summary: dict) -> bool:
    """Deprecated shim — delegates to tools.email_tool.send_job_report."""
    from tools.email_tool import send_job_report
    return send_job_report(run_summary)
