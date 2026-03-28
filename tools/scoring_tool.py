"""
Job Scoring Tool — LLM-based 0-100 relevance scorer

Scores each job against the candidate's resume and preferences using the
smart-match-scorer 4-dimension weighted framework:

  Core Match          (0-40 pts): skills + experience alignment
  Requirements Met    (0-30 pts): seniority level + location + work authorization
  Nice-to-Haves       (0-20 pts): compensation + company/industry fit
  No Deal-Breakers    (0-10 pts): sponsorship blocks, citizenship requirements

Output per job:
  score:               0-100 total
  sub_scores:          {"core_match": 36, "requirements_met": 25, "nice_to_haves": 16, "no_deal_breakers": 10}
  sponsorship_flag:    True if job explicitly blocks H1B sponsorship
  matched_skills:      skills from resume that match job requirements
  missing_skills:      required skills not found in resume
  rationale:           2-3 sentence human explanation
  recommended_action:  "apply" | "review" | "skip"

Usage:
    from tools.scoring_tool import score_job, score_jobs_batch
    result = score_job(job, resume, preferences, llm_client)
"""
import json
import logging
import sys
from typing import Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent.parent))

from agentic_base.interfaces.llm_scorer import LLMScorer, ScoringResult
from agentic_base.utils.llm_client import LLMClient
from tools.job_source.base import JobListing

logger = logging.getLogger("job_finder.scoring_tool")

SCORING_SYSTEM_PROMPT = (
    "You are a senior career coach and technical recruiter. "
    "You score job fit precisely and objectively. "
    "Respond ONLY with valid JSON — no prose before or after the JSON block."
)

SCORING_PROMPT_TEMPLATE = """
## CANDIDATE RESUME
Name: {name}
Location: {location}
Total Experience: {total_years} years (12 years total, 4 years in the US)
Work Authorization: {work_auth} — requires sponsorship: {requires_sponsorship}
Skills: {skills}

Experience:
{experience_summary}

Education:
{education_summary}

## JOB LISTING
Title: {job_title}
Company: {job_company}
Location: {job_location}
Employment Type: {employment_type}
Seniority Level: {seniority_level}
Salary Range: {salary_range}
Easy Apply: {easy_apply}

Description:
{job_description}

## CANDIDATE PREFERENCES
Target titles: {target_titles}
Location preference: {location_preference}
Minimum salary: ${min_salary:,}
Industries: {target_industries}
Employment types: {employment_types}
Seniority levels: {seniority_levels}

## SCORING TASK
Score this job 0-100 using the 4-dimension rubric below. Be strict and accurate.

RUBRIC:
  Core Match        (0-40 pts): Skills (0-25) + experience seniority alignment (0-15).
                                 25=all required skills present, 18=70%+ present, 10=50-70%, 0=under 50%.
                                 15=perfect seniority match, 8=one level off, 0=two+ levels off.
  Requirements Met  (0-30 pts): Location (0-15) + work authorization (0-15).
                                 Location: 15=matches preference exactly, 8=negotiable/partial, 0=hard mismatch.
                                 Auth: 15=no sponsorship conflict, 0=job explicitly says no sponsorship/must be citizen.
  Nice-to-Haves     (0-20 pts): Compensation (0-10) + company/industry fit (0-10).
                                 Comp: 10=salary meets/exceeds preference, 5=not listed, 0=below preference.
                                 Company: 10=industry+type match, 5=partial, 0=clear mismatch.
  No Deal-Breakers  (0-10 pts): 10=no blocking language found. Deduct for:
                                 "must be authorized without sponsorship", "no visa", "US citizens only",
                                 "must have security clearance", "active clearance required".
                                 0=any hard deal-breaker present.

SPONSORSHIP FLAG: Set sponsorship_flag=true if ANY of these appear in the description:
  "authorized to work without sponsorship", "no sponsorship", "cannot sponsor",
  "must be a US citizen", "US citizenship required", "security clearance required",
  "active clearance", "must be authorized", "without visa support"

score = sum of all four dimension scores (must equal sub_scores sum).

Respond with ONLY this JSON (no extra text):
{{
  "score": <integer 0-100>,
  "sub_scores": {{
    "core_match": <0-40>,
    "requirements_met": <0-30>,
    "nice_to_haves": <0-20>,
    "no_deal_breakers": <0-10>
  }},
  "sponsorship_flag": <true|false>,
  "matched_skills": ["skill1", "skill2"],
  "missing_skills": ["skill3", "skill4"],
  "rationale": "<2-3 sentence plain English explanation>",
  "recommended_action": "<apply|review|skip>"
}}
"""


