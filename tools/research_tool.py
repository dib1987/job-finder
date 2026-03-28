"""
Company Research Tool — LLM-based H1B sponsorship and culture signals

For each above-threshold job, this tool researches the hiring company before
the human approval gate. Based on the research-retrieval skill specification.

What it researches (per unique company):
  - H1B sponsorship history and likelihood
  - Explicit description red flags ("must be authorized without sponsorship", etc.)
  - Company culture and stability signals (layoffs, funding, Glassdoor trends)
  - A clear proceed / caution / avoid verdict with a plain-English reason

Why LLM-based (not live web scraping):
  - No extra dependencies (uses the same LLM client already in the pipeline)
  - Claude Sonnet has training data through early 2025 covering H1B petitions,
    company news, and hiring patterns for hundreds of major employers
  - For unknown companies, it reasons from description signals
  - Adds ~$0.01-0.02 per run for 5 companies — negligible cost

Usage:
    from tools.research_tool import research_jobs_batch
    results = research_jobs_batch(jobs_above_threshold, llm_client)
"""
import json
import logging
from typing import Optional

logger = logging.getLogger("job_finder.research_tool")

RESEARCH_SYSTEM_PROMPT = (
    "You are an H1B immigration specialist and company research expert. "
    "You reason carefully from your training knowledge about companies, "
    "their H1B sponsorship history, and hiring practices. "
    "When uncertain, say so clearly rather than guessing. "
    "Respond ONLY with valid JSON — no prose before or after the JSON block."
)

RESEARCH_PROMPT_TEMPLATE = """
## CANDIDATE CONTEXT
Work Authorization: H1B visa holder, I-140 approved, requires H1B sponsorship transfer
This means: the company must be willing to sponsor H1B visa transfers

## COMPANY TO RESEARCH
Company: {company}
Job Title: {job_title}

## JOB DESCRIPTION (first 1000 characters)
{description_snippet}

## RESEARCH TASK
Analyze this company and job for an H1B candidate. Check:

1. H1B SPONSORSHIP HISTORY: Does this company have a history of sponsoring H1B visas?
   Consider: large tech companies, Fortune 500, consulting firms, and startups tend to sponsor.
   Government contractors, small local businesses, and some industries rarely sponsor.

2. DESCRIPTION RED FLAGS: Does the description contain any of these blocking phrases?
   - "authorized to work without sponsorship"
   - "no sponsorship available" / "cannot sponsor"
   - "must be a US citizen" / "US citizenship required"
   - "security clearance required" / "active clearance"
   - "must be authorized" (without explicitly allowing sponsorship)

3. COMPANY SIGNALS: What do you know about this company's stability and culture?
   Consider: recent layoffs, funding rounds, acquisitions, Glassdoor reputation, size.

Respond with ONLY this JSON (no extra text):
{{
  "company": "{company}",
  "sponsorship_signal": "<likely_yes|likely_no|unknown>",
  "sponsorship_evidence": "<1-2 sentences citing specific evidence or reasoning>",
  "description_red_flags": ["<exact phrase from description if found, else empty list>"],
  "culture_signals": ["<1-2 key signals about company stability or culture>"],
  "research_verdict": "<proceed|caution|avoid>",
  "verdict_reason": "<2 sentence plain English summary for the candidate>"
}}

Verdict guide:
  proceed = strong sponsorship history, no red flags, stable company
  caution = unclear sponsorship, mixed signals, or some concerns
  avoid   = explicit no-sponsorship language, requires clearance, or strong evidence they don't sponsor
"""


# ── Core research functions ────────────────────────────────────────────────────

def research_company(
    company: str,
    job_id: str,
    job_title: str,
    description: str,
    llm,
) -> dict:
    """
    Research a single company for H1B sponsorship signals and culture.
    Returns a result dict. Never raises — returns a safe default on failure.
    """
    try:
        prompt = RESEARCH_PROMPT_TEMPLATE.format(
            company=company,
            job_title=job_title,
            description_snippet=description[:1000],
        )
        raw = llm.complete(
            prompt,
            system=RESEARCH_SYSTEM_PROMPT,
            max_tokens=600,
            temperature=0.0,
        )
        result = _parse_research_response(raw, company)
        result["job_id"] = job_id
        logger.info(
            f"Researched {company}: verdict={result.get('research_verdict')} "
            f"sponsorship={result.get('sponsorship_signal')}"
        )
        return result

    except Exception as e:
        logger.error(f"Research failed for {company} (job {job_id}): {e}")
        return _safe_default(company, job_id)


def research_jobs_batch(jobs_above: list[dict], llm) -> list[dict]:
    """
    Research all companies in the above-threshold job list.
    Deduplicates by company name — one LLM call per unique company.
    Maps results back to all job_ids from that company.

    Returns a flat list of research result dicts, one per job.
    """
    if not jobs_above:
        return []

    # Deduplicate: pick the first job_id per company as the representative
    seen_companies: dict[str, dict] = {}  # company_name → representative job dict
    for job in jobs_above:
        company = (job.get("company") or "").strip()
        if company and company not in seen_companies:
            seen_companies[company] = job

    logger.info(
        f"Researching {len(seen_companies)} unique companies "
        f"from {len(jobs_above)} above-threshold jobs..."
    )

    # Research each unique company once
    company_results: dict[str, dict] = {}
    for company, rep_job in seen_companies.items():
        result = research_company(
            company=company,
            job_id=rep_job.get("job_id", ""),
            job_title=rep_job.get("title", ""),
            description=rep_job.get("description_full") or rep_job.get("description", ""),
            llm=llm,
        )
        company_results[company] = result

    # Map results back to all jobs (multiple jobs may share a company)
    all_results = []
    for job in jobs_above:
        company = (job.get("company") or "").strip()
        if company in company_results:
            result = dict(company_results[company])  # copy
            result["job_id"] = job.get("job_id", "")  # assign this job's id
            all_results.append(result)
        else:
            all_results.append(_safe_default(company, job.get("job_id", "")))

    return all_results


# ── Parsing + defaults ─────────────────────────────────────────────────────────

def _parse_research_response(raw: str, company: str) -> dict:
    """Parse LLM JSON response. Falls back to safe default on parse failure."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(cleaned)
        # Validate required keys
        verdict = data.get("research_verdict", "unknown")
        if verdict not in ("proceed", "caution", "avoid"):
            data["research_verdict"] = "caution"
        signal = data.get("sponsorship_signal", "unknown")
        if signal not in ("likely_yes", "likely_no", "unknown"):
            data["sponsorship_signal"] = "unknown"
        return data
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error researching {company}: {e}\nRaw: {raw[:200]}")
        return _safe_default(company, "")


def _safe_default(company: str, job_id: str) -> dict:
    """Return a safe default result when research fails or cannot be parsed."""
    return {
        "company": company,
        "job_id": job_id,
        "sponsorship_signal": "unknown",
        "sponsorship_evidence": "Research could not be completed.",
        "description_red_flags": [],
        "culture_signals": [],
        "research_verdict": "caution",
        "verdict_reason": (
            "Company research was not available. Review the job description manually "
            "for any sponsorship restrictions before applying."
        ),
    }
