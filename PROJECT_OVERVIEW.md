# Job Finder Agent — Project Overview

**Author:** Dibyendu Mondal
**Status:** Production-ready personal tool (POC → Demo-ready)
**Stack:** Python 3.13 · Claude AI · Playwright · SQLite · Gmail SMTP · Windows Task Scheduler

---

## 1. What This Project Does

An AI-powered job application agent that runs every morning, finds relevant QA/Automation
Engineer jobs on LinkedIn, scores them against your resume, researches each company for
H1B sponsorship signals, and emails you a formatted report — all without you doing anything.

You wake up, read the email, double-click `run_approve.bat`, review the top matches, and
the agent handles the rest.

**The problem it solves:**
Job searching is repetitive and exhausting. A QA engineer applying to 5-10 jobs a day
spends hours scanning LinkedIn, reading descriptions, checking companies, and filling
the same Easy Apply form. This agent automates everything except the final human judgement
call ("do I actually want this job?").

---

## 2. Architecture — WAT Pattern

The project follows a 3-layer **WAT architecture (Workflows, Agents, Tools)**:

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Workflow                                          │
│  workflows/job_application.md                               │
│  Defines WHAT to do — the phases, rules, and goals         │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│  Layer 2: Agent                                             │
│  agent.py  (JobAgent → BaseAgent)                           │
│  Decides WHEN and in what order — phase sequencer           │
│  Reads/writes state between phases via .tmp/ files          │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────┘
       │          │          │          │          │
┌──────▼──┐ ┌────▼────┐ ┌───▼────┐ ┌───▼────┐ ┌──▼──────┐
│ resume  │ │  job    │ │scoring │ │research│ │  email  │
│ parser  │ │ scraper │ │  tool  │ │  tool  │ │  tool   │
│ _tool   │ │linkedin │ │        │ │        │ │         │
└─────────┘ └─────────┘ └────────┘ └────────┘ └─────────┘
  Layer 3: Tools — HOW — deterministic executors
```

**Key rules of this architecture:**
- Tools never call each other — only the agent orchestrates
- Tools never call the agent — no circular dependencies
- Each phase reads from state, does work, writes to state
- Adding a new phase = add one method to agent.py + one tool file

---

## 3. The Pipeline — 5 Automated Phases

Every morning the agent runs these phases in sequence:

```
PARSE → SCRAPE → SCORE → RESEARCH → NOTIFY
 (1)      (2)      (3)      (4)        (5)
```

Then you manually run:
```
APPROVE → APPLY
  (6)       (7)