class JobScorer(LLMScorer):
    """Scores JobListing objects against a resume + preferences dict."""

    def score(
        self,
        item: JobListing,
        criteria: dict,
        llm_client: LLMClient,
    ) -> ScoringResult:
        """Score a single job. Returns ScoringResult(score=0) on any failure."""
        try:
            prompt = self._build_prompt(item, criteria)
            raw = llm_client.complete(
                prompt,
                system=SCORING_SYSTEM_PROMPT,
                max_tokens=1024,
                temperature=0.0,  # Deterministic scoring
            )
            return self._parse_response(item.item_id, raw)
        except Exception as e:
            logger.error(f"Scoring failed for job {item.item_id}: {e}")
            return ScoringResult(
                item_id=item.item_id,
                score=0,
                rationale=f"Scoring failed: {e}",
                recommended_action="skip",
            )

    def _build_prompt(self, job: JobListing, criteria: dict) -> str:
        resume = criteria.get("resume", {})
        prefs = criteria.get("preferences", {})

        # Format experience summary (top 3 roles)
        exp_lines = []
        for exp in resume.get("experience", [])[:3]:
            exp_lines.append(
                f"  - {exp.get('title', '')} at {exp.get('company', '')} "
                f"({exp.get('duration', '')})"
            )

        edu_lines = []
        for edu in resume.get("education", []):
            edu_lines.append(f"  - {edu.get('degree', '')} from {edu.get('institution', '')}")

        job_prefs = prefs.get("job_search", {})
        loc_prefs = prefs.get("location", {})
        comp_prefs = prefs.get("compensation", {})
        emp_prefs = prefs.get("employment", {})

        personal = prefs.get("personal", {})
        # Use manually set experience years from preferences if available
        total_years = personal.get("total_years_experience") or resume.get("total_years_experience", 0)
        work_auth = personal.get("work_authorization", "Not specified")
        requires_sponsorship = personal.get("requires_sponsorship", False)

        return SCORING_PROMPT_TEMPLATE.format(
            name=resume.get("name", "Candidate"),
            location=personal.get("location_city") or resume.get("location", "Not specified"),
            total_years=total_years,
            work_auth=work_auth,
            requires_sponsorship=requires_sponsorship,
            skills=", ".join(resume.get("skills", [])[:30]),
            experience_summary="\n".join(exp_lines) or "  Not extracted",
            education_summary="\n".join(edu_lines) or "  Not extracted",
            job_title=job.title,
            job_company=job.company,
            job_location=job.location,
            employment_type=job.employment_type,
            seniority_level=job.seniority_level,
            salary_range=job.salary_range or "Not disclosed",
            easy_apply="Yes" if job.easy_apply else "No",
            job_description=job.description[:3000],  # Cap to avoid token limits
            target_titles=", ".join(job_prefs.get("target_titles", [])),
            location_preference=loc_prefs.get("preference", "any"),
            min_salary=comp_prefs.get("minimum_usd_annual", 0),
            target_industries=", ".join(job_prefs.get("target_industries", [])),
            employment_types=", ".join(emp_prefs.get("types", [])),
            seniority_levels=", ".join(emp_prefs.get("seniority_levels", [])),
        )

    def _parse_response(self, job_id: str, raw: str) -> ScoringResult:
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error for job {job_id}: {e}\nRaw: {raw[:200]}")
            return ScoringResult(
                item_id=job_id,
                score=0,
                rationale="Could not parse LLM response",
                recommended_action="review",
                raw_response=raw,
            )

        result = ScoringResult(
            item_id=job_id,
            score=max(0, min(100, int(data.get("score", 0)))),
            sub_scores=data.get("sub_scores", {}),
            matched=data.get("matched_skills", []),
            missing=data.get("missing_skills", []),
            rationale=data.get("rationale", ""),
            recommended_action=data.get("recommended_action", "review"),
            raw_response=raw,
        )
        # Attach sponsorship_flag as extra attribute (not in base ScoringResult)
        result.sponsorship_flag = bool(data.get("sponsorship_flag", False))
        return result


