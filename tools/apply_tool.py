"""
LinkedIn Easy Apply Tool — Playwright automation

Submits applications via LinkedIn's Easy Apply flow using the original resume PDF.

Only applies to jobs where:
  1. easy_apply == True
  2. User approved in the approval gate
  3. Not already in the tracker (duplicate prevention)

IMPORTANT: Always test with --dry-run first.
DRY_RUN=true in .env (or --dry-run flag) skips the actual form submission.

Flow per job:
  1. Navigate to job URL
  2. Click "Easy Apply" button
  3. Fill form fields from preferences.json (name, email, phone, location)
  4. Upload resume.pdf on the file upload step
  5. Click Continue through all steps
  6. Click Submit on the final step
  7. Record result in tracker

Usage:
    from tools.apply_tool import apply_to_jobs
    apply_to_jobs(approved_jobs, preferences, tracker)
"""
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

from tools.tracker_tool import ApplicationTracker

logger = logging.getLogger("job_finder.apply_tool")


def apply_to_jobs(
    approved_jobs: list[dict],
    preferences: dict,
    tracker: ApplicationTracker,
    resume_path: str = "config/resume.pdf",
    dry_run: bool = False,
) -> dict:
    """
    Apply to each approved job via LinkedIn Easy Apply.

    Returns summary:
        {"applied": [...], "skipped_manual": [...], "failed": [...], "dry_run": [...]}
    """
    dry_run = dry_run or os.getenv("DRY_RUN", "false").lower() == "true"
    resume = Path(resume_path)

    if not resume.exists():
        raise FileNotFoundError(
            f"Resume not found at: {resume.resolve()}\n"
            f"Place your resume PDF at: config/resume.pdf"
        )

    results = {"applied": [], "skipped_manual": [], "failed": [], "dry_run": []}

    easy_apply_jobs = [j for j in approved_jobs if j.get("easy_apply")]
    manual_jobs     = [j for j in approved_jobs if not j.get("easy_apply")]

    # Flag manual-apply jobs — can't auto-apply but record them
    for job in manual_jobs:
        logger.info(f"Manual apply required: {job['title']} @ {job['company']} — {job['url']}")
        tracker.record(
            job_id=job["job_id"],
            title=job["title"],
            company=job["company"],
            score=job["score"],
            status="manual_apply_needed",
            location=job.get("location", ""),
            url=job.get("url", ""),
            notes="No Easy Apply — requires manual application on company site",
        )
        results["skipped_manual"].append(job["job_id"])

    if not easy_apply_jobs:
        logger.info("No Easy Apply jobs in the approved list.")
        return results

    if dry_run:
        logger.info(f"DRY RUN: Would apply to {len(easy_apply_jobs)} jobs (no actual submissions)")
        for job in easy_apply_jobs:
            logger.info(f"  [DRY RUN] {job['title']} @ {job['company']}")
            tracker.record(
                job_id=job["job_id"],
                title=job["title"],
                company=job["company"],
                score=job["score"],
                status="dry_run",
                location=job.get("location", ""),
                url=job.get("url", ""),
                notes="Dry run — not actually submitted",
            )
            results["dry_run"].append(job["job_id"])
        return results

    from playwright.sync_api import sync_playwright
    try:
        from playwright_stealth import stealth_sync
        has_stealth = True
    except ImportError:
        has_stealth = False

    session_path = os.getenv("LINKEDIN_SESSION_PATH", "config/linkedin_session.json")
    personal = preferences.get("personal", {})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=session_path if Path(session_path).exists() else None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        if has_stealth:
            stealth_sync(page)

        for job in easy_apply_jobs:
            job_id = job["job_id"]
            title  = job["title"]
            company = job["company"]

            if tracker.is_duplicate(job_id):
                logger.info(f"Skipping duplicate: {title} @ {company}")
                continue

            logger.info(f"Applying: {title} @ {company}")
            success, notes = _apply_single(page, job, personal, str(resume.resolve()))

            status = "applied" if success else "failed"
            tracker.record(
                job_id=job_id,
                title=title,
                company=company,
                score=job["score"],
                status=status,
                location=job.get("location", ""),
                url=job.get("url", ""),
                notes=notes,
                raw_data=job,
            )

            if success:
                results["applied"].append(job_id)
                logger.info(f"  ✓ Applied successfully: {title} @ {company}")
            else:
                results["failed"].append(job_id)
                logger.warning(f"  ✗ Application failed: {title} @ {company} — {notes}")

            # Delay between applications (human-like)
            time.sleep(random.uniform(5.0, 10.0))

        browser.close()

    logger.info(
        f"Apply phase complete: {len(results['applied'])} applied, "
        f"{len(results['failed'])} failed, "
        f"{len(results['skipped_manual'])} manual-apply needed"
    )
    return results


def _apply_single(page, job: dict, personal: dict, resume_path: str) -> tuple[bool, str]:
    """
    Execute the LinkedIn Easy Apply flow for a single job.
    Returns (success, notes).
    """
    try:
        # Navigate to job page
        page.goto(job["url"], timeout=20000)
        time.sleep(random.uniform(2.0, 3.5))

        # Click Easy Apply button
        apply_btn = page.query_selector(
            "button[aria-label*='Easy Apply'], .jobs-apply-button--top-card"
        )
        if not apply_btn:
            return False, "Easy Apply button not found on page"

        apply_btn.click()
        time.sleep(random.uniform(1.5, 2.5))

        # Handle multi-step modal
        max_steps = 10
        for step in range(max_steps):
            # Fill contact info on first step
            if step == 0:
                _fill_contact_fields(page, personal)

            # Handle file upload (resume)
            upload_input = page.query_selector("input[type='file']")
            if upload_input:
                upload_input.set_input_files(resume_path)
                time.sleep(1.5)

            # Check for final submit button
            submit_btn = page.query_selector(
                "button[aria-label='Submit application'], "
                "button:has-text('Submit application')"
            )
            if submit_btn:
                submit_btn.click()
                time.sleep(2.0)
                return True, "Submitted via Easy Apply"

            # Click Next/Continue
            next_btn = page.query_selector(
                "button[aria-label='Continue to next step'], "
                "button:has-text('Next'), "
                "button:has-text('Continue'), "
                "button:has-text('Review')"
            )
            if next_btn:
                next_btn.click()
                time.sleep(random.uniform(1.0, 2.0))
            else:
                # No next and no submit — may be at review step
                review_btn = page.query_selector("button:has-text('Review your application')")
                if review_btn:
                    review_btn.click()
                    time.sleep(1.5)
                else:
                    return False, f"Stuck at step {step+1} — no Next/Submit button found"

        return False, "Exceeded max steps without submitting"

    except Exception as e:
        return False, f"Exception during apply: {e}"


def _fill_contact_fields(page, personal: dict) -> None:
    """Fill standard contact fields in the Easy Apply modal."""
    field_map = {
        "input[name='phoneNumber']": personal.get("phone", ""),
        "input[aria-label*='Phone']": personal.get("phone", ""),
        "input[aria-label*='Email']": personal.get("email", ""),
        "input[aria-label*='First name']": personal.get("name", "").split()[0] if personal.get("name") else "",
        "input[aria-label*='Last name']": personal.get("name", "").split()[-1] if personal.get("name") else "",
    }
    for selector, value in field_map.items():
        if not value:
            continue
        try:
            el = page.query_selector(selector)
            if el and not el.get_attribute("value"):
                el.fill(value)
        except Exception:
            pass
