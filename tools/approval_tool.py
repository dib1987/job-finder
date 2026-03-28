"""
Human Approval Gate — CLI interface for reviewing scored jobs before applying.

Displays each job with score breakdown, rationale, and apply/skip controls.
State is persisted after each decision so the session can be interrupted and resumed.

Interface design (SaaS migration path):
  present_for_approval() accepts a callback function.
  CLI version: callback is called immediately after user presses a key.
  Web version: callback fires when user clicks Approve/Skip in the browser.
  Agent layer never changes — only the callback implementation changes.

Usage:
    from tools.approval_tool import present_for_approval
    approved = present_for_approval(scored_jobs, tracker)
"""
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("job_finder.approval")


@dataclass
class ApprovalDecision:
    job_id: str
    decision: str        # "apply" | "skip"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    notes: str = ""


def present_for_approval(
    scored_jobs: list[dict],
    state_dir: str = ".tmp",
    callback: Optional[Callable[[ApprovalDecision], None]] = None,
) -> list[dict]:
    """
    Show each job to the user and collect apply/skip decisions.
    Returns list of jobs the user approved for application.

    Args:
        scored_jobs:  list of job+score dicts from scoring_tool.score_jobs_batch()
        state_dir:    directory to persist approval state (resume support)
        callback:     optional function called after each decision
                      (CLI: immediate; Web: async event — future SaaS seam)
    """
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from rich import box
        console = Console()
        use_rich = True
    except ImportError:
        console = None
        use_rich = False

    state_path = Path(state_dir) / f"approval_state_{datetime.now().strftime('%Y%m%d')}.json"
    decisions = _load_state(state_path)

    # Filter out already-decided jobs
    pending = [j for j in scored_jobs if j["job_id"] not in decisions]
    already_approved = [
        j for j in scored_jobs
        if decisions.get(j["job_id"]) == "apply"
    ]

    if not pending:
        if use_rich:
            console.print(f"\n[green]All {len(scored_jobs)} jobs already reviewed.[/green]")
        return already_approved

    approved = list(already_approved)
    total = len(scored_jobs)
    pending_count = len(pending)

    if use_rich:
        console.print(f"\n[bold cyan]── Approval Gate ──────────────────────────────────────[/bold cyan]")
        console.print(f"[dim]{pending_count} jobs pending review  |  {len(already_approved)} already approved[/dim]\n")
    else:
        print(f"\n{'='*60}")
        print(f"APPROVAL GATE: {pending_count} jobs to review")
        print("="*60)

    for i, job in enumerate(pending):
        position = scored_jobs.index(job) + 1

        if use_rich:
            _display_job_rich(console, job, position, total)
        else:
            _display_job_plain(job, position, total)

        decision = _prompt_decision()
        dec = ApprovalDecision(job_id=job["job_id"], decision=decision)

        # Persist immediately
        decisions[job["job_id"]] = decision
        _save_state(state_path, decisions)

        if callback:
            callback(dec)

        if decision == "apply":
            approved.append(job)
            if use_rich:
                console.print("[green]✓ Added to apply queue[/green]\n")
            else:
                print("→ Added to apply queue\n")
        elif decision == "skip":
            if use_rich:
                console.print("[dim]→ Skipped[/dim]\n")
            else:
                print("→ Skipped\n")
        elif decision == "quit":
            if use_rich:
                console.print("[yellow]Session saved. Resume with: python agent.py --phase approve[/yellow]")
            else:
                print("Session saved. Resume with: python agent.py --phase approve")
            break

    if use_rich:
        console.print(
            f"\n[bold]Review complete: {len(approved)} jobs approved for application.[/bold]"
        )
    else:
        print(f"\nReview complete: {len(approved)} jobs approved.")

    return approved


