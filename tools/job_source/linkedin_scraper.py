"""
LinkedIn Job Scraper — Playwright-based (POC source)

Fixed for:
  - playwright-stealth v2 API (uses Stealth class, not stealth_sync)
  - LinkedIn DOM updates (robust multi-selector fallbacks, no hardcoded class names)
  - More resilient wait strategy (networkidle instead of specific selectors)
"""
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

from tools.job_source.base import JobListing, JobSource

logger = logging.getLogger("job_finder.linkedin_scraper")

LINKEDIN_JOBS_URL = "https://www.linkedin.com/jobs/search/"


class LinkedInScraper(JobSource):

    def __init__(self, session_path: str = "config/linkedin_session.json"):
        self.session_path = Path(session_path)
        self._check_session_age()

    def fetch_jobs(
        self,
        keywords: list[str],
        location: str,
        limit: int = 30,
        filters: Optional[dict] = None,
    ) -> list[JobListing]:
        from playwright.sync_api import sync_playwright

        jobs = []
        query = " ".join(keywords)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(self.session_path) if self.session_path.exists() else None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            # Apply stealth — hide webdriver fingerprint manually
            # (works regardless of playwright-stealth version)
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                window.chrome = { runtime: {} };
            """)

            try:
                logger.info(f"Searching LinkedIn: '{query}' in '{location}'")
                search_url = self._build_search_url(query, location, filters)
                page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
                self._random_delay(2, 4)

                # Check if redirected to login
                if "login" in page.url or "authwall" in page.url or "checkpoint" in page.url:
                    logger.error(
                        "LinkedIn session expired or blocked. "
                        "Run setup_linkedin.bat to refresh your session."
                    )
                    return []

                job_ids = self._collect_job_ids(page, limit)
                logger.info(f"Found {len(job_ids)} job IDs. Fetching details...")

                for i, job_id in enumerate(job_ids):
                    logger.info(f"  [{i+1}/{len(job_ids)}] Fetching job {job_id}...")
                    job = self._fetch_job_detail(page, job_id)
                    if job:
                        jobs.append(job)
                    self._random_delay(2, 4)

            except Exception as e:
                logger.error(f"Scraping error: {e}")
            finally:
                browser.close()

        logger.info(f"Scraped {len(jobs)} jobs successfully.")
        return jobs

    def get_job_detail(self, job_id: str) -> Optional[JobListing]:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(self.session_path) if self.session_path.exists() else None
            )
            page = context.new_page()
            job = self._fetch_job_detail(page, job_id)
            browser.close()
        return job

    # ── Session Setup ─────────────────────────────────────────────────────────

    def setup_session(self) -> None:
        """One-time interactive login — called from setup_linkedin.py."""
        from playwright.sync_api import sync_playwright
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto("https://www.linkedin.com/login")
            input("\nLog in to LinkedIn, then press ENTER here...")
            context.storage_state(path=str(self.session_path))
            browser.close()
        print(f"Session saved to: {self.session_path}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_search_url(self, query: str, location: str, filters: Optional[dict]) -> str:
        import urllib.parse
        params = {
            "keywords": query,
            "location": location or "United States",
            "f_TPR": "r604800",   # last 7 days
            "f_JT": "F",          # Full-time
        }
        if filters and filters.get("remote_only"):
            params["f_WT"] = "2"
        elif location and location.lower() in ("hybrid",):
            params["f_WT"] = "3"  # Hybrid work type
        return f"{LINKEDIN_JOBS_URL}?{urllib.parse.urlencode(params)}"

    def _collect_job_ids(self, page, limit: int) -> list[str]:
        """Collect job IDs from search results page."""
        job_ids = []
        max_scrolls = (limit // 10) + 5

        for scroll in range(max_scrolls):
            if len(job_ids) >= limit:
                break

            # Try multiple selector patterns LinkedIn uses
            for selector in [
                "[data-occludable-job-id]",
                "[data-job-id]",
                "li[class*='jobs-search-results__list-item']",
                "div[class*='job-card-container']",
            ]:
                cards = page.query_selector_all(selector)
                for card in cards:
                    # Extract job ID from various attributes
                    job_id = (
                        card.get_attribute("data-occludable-job-id")
                        or card.get_attribute("data-job-id")
                        or self._extract_id_from_href(card)
                    )
                    if job_id and job_id not in job_ids:
                        job_ids.append(job_id)

            if len(job_ids) >= limit:
                break

            # Scroll to load more
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._random_delay(1.5, 3.0)

            # Click "Show more" button if present
            try:
                show_more = page.query_selector("button[aria-label*='Load more'], button[aria-label*='Show more']")
                if show_more:
                    show_more.click()
                    self._random_delay(1.5, 2.5)
            except Exception:
                pass

        return job_ids[:limit]

    def _extract_id_from_href(self, card) -> Optional[str]:
        """Extract job ID from an anchor href like /jobs/view/1234567/"""
        try:
            link = card.query_selector("a[href*='/jobs/view/']")
            if link:
                href = link.get_attribute("href") or ""
                import re
                m = re.search(r"/jobs/view/(\d+)", href)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return None

    def _fetch_job_detail(self, page, job_id: str) -> Optional[JobListing]:
        """Navigate to job detail page and extract all fields."""
        try:
            job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
            page.goto(job_url, timeout=25000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                self._random_delay(2, 3)

            if "login" in page.url or "authwall" in page.url:
                logger.warning(f"Session expired while fetching job {job_id}")
                return None

            # ── Title + Company from <title> tag ──────────────────────────────
            # LinkedIn title format: "Job Title | Company | LinkedIn"
            page_title = page.title()
            title, company = self._parse_page_title(page_title)

            # ── Full page text for everything else ────────────────────────────
            page_text = page.evaluate("() => document.body.innerText") or ""

            # ── Location: line right after company in header area ─────────────
            location = self._extract_location_from_text(page_text, company)

            # ── Description: everything after "About the job" ─────────────────
            description = self._extract_description(page_text)

            # ── Easy Apply: check all button text on page ─────────────────────
            easy_apply = self._detect_easy_apply(page)

            # ── Employment type + seniority from page text ────────────────────
            employment_type = self._extract_field(page_text, ["Full-time", "Part-time", "Contract", "Internship", "Temporary"])
            seniority = self._extract_field(page_text, ["Entry level", "Associate", "Mid-Senior level", "Director", "Executive", "Not Applicable"])
            salary = self._extract_salary(page_text)
            date_posted = self._extract_date_posted(page_text)

            if not title:
                logger.warning(f"Job {job_id}: could not extract title, skipping")
                return None

            return JobListing(
                item_id=job_id,
                title=title,
                company=company,
                location=location,
                description=description,
                seniority_level=seniority,
                employment_type=employment_type,
                easy_apply=easy_apply,
                url=job_url,
                date_posted=date_posted,
                salary_range=salary,
                source="linkedin_scraper",
                raw_data={},
            )

        except Exception as e:
            logger.warning(f"Failed to fetch job {job_id}: {e}")
            return None

    def _parse_page_title(self, page_title: str) -> tuple[str, str]:
        """Parse 'Job Title | Company | LinkedIn' into (title, company)."""
        parts = [p.strip() for p in page_title.split("|")]
        # Remove 'LinkedIn' suffix
        parts = [p for p in parts if p.lower() not in ("linkedin", "")]
        if len(parts) >= 2:
            return parts[0], parts[1]
        if len(parts) == 1:
            return parts[0], ""
        return "", ""

    def _extract_location_from_text(self, text: str, company: str) -> str:
        """Find location by looking for City, ST or Remote/Hybrid patterns near top of page."""
        import re
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        # Look in first 20 lines for location patterns
        for line in lines[:20]:
            # Remote / Hybrid standalone
            if line.lower() in ("remote", "hybrid", "on-site", "onsite"):
                return line
            # "City, ST" or "City, State" or "City, Country"
            if re.match(r"^[A-Z][a-z].*,\s*[A-Z]", line) and len(line) < 60:
                return line
            # Contains location keywords
            if any(kw in line for kw in (" · Remote", " · Hybrid", " · On-site")):
                return line

        return ""

    def _extract_description(self, page_text: str) -> str:
        """Extract job description — everything after 'About the job'."""
        markers = ["About the job", "About this role", "Job Description", "Position Overview", "Role Overview"]
        for marker in markers:
            idx = page_text.find(marker)
            if idx != -1:
                desc = page_text[idx + len(marker):idx + 4000].strip()
                if len(desc) > 100:
                    return desc

        # Fallback: grab a large chunk from the middle of the page
        if len(page_text) > 500:
            return page_text[500:4000]
        return page_text

    def _detect_easy_apply(self, page) -> bool:
        """Detect Easy Apply by checking button text — works regardless of class names."""
        try:
            buttons = page.query_selector_all("button")
            for btn in buttons:
                text = btn.inner_text().strip().lower()
                if "easy apply" in text:
                    return True
        except Exception:
            pass
        return False

    def _extract_field(self, text: str, options: list[str]) -> str:
        """Return the first option found in the page text."""
        for option in options:
            if option in text:
                return option
        return ""

    def _extract_salary(self, text: str) -> str:
        """Find salary range like $120,000/yr or $60/hr."""
        import re
        m = re.search(r"\$[\d,]+(?:\.\d+)?(?:/yr|/hr|K|k)?(?:\s*[-–]\s*\$[\d,]+(?:\.\d+)?(?:/yr|/hr|K|k)?)?", text)
        return m.group(0) if m else ""

    def _extract_date_posted(self, text: str) -> str:
        """Find 'X days ago' or 'X hours ago' patterns."""
        import re
        m = re.search(r"\d+\s+(?:minute|hour|day|week)s? ago", text, re.IGNORECASE)
        return m.group(0) if m else ""

    def _get_text_multi(self, page, selectors: list[str]) -> str:
        """Try each selector in order, return first non-empty text found."""
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        return text
            except Exception:
                continue
        return ""

    def _random_delay(self, min_s: float = 2.0, max_s: float = 4.0) -> None:
        time.sleep(random.uniform(min_s, max_s))

    def _check_session_age(self) -> None:
        if not self.session_path.exists():
            return
        age_days = (time.time() - os.path.getmtime(self.session_path)) / 86400
        if age_days > 25:
            logger.warning(
                f"LinkedIn session is {age_days:.0f} days old. "
                f"Refresh with setup_linkedin.bat"
            )
