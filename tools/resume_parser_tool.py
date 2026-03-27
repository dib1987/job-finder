"""
Resume Parser Tool — PDF → structured JSON

Uses PyMuPDF (fitz) for text extraction. PyMuPDF preserves reading order
correctly on modern multi-column resumes unlike PyPDF2.

Output schema (saved to .tmp/parsed_resume_YYYYMMDD.json):
{
  "name": str,
  "email": str,
  "phone": str,
  "location": str,
  "summary": str,
  "skills": [str, ...],
  "experience": [{"title", "company", "duration", "years", "bullets": [...]}],
  "education": [{"degree", "institution", "year"}],
  "certifications": [str, ...],
  "total_years_experience": float,
  "raw_text": str
}

Usage:
    from tools.resume_parser_tool import parse_resume
    resume = parse_resume("config/resume.pdf")
"""
import re
import sys
from pathlib import Path
from typing import Optional


def parse_resume(pdf_path: str) -> dict:
    """
    Parse a resume PDF into structured JSON.
    Returns the resume dict. Raises FileNotFoundError if PDF missing.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Resume not found: {pdf_path}\n"
            f"Place your resume at: {path.resolve()}"
        )

    raw_text = _extract_text(path)
    if not raw_text.strip():
        raise ValueError(f"Could not extract text from {pdf_path}. Is it a scanned image PDF?")

    resume = {
        "name": _extract_name(raw_text),
        "email": _extract_email(raw_text),
        "phone": _extract_phone(raw_text),
        "location": _extract_location(raw_text),
        "summary": _extract_summary(raw_text),
        "skills": _extract_skills(raw_text),
        "experience": _extract_experience(raw_text),
        "education": _extract_education(raw_text),
        "certifications": _extract_certifications(raw_text),
        "raw_text": raw_text,
    }
    resume["total_years_experience"] = _compute_total_years(resume["experience"])
    return resume


# ── Text Extraction ─────────────────────────────────────────────────────────

def _extract_text(path: Path) -> str:
    """Extract all text from PDF preserving reading order."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF not installed. Run: pip install pymupdf"
        )

    doc = fitz.open(str(path))
    pages = []
    for page in doc:
        # sort=True preserves reading order (left→right, top→bottom)
        pages.append(page.get_text("text", sort=True))
    doc.close()
    return "\n".join(pages)


# ── Field Extractors ─────────────────────────────────────────────────────────

