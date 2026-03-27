"""
Application Tracker — SQLite-based

Responsibilities:
  1. Prevent duplicate applications (check before applying)
  2. Record every application with status and timestamp
  3. Display history table via CLI

DB: data/applications.db
Schema: applications(job_id, title, company, location, url, score, status, applied_at, notes)

Status values: "applied" | "skipped" | "pending_approval" | "interview" | "rejected" | "offer"

SaaS migration: replace SQLite with PostgreSQL + user_id column — same interface, no other changes.

Usage:
    from tools.tracker_tool import ApplicationTracker
    tracker = ApplicationTracker()
    if not tracker.is_duplicate(job_id):
        tracker.record(job_id, title, company, ...)
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("job_finder.tracker")

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL,
    company     TEXT    NOT NULL,
    location    TEXT    DEFAULT '',
    url         TEXT    DEFAULT '',
    score       INTEGER DEFAULT 0,
    status      TEXT    DEFAULT 'applied',
    applied_at  TEXT    NOT NULL,
    notes       TEXT    DEFAULT '',
    raw_json    TEXT    DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_job_id ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_status  ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applied ON applications(applied_at);
"""


class ApplicationTracker:

    def __init__(self, db_path: str = "data/applications.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Core API ──────────────────────────────────────────────────────────────

    def is_duplicate(self, job_id: str) -> bool:
        """Return True if this job has already been recorded (applied or skipped)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
        return row is not None

    def record(
        self,
        job_id: str,
        title: str,
        company: str,
        score: int,
        status: str = "applied",
        location: str = "",
        url: str = "",
        notes: str = "",
        raw_data: Optional[dict] = None,
    ) -> None:
        """Insert or update an application record."""
        now = datetime.now().isoformat()
        raw_json = json.dumps(raw_data or {})
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO applications
                  (job_id, title, company, location, url, score, status, applied_at, notes, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  status=excluded.status,
                  notes=excluded.notes
                """,
                (job_id, title, company, location, url, score, status, now, notes, raw_json),
            )
        logger.info(f"Recorded: [{status.upper()}] {title} @ {company} (score={score})")

    def update_status(self, job_id: str, status: str, notes: str = "") -> None:
        """Update the status of an existing application (e.g., got interview)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE applications SET status=?, notes=? WHERE job_id=?",
                (status, notes, job_id),
            )

    def get_all(self, status: Optional[str] = None) -> list[dict]:
        """Return all applications, optionally filtered by status."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM applications WHERE status=? ORDER BY applied_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM applications ORDER BY applied_at DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self) -> dict:
        """Return summary statistics."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            by_status = conn.execute(
                "SELECT status, COUNT(*) as count FROM applications GROUP BY status"
            ).fetchall()
        return {
            "total": total,
            "by_status": {row["status"]: row["count"] for row in by_status},
        }

    # ── Display ───────────────────────────────────────────────────────────────

    def print_history(self, limit: int = 50) -> None:
        """Print a rich formatted table of recent applications."""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich import box
            _print_rich_table(self.get_all()[:limit])
        except ImportError:
            self._print_plain_table(self.get_all()[:limit])

    def _print_plain_table(self, records: list[dict]) -> None:
        if not records:
            print("No applications recorded yet.")
            return
        print(f"\n{'Job ID':<15} {'Score':>5} {'Status':<18} {'Title':<35} {'Company':<25} Applied")
        print("-" * 115)
        for r in records:
            date = r["applied_at"][:10]
            print(
                f"{r['job_id'][:14]:<15} {r['score']:>5} {r['status']:<18} "
                f"{r['title'][:34]:<35} {r['company'][:24]:<25} {date}"
            )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(DB_SCHEMA)


def _print_rich_table(records: list[dict]) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.style import Style

    console = Console()

    if not records:
        console.print("[yellow]No applications recorded yet.[/yellow]")
        return

    STATUS_COLORS = {
        "applied": "green",
        "skipped": "dim",
        "pending_approval": "yellow",
        "interview": "bright_green",
        "rejected": "red",
        "offer": "bright_cyan",
    }

    table = Table(
        title=f"Application History ({len(records)} records)",
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("Score", justify="center", width=6)
    table.add_column("Status", width=18)
    table.add_column("Title", width=32)
    table.add_column("Company", width=22)
    table.add_column("Location", width=18)
    table.add_column("Applied", width=12)

    for r in records:
        color = STATUS_COLORS.get(r["status"], "white")
        date = r["applied_at"][:10]
        score_color = "green" if r["score"] >= 80 else ("yellow" if r["score"] >= 60 else "red")
        table.add_row(
            f"[{score_color}]{r['score']}[/{score_color}]",
            f"[{color}]{r['status']}[/{color}]",
            r["title"][:30],
            r["company"][:20],
            r["location"][:16],
            date,
        )

    console.print(table)