```

### Phase 1 — Parse Resume
**File:** `tools/resume_parser_tool.py`

Reads `config/resume.pdf` using PyMuPDF and extracts structured data:
- Name, email, phone, location
- Skills list (26 skills in your case)
- Work experience (4 positions, 12 years)
- Education

Saves to `.tmp/parsed_resume_YYYYMMDD.json`. Every subsequent phase uses this.

**Why this matters:** The LLM scorer needs structured data, not raw PDF text. A clean
parse means accurate scoring.

---

### Phase 2 — Scrape Jobs
**File:** `tools/job_source/linkedin_scraper.py`

Uses Playwright (real browser) with your saved LinkedIn session to search for jobs
matching your target titles (QA Engineer, QA Automation Engineer, QA Automation)
in your target location (United States, Remote).

For each job found:
1. Collects the job ID from search results
2. Visits the job detail page
3. Extracts: title, company, location, full description, seniority level,
   employment type, salary (if shown), Easy Apply flag

**Anti-detection measures:**
- Chrome 133 User-Agent string (configurable via `LINKEDIN_USER_AGENT`)
- Random 2-4 second delays between page loads
- Exponential backoff retry (3s, then 8s) on failed fetches
- Stops scraping if LinkedIn blocks with authwall/checkpoint

**Output:** Up to 30 job listings in `.tmp/scraped_jobs_YYYYMMDD.json`

---

### Phase 3 — Score Jobs
**File:** `tools/scoring_tool.py`

Each job description is sent to Claude AI with your resume and a 4-dimension scoring rubric:

| Dimension | Weight | What it checks |
|---|---|---|
| Core Match | 40% | Do your skills and experience match the job requirements? |
| Requirements Met | 30% | Seniority level, location preference, work authorization |
| Nice-to-Haves | 20% | Salary range, company size, industry preference |
| No Deal-Breakers | 10% | Explicit no-sponsorship language, citizenship requirements |

**Output per job:**
```json
{
  "score": 82,
  "sub_scores": {"core_match": 34, "requirements_met": 26, "nice_to_haves": 16, "no_deal_breakers": 10},
  "sponsorship_flag": false,
  "matched_skills": ["Selenium", "Python", "CI/CD"],
  "missing_skills": ["Kubernetes"],
  "rationale": "Strong automation background, matches seniority level",
  "recommended_action": "apply"
}
```

Jobs above the threshold (default: 60/100) go into `jobs_above_threshold` state.
Today's run: 25 jobs scraped → 9-11 above threshold.

---

### Phase 4 — Research Companies
**File:** `tools/research_tool.py`

For each above-threshold company, asks Claude to research:
1. **H1B sponsorship history** — does this company have a record of sponsoring visas?
2. **Description red flags** — exact phrases like "must be authorized without sponsorship"
3. **Company stability** — layoffs, funding, Glassdoor reputation, size

**Why LLM-based (not live web scraping):**
- No extra dependencies or API costs
- Claude's training data covers H1B petition records and company news through early 2025
- Adds ~$0.01 per company — negligible
- Deduplicates by company name: 11 jobs at 3 companies = 3 LLM calls, not 11

**Output per company:**
```json
{
  "sponsorship_signal": "likely_yes",
  "research_verdict": "proceed",
  "verdict_reason": "Mastercard filed 200+ H1B petitions in 2023-2024. No red flags in description.",
  "description_red_flags": []
}
```

Verdicts: `proceed` (green) | `caution` (yellow) | `avoid` (red)

---

### Phase 5 — Notify
**File:** `tools/email_tool.py`

Builds and sends an HTML email to your Gmail with:
- Run summary (jobs scanned, matches found, pending your review)
- Top 5 matches with scores, company, and LinkedIn link
- Company Research section with H1B sponsorship badges per company
- Color-coded score badges (Excellent 85+, Good 70+, Partial 50+, Poor)
- Alert if any jobs are awaiting your approval

**Transport:** Gmail SMTP over SSL port 465 using an App Password.

---

### Phase 6 — Approve (Manual)
**File:** `tools/approval_tool.py`
**How to run:** Double-click `run_approve.bat`

Interactive terminal review. For each above-threshold job, shows:
- Score breakdown across all 4 dimensions
- Matched vs missing skills
- Research verdict (H1B signal + reason)
- Red sponsorship warning if `sponsorship_flag = true`
- LinkedIn URL

You press **A** (approve), **S** (skip), or **Q** (quit and save progress).

---

### Phase 7 — Apply (Manual)
**File:** `tools/apply_tool.py`
**How to run:** `python agent.py --phase apply`

For each approved job, uses Playwright to:
1. Navigate to the LinkedIn job page
2. Click "Easy Apply"
3. Fill contact fields (name, email, phone) from `preferences.json`
4. Upload `config/resume.pdf`
5. Step through multi-page form
6. Click Submit
7. Record result in SQLite tracker

Jobs without Easy Apply are flagged as `manual_apply_needed` — you apply on the company
site yourself, and the tracker reminds you.

---

## 4. State Management

Phases pass data to each other via dated JSON files in `.tmp/`:

```
.tmp/
  parsed_resume_20260328.json       ← Phase 1 writes
  scraped_jobs_20260328.json        ← Phase 2 writes
  scored_jobs_20260328.json         ← Phase 3 writes
  jobs_above_threshold_20260328.json ← Phase 3 writes, Phase 4 enriches
  research_results_20260328.json    ← Phase 4 writes
  approved_jobs_20260328.json       ← Phase 6 writes
  apply_results_20260328.json       ← Phase 7 writes
  run_summary_20260328.json         ← Phase 5 writes
```

Files older than 3 days are automatically deleted on agent startup (`cleanup_old_files`).

This design means:
- Any phase can be re-run independently: `python agent.py --phase score`
- If scraping takes 8 minutes and scoring crashes, you don't re-scrape
- Running approve the next morning still works because the state files are there

---

## 5. Duplicate Prevention

`tools/tracker_tool.py` maintains a SQLite database at `data/applications.db`.

Before applying to any job, the agent checks:
```python
if tracker.is_duplicate(job_id):
    skip  # Already applied or recorded