def _extract_email(text: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", text)
    return match.group(0) if match else ""


def _extract_phone(text: str) -> str:
    # Matches: +1 (555) 000-0000, 555-000-0000, (555)000-0000, etc.
    match = re.search(
        r"(\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", text
    )
    return match.group(0).strip() if match else ""


def _extract_name(text: str) -> str:
    """
    Heuristic: name is usually the first non-empty line of the resume.
    Falls back to empty string — user can fill manually in preferences.json.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        first = lines[0]
        # Reject if it looks like a title or contains digits/symbols
        if len(first) < 50 and not re.search(r"[\d@|/\\]", first):
            return first
    return ""


def _extract_location(text: str) -> str:
    """
    Look for patterns like: City, State  |  City, Country  |  Remote
    Checks the first 20 lines where contact info typically appears.
    """
    header_text = "\n".join(text.split("\n")[:20])

    # Explicit "Remote"
    if re.search(r"\bremote\b", header_text, re.IGNORECASE):
        return "Remote"

    # City, State (US format)
    match = re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s*([A-Z]{2})\b", header_text)
    if match:
        return match.group(0)

    # City, Country
    match = re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s*([A-Z][a-z]+)", header_text)
    if match:
        return match.group(0)

    return ""


def _extract_summary(text: str) -> str:
    """Extract professional summary / objective section."""
    patterns = [
        r"(?:SUMMARY|PROFILE|OBJECTIVE|ABOUT)[:\s]*\n(.*?)(?=\n[A-Z]{2,}|\Z)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            summary = match.group(1).strip()
            # Trim to first 500 chars if very long
            return summary[:500] if len(summary) > 500 else summary
    return ""


def _extract_skills(text: str) -> list[str]:
    """
    Extract skills from a SKILLS section.
    Falls back to looking for comma/pipe separated lists.
    """
    # Find skills section
    match = re.search(
        r"(?:SKILLS?|TECHNICAL SKILLS?|CORE COMPETENCIES?)[:\s]*\n(.*?)(?=\n[A-Z]{3,}|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if match:
        skills_text = match.group(1)
    else:
        skills_text = text

    # Split on comma, pipe, bullet, newline
    raw = re.split(r"[,|•·\n]", skills_text)
    skills = []
    for item in raw:
        item = item.strip().strip("–-•·").strip()
        # Filter: 2-50 chars, not a section header, not pure digits
        if 2 <= len(item) <= 50 and not item.isupper() and not item.isdigit():
            skills.append(item)

    # Deduplicate preserving order
    seen = set()
    unique = []
    for s in skills:
        if s.lower() not in seen:
            seen.add(s.lower())
            unique.append(s)

    return unique[:60]  # Cap at 60 skills


def _extract_experience(text: str) -> list[dict]:
    """
    Extract work experience entries.
    Each entry: {title, company, duration, years, bullets}
    """
    # Find experience section
    match = re.search(
        r"(?:EXPERIENCE|WORK HISTORY|EMPLOYMENT)[:\s]*\n(.*?)(?=\n(?:EDUCATION|SKILLS?|CERTIF|PROJECTS?)|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    section = match.group(1) if match else text

    experiences = []
    # Split by date patterns (job boundaries)
    # Pattern: line containing year range like "2021 – Present" or "Jan 2020 - Dec 2022"
    date_pattern = re.compile(
        r"(\d{4}\s*[–\-]\s*(?:Present|\d{4})|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{4}\s*[–\-])",
        re.IGNORECASE,
    )

    blocks = date_pattern.split(section)

    i = 0
    while i < len(blocks):
        block = blocks[i].strip()
        if not block or len(block) < 10:
            i += 1
            continue

        # Try to parse block as job entry
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            i += 1
            continue

        entry = {
            "title": "",
            "company": "",
            "duration": "",
            "years": 0.0,
            "bullets": [],
        }

        # First line is usually title or company
        if lines:
            entry["title"] = lines[0]
        if len(lines) > 1:
            entry["company"] = lines[1]

        # Capture date from next block if it's a date match
        if i + 1 < len(blocks) and date_pattern.match(blocks[i + 1].strip()):
            entry["duration"] = blocks[i + 1].strip()
            entry["years"] = _parse_years_from_duration(entry["duration"])
            i += 2
            # Collect bullet points from the block after the date
            if i < len(blocks):
                bullet_text = blocks[i]
                entry["bullets"] = _extract_bullets(bullet_text)
        else:
            i += 1

        if entry["title"]:
            experiences.append(entry)

    return experiences[:15]  # Cap at 15 entries


def _extract_bullets(text: str) -> list[str]:
    """Extract bullet points from a text block."""
    lines = text.split("\n")
    bullets = []
    for line in lines:
        line = line.strip().lstrip("•·–-▪▸►").strip()
        if len(line) > 20:  # Minimum meaningful length
            bullets.append(line)
    return bullets[:10]


def _parse_years_from_duration(duration: str) -> float:
    """
    Estimate years from a duration string.
    "2020 – 2023" → 3.0
    "Jan 2021 – Present" → years since Jan 2021
    """
    from datetime import date

    now = date.today()

    # Extract years from range
    years_found = re.findall(r"\d{4}", duration)
    if not years_found:
        return 0.0

    start_year = int(years_found[0])

    if re.search(r"present|current|now", duration, re.IGNORECASE):
        end_year = now.year
    elif len(years_found) >= 2:
        end_year = int(years_found[1])
    else:
        end_year = now.year

    return max(0.0, float(end_year - start_year))


def _extract_education(text: str) -> list[dict]:
    """Extract education entries."""
    match = re.search(
        r"(?:EDUCATION)[:\s]*\n(.*?)(?=\n(?:EXPERIENCE|SKILLS?|CERTIF|PROJECTS?)|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    section = match.group(1)
    entries = []
    lines = [l.strip() for l in section.split("\n") if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]
        # Degree keywords
        if re.search(
            r"\b(Bachelor|Master|PhD|B\.S\.|M\.S\.|B\.E\.|M\.E\.|B\.Tech|M\.Tech|MBA|Associate)\b",
            line,
            re.IGNORECASE,
        ):
            entry = {"degree": line, "institution": "", "year": ""}
            if i + 1 < len(lines):
                entry["institution"] = lines[i + 1]
            # Look for year
            year_match = re.search(r"\b(19|20)\d{2}\b", line)
            if not year_match and i + 1 < len(lines):
                year_match = re.search(r"\b(19|20)\d{2}\b", lines[i + 1])
            if year_match:
                entry["year"] = year_match.group(0)
            entries.append(entry)
        i += 1

    return entries


def _extract_certifications(text: str) -> list[str]:
    """Extract certifications."""
    match = re.search(
        r"(?:CERTIF[A-Z]*)[:\s]*\n(.*?)(?=\n[A-Z]{3,}|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    lines = [l.strip().lstrip("•·–-").strip() for l in match.group(1).split("\n")]
    return [l for l in lines if len(l) > 5][:20]


def _compute_total_years(experience: list[dict]) -> float:
    """
    Compute total years of experience.
    Uses the span from earliest start year to today — avoids double-counting
    overlapping entries that the naive sum produces.
    """
    from datetime import date
    import re

    start_years = []
    for e in experience:
        duration = e.get("duration", "")
        years_found = re.findall(r"\b(19|20)\d{2}\b", duration)
        if years_found:
            start_years.append(int(years_found[0]))

    if not start_years:
        # Fallback: use raw sum but cap at 40 years
        raw = sum(e.get("years", 0) for e in experience)
        return round(min(raw, 40), 1)

    earliest = min(start_years)
    total = date.today().year - earliest
    return round(max(0, min(total, 40)), 1)


# ── CLI standalone test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    pdf = sys.argv[1] if len(sys.argv) > 1 else "config/resume.pdf"
    print(f"Parsing: {pdf}")
    result = parse_resume(pdf)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nSkills found: {len(result['skills'])}")
    print(f"Experience entries: {len(result['experience'])}")
    print(f"Total years: {result['total_years_experience']}")
