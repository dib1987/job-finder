"""
JobSource interface and JobListing dataclass.

This is the KEY architectural seam:
  - agent.py only calls source.fetch_jobs() and source.get_job_detail()
  - The concrete implementation (scraper vs API) is selected by JOB_SOURCE in .env
  - Swapping sources = change one env var, zero agent code changes

To add a new source (e.g. Indeed, Glassdoor, Wellfound):
  1. Create tools/job_source/indeed_scraper.py
  2. Implement JobSource ABC
  3. Add entry to get_job_source() factory below
"""
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# Make agentic_base importable from this project
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent.parent.parent))

from agentic_base.interfaces.data_source import BaseItem, DataSource


@dataclass
class JobListing(BaseItem):
    """
    A single LinkedIn job listing.
    Extends BaseItem (item_id, title, source, url, raw_data).
    """
    company: str = ""
    location: str = ""
    description: str = ""          # Full job description text — fed to scorer
    seniority_level: str = ""      # "Mid-Senior", "Senior", "Director", etc.
    employment_type: str = ""      # "Full-time", "Contract", etc.
    easy_apply: bool = False       # True = can auto-apply via LinkedIn Easy Apply
    date_posted: str = ""          # "2 days ago", "2026-03-25", etc.
    salary_range: str = ""         # "$120k – $160k" if disclosed
    applicant_count: str = ""      # "Over 200 applicants" — useful for prioritizing

    def to_dict(self) -> dict:
        return {
            "job_id": self.item_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "description": self.description[:500],  # truncated for JSON storage
            "description_full": self.description,
            "seniority_level": self.seniority_level,
            "employment_type": self.employment_type,
            "easy_apply": self.easy_apply,
            "url": self.url,
            "date_posted": self.date_posted,
            "salary_range": self.salary_range,
            "applicant_count": self.applicant_count,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JobListing":
        return cls(
            item_id=d.get("job_id", ""),
            title=d.get("title", ""),
            company=d.get("company", ""),
            location=d.get("location", ""),
            description=d.get("description_full", d.get("description", "")),
            seniority_level=d.get("seniority_level", ""),
            employment_type=d.get("employment_type", ""),
            easy_apply=d.get("easy_apply", False),
            url=d.get("url", ""),
            date_posted=d.get("date_posted", ""),
            salary_range=d.get("salary_range", ""),
            applicant_count=d.get("applicant_count", ""),
            source=d.get("source", ""),
            raw_data=d,
        )


class JobSource(DataSource):
    """
    Abstract job source — specialization of DataSource for job listings.
    Concrete implementations: LinkedInScraper, RapidAPISource, etc.
    """

    @abstractmethod
    def fetch_jobs(
        self,
        keywords: list[str],
        location: str,
        limit: int = 30,
        filters: Optional[dict] = None,
    ) -> list[JobListing]:
        """
        Search for jobs matching keywords in location.
        Returns empty list on failure — never raises to caller.
        """
        ...

    # Bridge DataSource.fetch_items → fetch_jobs
    def fetch_items(self, query: str, location: str = "", limit: int = 30, filters=None):
        keywords = query.split(",") if "," in query else [query]
        return self.fetch_jobs(keywords, location, limit, filters)

    @abstractmethod
    def get_job_detail(self, job_id: str) -> Optional[JobListing]:
        """Fetch full description for a single job by ID."""
        ...

    def get_detail(self, item_id: str) -> Optional[JobListing]:
        return self.get_job_detail(item_id)


# ── Factory ──────────────────────────────────────────────────────────────────

def get_job_source(source_type: Optional[str] = None) -> JobSource:
    """
    Factory: return the configured job source.
    Reads JOB_SOURCE from env if not provided explicitly.

    To add a new source: add an elif branch here + create the implementation file.
    """
    source_type = source_type or os.getenv("JOB_SOURCE", "linkedin_scraper")

    if source_type == "linkedin_scraper":
        from tools.job_source.linkedin_scraper import LinkedInScraper
        session_path = os.getenv("LINKEDIN_SESSION_PATH", "config/linkedin_session.json")
        return LinkedInScraper(session_path=session_path)

    if source_type == "rapidapi":
        from tools.job_source.rapidapi_source import RapidAPISource
        api_key = os.getenv("RAPIDAPI_KEY")
        if not api_key:
            raise EnvironmentError("RAPIDAPI_KEY not set in .env")
        return RapidAPISource(api_key=api_key)

    raise ValueError(
        f"Unknown JOB_SOURCE: '{source_type}'. "
        f"Valid options: linkedin_scraper, rapidapi"
    )