```

The database also tracks every application outcome:

| Status | Meaning |
|---|---|
| `applied` | Successfully submitted via Easy Apply |
| `manual_apply_needed` | No Easy Apply — apply on company site |
| `dry_run` | Test run — not actually submitted |
| `failed` | Easy Apply started but couldn't complete |
| `interview` | Update manually when you get a response |
| `rejected` | Update manually |
| `offer` | Update manually |

View history: `python agent.py --history`
View stats: `python agent.py --stats`

---

## 6. Scheduling

**File:** `scheduler.py` + `run_daily.bat`

The daily automated run is registered in Windows Task Scheduler:

```
Every day at 08:00 AM EST
→ runs run_daily.bat
→ which runs: python agent.py --daily
→ which runs: parse → scrape → score → research → notify
→ email arrives in inbox ~10 minutes later
```

**Key settings applied:**
- `StartWhenAvailable = True` — runs on next login if machine was off at 8 AM
- `DisallowStartIfOnBatteries = False` — runs even on battery (critical fix)
- `ExecutionTimeLimit = PT2H` — auto-kills after 2 hours if hung
- `--daily` flag — only non-interactive phases, never blocks for input

**If the run fails:** `run_daily.bat` sends an error email so you're not left wondering why no report arrived.

**Manage the schedule:**
```bash
python scheduler.py --status       # Check next run time
python scheduler.py --run-now      # Trigger immediately
python scheduler.py --install --time 08:00  # Change time
python scheduler.py --remove       # Uninstall
```

---

## 7. Key Design Decisions & Why

### Job Source Abstraction
`tools/job_source/base.py` defines a `JobSource` ABC. `agent.py` only calls `source.fetch_jobs()`. The concrete implementation is selected by `JOB_SOURCE` in `.env`:

```
JOB_SOURCE=linkedin_scraper   ← current (free, personal use)
JOB_SOURCE=rapidapi           ← future (paid API, cloud-compatible)
```

Switching to cloud hosting = change one line in `.env`. Zero code changes.

### Human-in-the-Loop (Non-Negotiable)
The approval gate (`phase_approve`) is always a manual step. The agent will never apply
to a job without explicit human approval. `auto_apply_above` in `preferences.json` allows
bypassing for scores ≥90 — but it's off by default and should only be enabled after
extensive testing.

### LLM Abstraction
`agentic_base/utils/llm_client.py` provides `get_llm_client()` which reads `LLM_MODEL`
from env. The same interface works regardless of provider:

```
LLM_MODEL=claude-sonnet-4-6   ← current
LLM_MODEL=gpt-4o              ← OpenAI alternative
LLM_MODEL=gemini-1.5-pro      ← Google alternative
```

### Shared Base Framework
`agentic_base/` (sibling directory at `c:\Agentic Workflow\agentic_base`) is shared
across multiple agents (Job Finder, HospitalService, SocialMedia). It provides:
- `BaseAgent` — phase sequencing, CLI parsing, logging
- `StateManager` — .tmp/ file read/write/cleanup
- `LLMClient` — provider-agnostic LLM calls
- Interfaces: `DataSource`, `BaseItem`

Adding a new agent = subclass `BaseAgent`, define `PHASES`, implement `phase_X()` methods.

---

## 8. Custom Skills Integration

Three custom Claude skills were created as SKILL.md prompt instructions and then
translated into Python tools for the automated pipeline:

| Skill | Interactive Use | Python Implementation |
|---|---|---|
| `research-retrieval` | Research any company or topic on demand | `tools/research_tool.py` (Phase 4) |
| `smart-match-scorer` | Score any items against any criteria | `tools/scoring_tool.py` (Phase 3) |
| `email-reporter` | Send formatted reports to your inbox | `tools/email_tool.py` (Phase 5) |

**The skills define the contract — the Python tools implement it.**

Each skill now contains an "Implementation Contract" section documenting:
- Exact function signatures
- Standard input/output schemas
- What is fixed (rubric, SMTP logic, deduplication pattern)
- What changes per project (prompts, sections, criteria)

This means the next agent (hospital-matching, vendor-vetting, etc.) can reuse the
same patterns — only the domain-specific prompts change.

---

## 9. File Structure

```
c:\Agentic Workflow\Job Finder\
│
├── agent.py                    ← Main orchestrator (Layer 2)
├── scheduler.py                ← Windows Task Scheduler integration
│
├── run_daily.bat               ← Automated daily run (called by scheduler)
├── run_approve.bat             ← Manual approval gate (run after email)
├── run_dryrun.bat              ← Full pipeline test (no submissions)
│
├── workflows/
│   └── job_application.md     ← Workflow definition (Layer 1)
│
├── tools/                     ← All tool implementations (Layer 3)
│   ├── resume_parser_tool.py  ← PDF → structured JSON
│   ├── scoring_tool.py        ← LLM job scoring (4-dimension rubric)
│   ├── research_tool.py       ← LLM company H1B research
│   ├── approval_tool.py       ← Interactive terminal review gate
│   ├── apply_tool.py          ← Playwright Easy Apply automation
│   ├── email_tool.py          ← Gmail SMTP reporting
│   ├── tracker_tool.py        ← SQLite duplicate prevention + history
│   └── job_source/
│       ├── base.py            ← JobSource ABC + JobListing dataclass
│       ├── linkedin_scraper.py ← Playwright LinkedIn scraper (current)
│       └── rapidapi_source.py  ← RapidAPI source (future cloud use)
│
├── config/
│   ├── resume.pdf             ← Your resume (submitted as-is)
│   ├── preferences.json       ← Job search preferences + personal info
│   └── linkedin_session.json  ← Saved browser session (gitignored)
│
├── data/
│   └── applications.db        ← SQLite tracker (gitignored)
│
├── logs/                      ← One JSONL log file per run (auto-cleaned after 3 days)
├── output/run_reports/        ← JSON report per run
├── .tmp/                      ← Inter-phase state files (auto-cleaned after 3 days)
└── .env                       ← All credentials and config (gitignored)
```

---

## 10. Environment Variables

```bash
# LLM
ANTHROPIC_API_KEY=...              # Required — Claude API
LLM_MODEL=claude-sonnet-4-6       # Swap to gpt-4o or gemini-1.5-pro