# ── Convenience functions ────────────────────────────────────────────────────

_scorer = JobScorer()


def score_job(
    job: JobListing,
    resume: dict,
    preferences: dict,
    llm_client: LLMClient,
) -> ScoringResult:
    """Score a single job against the candidate's resume and preferences."""
    criteria = {"resume": resume, "preferences": preferences}
    return _scorer.score(job, criteria, llm_client)


def score_jobs_batch(
    jobs: list[JobListing],
    resume: dict,
    preferences: dict,
    llm_client: LLMClient,
    threshold: int = 0,
) -> list[dict]:
    """
    Score all jobs and return sorted list of dicts (highest score first).
    If threshold > 0, filters out jobs below the threshold.
    """
    criteria = {"resume": resume, "preferences": preferences}
    results = _scorer.score_batch(jobs, criteria, llm_client)

    # Build combined job+score dicts for storage
    scored = []
    job_map = {j.item_id: j for j in jobs}
    for result in results:
        if threshold > 0 and result.score < threshold:
            continue
        job = job_map.get(result.item_id)
        if not job:
            continue
        scored.append({
            **job.to_dict(),
            "score": result.score,
            "sub_scores": result.sub_scores,
            "sponsorship_flag": getattr(result, "sponsorship_flag", False),
            "matched_skills": result.matched,
            "missing_skills": result.missing,
            "rationale": result.rationale,
            "recommended_action": result.recommended_action,
        })

    return sorted(scored, key=lambda x: x["score"], reverse=True)


# ── CLI standalone test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from dotenv import load_dotenv
    load_dotenv()

    from agentic_base.utils.llm_client import get_llm_client

    # Quick test with a fake job
    fake_job = JobListing(
        item_id="test_001",
        title="Senior Python Engineer",
        company="Stripe",
        location="Remote",
        description=(
            "We're looking for a Senior Python Engineer to join our platform team. "
            "You'll build scalable APIs with Python, FastAPI, and PostgreSQL. "
            "Required: 5+ years Python, REST APIs, SQL databases. "
            "Nice to have: Kubernetes, Go, distributed systems experience."
        ),
        seniority_level="Senior",
        employment_type="Full-time",
        easy_apply=True,
        url="https://linkedin.com/jobs/view/test_001",
        source="test",
    )

    fake_resume = {
        "name": "Test Candidate",
        "location": "Remote",
        "total_years_experience": 6,
        "skills": ["Python", "FastAPI", "PostgreSQL", "REST APIs", "Docker", "AWS"],
        "experience": [
            {"title": "Senior Developer", "company": "Acme Corp", "duration": "2021–Present", "bullets": []},
        ],
        "education": [{"degree": "B.S. Computer Science", "institution": "State University", "year": "2018"}],
    }

    fake_prefs = {
        "job_search": {
            "target_titles": ["Senior Software Engineer"],
            "target_industries": ["fintech", "SaaS"],
        },
        "location": {"preference": "remote"},
        "compensation": {"minimum_usd_annual": 130000},
        "employment": {"types": ["full_time"], "seniority_levels": ["senior", "staff"]},
    }

    llm = get_llm_client()
    result = score_job(fake_job, fake_resume, fake_prefs, llm)

    print(f"\nScore: {result.score}/100  [{result.recommended_action.upper()}]")
    print(f"Sub-scores: {result.sub_scores}")
    print(f"Matched: {result.matched}")
    print(f"Missing: {result.missing}")
    print(f"Rationale: {result.rationale}")
