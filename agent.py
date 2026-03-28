"""
Job Finder Agent — Main Orchestrator

WAT Architecture (Workflows, Agents, Tools):
  - This file is Layer 2: the Agent (decision maker + phase sequencer)
  - workflows/job_application.md defines WHAT to do
  - tools/ directory contains the deterministic executors (HOW)

Phases:
  1. parse   — PDF resume → structured JSON
  2. scrape  — LinkedIn search → list of JobListings
  3. score   — LLM rates each job 0-100 against resume + preferences
  4. approve — Human reviews top matches, approves/skips
  5. apply   — Playwright submits LinkedIn Easy Apply for approved jobs
  6. notify  — Email daily summary report

CLI Usage:
  python agent.py                          # Full pipeline
  python agent.py --phase parse            # One phase only
  python agent.py --phase approve          # Resume from approval gate (uses saved .tmp state)
  python agent.py --dry-run                # Full pipeline, no actual submissions
  python agent.py --setup-linkedin         # One-time LinkedIn session setup
  python agent.py --history                # Show application history table
  python agent.py --stats                  # Show application statistics

Phase re-run (skip already-done phases):
  python agent.py --phase score            # Assumes parse + scrape already in .tmp/
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Make agentic_base importable (sibling directory)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from agentic_base.base_agent import BaseAgent
from agentic_base.utils.state_manager import StateManager


class JobAgent(BaseAgent):

    AGENT_NAME = "job_finder"
    PHASES = ["parse", "scrape", "score", "research", "approve", "apply", "notify"]

    def __init__(self):
        super().__init__(base_dir=str(Path(__file__).parent))
        self.preferences = self._load_preferences()
        self._tracker = None
        deleted = self.state.cleanup_old_files(keep_days=3)
        if deleted:
            self.logger.info(json.dumps({"event": "tmp_cleanup", "files_deleted": deleted}))

    # ── Phase 1: Parse Resume ─────────────────────────────────────────────────

    def phase_parse(self) -> None:
        from tools.resume_parser_tool import parse_resume

        resume_path = self.preferences.get("resume_path", "config/resume.pdf")
        self.logger.info(json.dumps({"event": "parsing_resume", "path": resume_path}))

        resume = parse_resume(resume_path)

        # Override computed years with manually set value from preferences (parser can misread PDF dates)
        manual_years = self.preferences.get("personal", {}).get("total_years_experience")
        if manual_years:
            resume["total_years_experience"] = manual_years

        self.state.save("parsed_resume", resume)

        self.logger.info(json.dumps({
            "event": "resume_parsed",
            "name": resume.get("name", ""),
            "skills_count": len(resume.get("skills", [])),
            "experience_count": len(resume.get("experience", [])),
            "total_years": resume.get("total_years_experience", 0),
        }))

        try:
            from rich.console import Console
            from rich.table import Table
            console = Console()
            console.print(f"\n[green]Resume parsed:[/green] {resume.get('name', 'Unknown')}")
            console.print(f"  Skills: {len(resume.get('skills', []))}  |  "
                         f"Experience: {resume.get('total_years_experience', 0)} yrs  |  "
                         f"Jobs: {len(resume.get('experience', []))}")
        except ImportError:
            print(f"Resume parsed: {resume.get('name')} — {len(resume.get('skills', []))} skills")

    # ── Phase 2: Scrape Jobs ──────────────────────────────────────────────────

    def phase_scrape(self) -> None:
        from tools.job_source.base import get_job_source

        job_prefs  = self.preferences.get("job_search", {})
        loc_prefs  = self.preferences.get("location", {})
        scoring    = self.preferences.get("scoring", {})

        keywords   = job_prefs.get("target_titles", ["Software Engineer"])
        location   = "Remote" if loc_prefs.get("preference") == "remote" else \
                     ", ".join(loc_prefs.get("target_locations", []))
        limit      = int(os.getenv("MAX_JOBS_PER_RUN", scoring.get("max_jobs_per_run", 30)))

        filters = {
            "remote_only": loc_prefs.get("preference") == "remote",
            "experience_level": self.preferences.get("employment", {}).get("seniority_levels", []),
        }

        self.logger.info(json.dumps({
            "event": "scraping_jobs",
            "keywords": keywords,
            "location": location,
            "limit": limit,
        }))

        source = get_job_source()
        jobs   = source.fetch_jobs(keywords, location, limit, filters)

        jobs_data = [j.to_dict() for j in jobs]
        self.state.save("scraped_jobs", jobs_data)

        self.logger.info(json.dumps({"event": "scrape_complete", "count": len(jobs)}))
        print(f"\nScraped {len(jobs)} jobs from LinkedIn.")

    # ── Phase 3: Score Jobs ───────────────────────────────────────────────────

    def phase_score(self) -> None:
        from tools.job_source.base import JobListing
        from tools.scoring_tool import score_jobs_batch

        resume     = self.state.load("parsed_resume")
        jobs_data  = self.state.load("scraped_jobs")

        if not resume:
            print("No parsed resume found. Run: python agent.py --phase parse")
            sys.exit(1)
        if not jobs_data:
            print("No scraped jobs found. Run: python agent.py --phase scrape")
            sys.exit(1)

        threshold  = int(os.getenv("SCORING_THRESHOLD",
                        self.preferences.get("scoring", {}).get("threshold", 70)))

        jobs = [JobListing.from_dict(d) for d in jobs_data]

        self.logger.info(json.dumps({
            "event": "scoring_jobs",
            "total_jobs": len(jobs),
            "threshold": threshold,
        }))

        print(f"\nScoring {len(jobs)} jobs against your resume (threshold: {threshold}/100)...")

        scored = score_jobs_batch(jobs, resume, self.preferences, self.llm, threshold=0)
        above  = [j for j in scored if j["score"] >= threshold]

        self.state.save("scored_jobs", scored)
        self.state.save("jobs_above_threshold", above)

        self.logger.info(json.dumps({
            "event": "scoring_complete",
            "total_scored": len(scored),
            "above_threshold": len(above),
        }))

        # Print score summary
        try:
            from rich.console import Console
            from rich.table import Table
            from rich import box
            console = Console()
            console.print(f"\n[bold]Scoring complete:[/bold] {len(above)} of {len(scored)} jobs above threshold {threshold}\n")

            if above:
                table = Table(title="Top Matches", box=box.ROUNDED)
                table.add_column("Score", justify="center", width=7)
                table.add_column("Title", width=35)
                table.add_column("Company", width=25)
                table.add_column("Action", width=10)
                table.add_column("Easy Apply", justify="center", width=11)

                for job in above[:10]:
                    score = job["score"]
                    color = "green" if score >= 80 else "yellow" if score >= 65 else "red"
                    ea    = "[green]YES[/green]" if job.get("easy_apply") else "[dim]No[/dim]"
                    table.add_row(
                        f"[{color}]{score}[/{color}]",
                        job["title"][:33],
                        job["company"][:23],
                        job.get("recommended_action", "review").upper(),
                        ea,
                    )
                console.print(table)
        except ImportError:
            print(f"Scoring complete: {len(above)} matches above {threshold}:")
            for job in above[:5]:
                print(f"  [{job['score']}/100] {job['title']} @ {job['company']}")

    # ── Phase 4: Research Companies ───────────────────────────────────────────

    def phase_research(self) -> None:
        from tools.research_tool import research_jobs_batch

        jobs_above = self.state.load("jobs_above_threshold")
        if not jobs_above:
            print("No scored jobs found. Run: python agent.py --phase score first.")
            import sys
            sys.exit(1)

        print(f"\nResearching {len(jobs_above)} companies for H1B sponsorship signals...")

        results = research_jobs_batch(jobs_above, self.llm)
        self.state.save("research_results", results)

        # Attach research data to each job in jobs_above_threshold so the
        # approval gate can display it without loading a separate state file.
        research_map = {r["job_id"]: r for r in results}
        for job in jobs_above:
            job["research"] = research_map.get(job.get("job_id", ""), {})
        self.state.save("jobs_above_threshold", jobs_above)

        avoid_count   = sum(1 for r in results if r.get("research_verdict") == "avoid")
        caution_count = sum(1 for r in results if r.get("research_verdict") == "caution")

        self.logger.info(json.dumps({
            "event":               "research_complete",
            "companies_researched": len(results),
            "avoid_signals":        avoid_count,
            "caution_signals":      caution_count,
        }))

        print(f"Research complete: {avoid_count} avoid signals, {caution_count} caution signals")

    def phase_approve(self) -> None:
        from tools.approval_tool import present_for_approval

        jobs_above = self.state.load("jobs_above_threshold")
        if not jobs_above:
            print("No scored jobs found. Run: python agent.py --phase score first.")
            sys.exit(1)

        self.logger.info(json.dumps({
            "event": "approval_gate_start",
            "jobs_to_review": len(jobs_above),
        }))

        approved = present_for_approval(jobs_above, state_dir=str(self.base_dir / ".tmp"))
        self.state.save("approved_jobs", approved)

        self.logger.info(json.dumps({
            "event": "approval_gate_complete",
            "approved_count": len(approved),
        }))

    # ── Phase 5: Apply ────────────────────────────────────────────────────────

    def phase_apply(self) -> None:
        from tools.apply_tool import apply_to_jobs

        approved = self.state.load("approved_jobs")
        if not approved:
            print("No approved jobs found. Run: python agent.py --phase approve first.")
            sys.exit(1)

        self.logger.info(json.dumps({
            "event": "apply_start",
            "approved_count": len(approved),
            "dry_run": self.dry_run,
        }))

        resume_path = self.preferences.get("resume_path", "config/resume.pdf")
        results = apply_to_jobs(
            approved,
            self.preferences,
            self.tracker,
            resume_path=resume_path,
            dry_run=self.dry_run,
        )

        self.state.save("apply_results", results)
        self.logger.info(json.dumps({"event": "apply_complete", **{k: len(v) for k, v in results.items()}}))

        print(f"\nApply phase complete:")
        print(f"  Applied:              {len(results.get('applied', []))}")
        print(f"  Manual apply needed:  {len(results.get('skipped_manual', []))}")
        print(f"  Failed:               {len(results.get('failed', []))}")
        if self.dry_run:
            print(f"  (Dry run — nothing actually submitted)")

    # ── Phase 6: Notify ───────────────────────────────────────────────────────

    def phase_notify(self) -> None:
        from tools.email_tool import send_job_report

        # Build summary from all phase states
        scraped        = self.state.load("scraped_jobs") or []
        jobs_above     = self.state.load("jobs_above_threshold") or []
        approved       = self.state.load("approved_jobs") or []
        apply_results  = self.state.load("apply_results") or {}
        research       = self.state.load("research_results") or []

        top_matches = [
            {
                "title":   j["title"],
                "company": j["company"],
                "score":   j["score"],
                "url":     j.get("url", ""),
            }
            for j in (jobs_above or [])[:5]
        ]

        research_highlights = [
            {
                "company":            r.get("company", ""),
                "sponsorship_signal": r.get("sponsorship_signal", "unknown"),
                "verdict":            r.get("research_verdict", "unknown"),
                "verdict_reason":     r.get("verdict_reason", ""),
            }
            for r in research[:5]
        ]

        run_summary = {
            "timestamp":                 datetime.now().isoformat(),
            "jobs_scanned":              len(scraped),
            "matches_above_threshold":   len(jobs_above),
            "approved_count":            len(approved),
            "applied_count":             len(apply_results.get("applied", [])),
            "failed_count":              len(apply_results.get("failed", [])),
            "manual_apply_count":        len(apply_results.get("skipped_manual", [])),
            "pending_approval_count":    max(0, len(jobs_above) - len(approved)),
            "top_matches":               top_matches,
            "research_highlights":       research_highlights,
        }

        self.state.save("run_summary", run_summary)

        # Save to output/run_reports/
        report_dir = self.base_dir / "output" / "run_reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        with open(report_file, "w") as f:
            json.dump(run_summary, f, indent=2)

        send_job_report(run_summary)
        self.logger.info(json.dumps({"event": "notify_complete", **{
            k: v for k, v in run_summary.items() if k != "research_highlights"
        }}))

    # ── Special Flags ─────────────────────────────────────────────────────────

    def _add_custom_args(self, parser) -> None:
        parser.add_argument(
            "--setup-linkedin",
            action="store_true",
            help="One-time LinkedIn session setup (opens browser)",
        )
        parser.add_argument(
            "--history",
            action="store_true",
            help="Display application history table",
        )
        parser.add_argument(
            "--stats",
            action="store_true",
            help="Display application statistics",
        )

    def _handle_special_flags(self, args) -> None:
        super()._handle_special_flags(args)

        if getattr(args, "setup_linkedin", False):
            from tools.job_source.linkedin_scraper import LinkedInScraper
            session_path = os.getenv("LINKEDIN_SESSION_PATH", "config/linkedin_session.json")
            LinkedInScraper(session_path).setup_session()
            sys.exit(0)

        if getattr(args, "history", False):
            self.tracker.print_history()
            sys.exit(0)

        if getattr(args, "stats", False):
            stats = self.tracker.get_stats()
            print(f"\nApplication Statistics:")
            print(f"  Total applications: {stats['total']}")
            for status, count in stats.get("by_status", {}).items():
                print(f"  {status:<25} {count}")
            sys.exit(0)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def tracker(self):
        if self._tracker is None:
            from tools.tracker_tool import ApplicationTracker
            self._tracker = ApplicationTracker(str(self.base_dir / "data" / "applications.db"))
        return self._tracker

    def _load_preferences(self) -> dict:
        prefs_path = self.base_dir / "config" / "preferences.json"
        if not prefs_path.exists():
            self.logger.warning(f"preferences.json not found at {prefs_path}. Using defaults.")
            return {}
        with open(prefs_path) as f:
            return json.load(f)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    JobAgent().run()