def _display_job_rich(console, job: dict, position: int, total: int) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    score = job["score"]
    score_color = "bright_green" if score >= 80 else ("yellow" if score >= 65 else "red")
    action_color = "green" if job.get("recommended_action") == "apply" else "yellow"

    sub = job.get("sub_scores", {})
    matched = ", ".join(job.get("matched_skills", [])[:6]) or "—"
    missing = ", ".join(job.get("missing_skills", [])[:4]) or "None"
    easy_apply_badge = "[green]YES[/green]" if job.get("easy_apply") else "[red]NO (manual)[/red]"

    content = (
        f"[bold]{job['title']}[/bold]  @  [cyan]{job['company']}[/cyan]\n"
        f"[dim]{job['location']}  ·  {job.get('employment_type', '')}  ·  "
        f"Seniority: {job.get('seniority_level', '?')}[/dim]\n"
        f"Easy Apply: {easy_apply_badge}  ·  "
        f"Salary: [dim]{job.get('salary_range', 'Not disclosed')}[/dim]\n\n"
    )

    # Sponsorship warning banner
    sponsorship_warn = ""
    if job.get("sponsorship_flag"):
        sponsorship_warn = "[bold red]⚠ SPONSORSHIP BLOCKED — job description explicitly excludes H1B/visa sponsorship[/bold red]\n\n"

    # Research section (if research phase ran)
    research_section = ""
    research = job.get("research", {})
    if research:
        verdict = research.get("research_verdict", "unknown")
        verdict_color = {"proceed": "green", "caution": "yellow", "avoid": "red"}.get(verdict, "dim")
        sponsor_sig = research.get("sponsorship_signal", "unknown")
        sig_color = "green" if sponsor_sig == "likely_yes" else ("red" if sponsor_sig == "likely_no" else "dim")
        research_section = (
            f"[bold]Company Research:[/bold]\n"
            f"  Sponsorship: [{sig_color}]{sponsor_sig}[/{sig_color}]  "
            f"Verdict: [{verdict_color}]{verdict.upper()}[/{verdict_color}]\n"
            f"  [italic dim]{research.get('verdict_reason', '')}[/italic dim]\n\n"
        )

    panel_content = sponsorship_warn + content + research_section
    panel_content += f"[bold]Score Breakdown:[/bold]\n"

    sub_lines = [
        f"  Core Match (skills+exp): {sub.get('core_match', '?')}/40",
        f"  Requirements Met:        {sub.get('requirements_met', '?')}/30",
        f"  Nice-to-Haves:           {sub.get('nice_to_haves', '?')}/20",
        f"  No Deal-Breakers:        {sub.get('no_deal_breakers', '?')}/10",
    ]
    panel_content += "\n".join(sub_lines) + "\n\n"
    panel_content += f"[green]Matched skills:[/green] {matched}\n"
    panel_content += f"[yellow]Missing skills:[/yellow] {missing}\n\n"
    panel_content += f"[italic]{job.get('rationale', '')}[/italic]\n\n"
    panel_content += f"[dim]{job.get('url', '')}[/dim]"

    console.print(Panel(
        panel_content,
        title=f"[bold]Job {position} of {total}  |  Score: [{score_color}]{score}/100[/{score_color}]  [{action_color}]{job.get('recommended_action', 'review').upper()}[/{action_color}][/bold]",
        border_style="cyan",
    ))


def _display_job_plain(job: dict, position: int, total: int) -> None:
    score = job["score"]
    sub = job.get("sub_scores", {})
    print(f"\n{'='*65}")
    print(f"Job {position} of {total}  |  Score: {score}/100  [{job.get('recommended_action','review').upper()}]")
    print(f"{'='*65}")
    if job.get("sponsorship_flag"):
        print("*** WARNING: JOB BLOCKS H1B/VISA SPONSORSHIP ***")
    print(f"Title:     {job['title']}")
    print(f"Company:   {job['company']}")
    print(f"Location:  {job['location']}")
    print(f"Easy Apply:{' YES' if job.get('easy_apply') else ' NO (manual apply)'}")
    print(f"Salary:    {job.get('salary_range', 'Not disclosed')}")
    research = job.get("research", {})
    if research:
        print(f"Research:  Sponsorship={research.get('sponsorship_signal','?')}  "
              f"Verdict={research.get('research_verdict','?').upper()}")
        print(f"           {research.get('verdict_reason', '')}")
    print()
    print(f"  Core Match (skills+exp): {sub.get('core_match', '?')}/40")
    print(f"  Requirements Met:        {sub.get('requirements_met', '?')}/30")
    print(f"  Nice-to-Haves:           {sub.get('nice_to_haves', '?')}/20")
    print(f"  No Deal-Breakers:        {sub.get('no_deal_breakers', '?')}/10")
    print()
    print(f"Matched: {', '.join(job.get('matched_skills', [])[:6]) or 'None'}")
    print(f"Missing: {', '.join(job.get('missing_skills', [])[:4]) or 'None'}")
    print()
    print(f"{job.get('rationale', '')}")
    print(f"\nURL: {job.get('url', '')}")


def _prompt_decision() -> str:
    """Prompt for user decision. Returns 'apply', 'skip', or 'quit'."""
    print()
    while True:
        choice = input("[A]pply  [S]kip  [Q]uit session > ").strip().lower()
        if choice in ("a", "apply"):
            return "apply"
        if choice in ("s", "skip"):
            return "skip"
        if choice in ("q", "quit"):
            return "quit"
        print("Please enter A, S, or Q.")


def _load_state(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _save_state(path: Path, decisions: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(decisions, f, indent=2)
