"""
RapidAPI Job Source — production-ready replacement for the LinkedIn scraper.

When to switch from linkedin_scraper to this:
  - LinkedIn starts blocking your session frequently
  - You want to scale to multiple users (SaaS)
  - You need better reliability and no ban risk

How to activate:
  1. Sign up at rapidapi.com → subscribe to "JSearch" (freemium, ~$0.01/request)
  2. Set in .env: JOB_SOURCE=rapidapi  and  RAPIDAPI_KEY=your_key
  3. Zero changes to agent.py — it calls the same JobSource interface

API used: JSearch by letscrape (rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
"""
import logging
from typing import Optional

import requests

from tools.job_source.base import JobListing, JobSource

logger = logging.getLogger("job_finder.rapidapi_source")

JSEARCH_URL = "https://jsearch.p.rapidapi.com/search"
RAPIDAPI_HOST = "jsearch.p.rapidapi.com"


class RapidAPISource(JobSource):

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
        }

    def fetch_jobs(
        self,
        keywords: list[str],
        location: str,
        limit: int = 30,
        filters: Optional[dict] = None,
    ) -> list[JobListing]:
        """Fetch jobs from JSearch API."""
        query = " ".join(keywords)
        if location and location.lower() != "remote":
            query = f"{query} in {location}"

        params = {
            "query": query,
            "page": "1",
            "num_pages": str(max(1, limit // 10)),
            "date_posted": "week",
        }

        if filters and filters.get("remote_only"):
            params["remote_jobs_only"] = "true"

        try:
            resp = requests.get(JSEARCH_URL, headers=self.headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"RapidAPI request failed: {e}")
            return []

        jobs = []
        for item in data.get("data", [])[:limit]:
            job = self._parse_job(item)
            if job:
                jobs.append(job)

        logger.info(f"RapidAPI returned {len(jobs)} jobs.")
        return jobs

    def get_job_detail(self, job_id: str) -> Optional[JobListing]:
        """JSearch doesn't have a single-job endpoint — return None."""
        return None

    def _parse_job(self, item: dict) -> Optional[JobListing]:
        try:
            # Build salary string if available
            salary = ""
            if item.get("job_min_salary") and item.get("job_max_salary"):
                salary = f"${item['job_min_salary']:,.0f} – ${item['job_max_salary']:,.0f}"

            return JobListing(
                item_id=item.get("job_id", ""),
                title=item.get("job_title", ""),
                company=item.get("employer_name", ""),
                location=(
                    "Remote" if item.get("job_is_remote")
                    else f"{item.get('job_city', '')}, {item.get('job_country', '')}".strip(", ")
                ),
                description=item.get("job_description", ""),
                seniority_level=item.get("job_required_experience", {}).get("required_experience_in_months", ""),
                employment_type=item.get("job_employment_type", ""),
                easy_apply=False,  # JSearch doesn't provide Easy Apply info
                url=item.get("job_apply_link", ""),
                date_posted=item.get("job_posted_at_datetime_utc", ""),
                salary_range=salary,
                applicant_count="",
                source="rapidapi_jsearch",
                raw_data=item,
            )
        except Exception as e:
            logger.warning(f"Failed to parse RapidAPI job: {e}")
            return None

    def health_check(self) -> bool:
        """Verify API key is valid."""
        try:
            resp = requests.get(
                JSEARCH_URL,
                headers=self.headers,
                params={"query": "test", "page": "1", "num_pages": "1"},
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False