# LinkedIn
LINKEDIN_SESSION_PATH=config/linkedin_session.json
LINKEDIN_USER_AGENT=Mozilla/5.0 ... Chrome/133.0.0.0 ...  # Updatable without code change

# Job Source
JOB_SOURCE=linkedin_scraper        # Switch to rapidapi for cloud hosting

# Email (unified — one set for all projects)
EMAIL_FROM=your@gmail.com
EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # Gmail App Password (16 chars)
EMAIL_TO=your@gmail.com

# Behavior
SCORING_THRESHOLD=60               # Override without editing preferences.json
MAX_JOBS_PER_RUN=30                # Keep ≤30 to reduce LinkedIn ban risk
DRY_RUN=false                      # Set true to test without submitting
PYTHONUTF8=1                       # Required on Windows — prevents emoji encoding errors
```

---

## 11. What Was Built During This Session

This section documents what was added/changed from the initial POC:

| What | Files | Why |
|---|---|---|
| Research Phase (new) | `tools/research_tool.py`, `agent.py` | H1B candidates need sponsorship signals before applying — critical for Dibyendu |
| Email unification | `tools/email_tool.py` | Two conflicting email systems (notifier_tool + email-reporter skill) — unified into one |
| `--daily` flag | `agent.py` | `--phase X` runs from X to end of pipeline — caused scheduler to hang at `approve` |
| Scoring enhancement | `tools/scoring_tool.py` | Aligned with smart-match-scorer 4-dimension rubric; added `sponsorship_flag` |
| Approval gate enrichment | `tools/approval_tool.py` | Shows research verdict + sponsorship warning per job |
| Scraper hardening | `tools/job_source/linkedin_scraper.py` | Updated Chrome UA to 133, added retry with backoff, added CAPTCHA fallback |
| State cleanup | `agentic_base/utils/state_manager.py` | .tmp/ was growing 8 files/day with no cleanup |
| Scheduling fixed | `scheduler.py`, `run_daily.bat` | Battery restriction was silently blocking all scheduled runs |
| Skill contracts | `~/.claude/skills/*/SKILL.md` | Skills now document Python interfaces for reuse across agents |

---

## 12. Known Limitations & Future Roadmap

### Current Limitations

| Limitation | Impact | Fix When |
|---|---|---|
| LinkedIn scraper breaks if IP is flagged | 0 jobs scraped, email shows no matches | Switch to RapidAPI |
| Session expires ~weekly | Scraper fails, needs `--setup-linkedin` | RapidAPI removes this entirely |
| Resume sent as-is (no tailoring) | Slightly lower match quality | Phase 2 feature: tailoring_tool.py |
| Windows-only scheduler | Can't run unattended on cloud | Move to Railway/GitHub Actions + RapidAPI |
| LLM research uses training data only | Company info may be outdated post-Aug 2025 | Add optional live web search via Serper/Tavily API |

### Roadmap

**Near-term (next iteration):**
- Switch to `JOB_SOURCE=rapidapi` — removes Playwright dependency, enables cloud hosting
- Deploy to Railway or GitHub Actions — runs even when laptop is off
- Resume tailoring: generate a customized cover note or highlights section per job

**Future (if selling to businesses):**
- Web UI for the approval gate (replace terminal prompts with browser interface)
- Multi-user support: `user_id` column in SQLite → PostgreSQL
- Multiple job boards: Indeed, Glassdoor, Wellfound via same `JobSource` ABC
- Slack/SMS notifications as alternative to email
- Analytics dashboard: application funnel, response rates, interview conversion

---

## 13. Lessons Learned

### On AI Agent Architecture
1. **WAT pattern works.** Separating workflow (what), agent (when), and tools (how) made
   every debug session faster — you always knew exactly which layer had the problem.

2. **Phases must be stateless and restartable.** Writing to `.tmp/` after each phase means
   a crash at Phase 4 doesn't lose Phase 2's 8 minutes of scraping. Always design for
   mid-pipeline recovery.

3. **Scheduled tasks must never block.** Any `input()` call in a scheduled process will
   hang silently forever. The `--daily` flag pattern (explicit non-interactive phase list)
   is the right architectural answer.

4. **LLM for research, not just generation.** Using Claude's training knowledge to research
   company H1B history was ~$0.01/company and more reliable than scraping H1B disclosure
   databases. Training data as a knowledge base is underused.

### On Skills as Blueprints
5. **Skills are design documents, not code.** SKILL.md files tell Claude how to behave
   interactively. Their value is as *specifications* — reading them before building a
   tool produces better implementations than starting from scratch.

6. **Implementation contracts enable reuse.** Adding function signatures and I/O schemas
   to SKILL.md means the next agent can be built in a fraction of the time. The domain
   changes; the pattern doesn't.

### On Windows Automation
7. **Default Task Scheduler settings are wrong for personal automation.** "Do not start
   on batteries" is the default — it silently blocks every run on a laptop. Always set
   `DisallowStartIfOnBatteries = False` and `StartWhenAvailable = True`.

8. **Error notifications are mandatory.** A pipeline that fails silently is worse than
   no pipeline. The error email in `run_daily.bat` was added after experiencing exactly
   this — no email arrived and there was no way to know why without checking logs manually.

### On H1B Job Searching Specifically
9. **Sponsorship detection needs two layers.** Description-level (`sponsorship_flag` from
   scorer) catches explicit phrases. Company-level (`research_verdict` from research tool)
   catches companies with a history of not sponsoring even when the description is silent.
   Either layer alone misses cases.

10. **Score thresholds need tuning.** Starting at 70 was too aggressive — only 2-3 jobs
    passed. Lowering to 60 gave 9-11 matches per day, which is a healthier pipeline.
    Threshold should be adjusted based on how many approvals feel right per day.

---

## 14. How to Run — Quick Reference

```bash
# Daily (automated via Task Scheduler at 8 AM)
# Nothing to do — check email

# After receiving email
run_approve.bat              # Review and approve jobs
python agent.py --phase apply  # Submit applications

# Test the full pipeline
run_dryrun.bat               # Full run, no submissions

# Debugging individual phases
python agent.py --phase parse
python agent.py --phase scrape
python agent.py --phase score
python agent.py --phase research
python agent.py --phase notify

# View history
python agent.py --history
python agent.py --stats

# Scheduler management
python scheduler.py --status
python scheduler.py --run-now    # Test trigger immediately
python scheduler.py --remove
python scheduler.py --install --time 08:00
```
