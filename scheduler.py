"""
Scheduler — Windows Task Scheduler integration

Registers the job agent to run automatically every day at a specified time.
Uses Windows Task Scheduler (schtasks) — no additional dependencies needed.

Commands:
  python scheduler.py --install --time 08:00    # Run daily at 8 AM
  python scheduler.py --remove                   # Remove the scheduled task
  python scheduler.py --status                   # Check if task is registered
  python scheduler.py --run-now                  # Trigger immediately (test)

SaaS migration: replace with Celery Beat, APScheduler, or Railway cron.
The agent.py interface is unchanged — only the trigger mechanism changes.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "JobFinderAgent"


def install(run_time: str = "08:00") -> None:
    """Register a daily Windows Task Scheduler entry."""
    agent_script = Path(__file__).parent / "agent.py"
    python_exe   = sys.executable

    if not agent_script.exists():
        print(f"Error: agent.py not found at {agent_script}")
        sys.exit(1)

    # Build the schtasks command
    # /SC DAILY — run every day
    # /ST HH:MM — start time
    # /TR — task to run
    # /F — force (overwrite if exists)
    cmd = [
        "schtasks", "/Create",
        "/TN", TASK_NAME,
        "/TR", f'"{python_exe}" "{agent_script}" --dry-run=false',
        "/SC", "DAILY",
        "/ST", run_time,
        "/F",  # Force overwrite
        "/RL", "LIMITED",
    ]

    print(f"Registering task: {TASK_NAME}")
    print(f"  Script: {agent_script}")
    print(f"  Time:   {run_time} daily")
    print(f"  Python: {python_exe}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"\nTask registered successfully.")
        print(f"The agent will run every day at {run_time}.")
        print(f"\nTo check status: python scheduler.py --status")
        print(f"To remove:       python scheduler.py --remove")
    else:
        print(f"\nFailed to register task.")
        print(f"Error: {result.stderr}")
        print(f"\nTroubleshooting:")
        print(f"  - Run this command as Administrator")
        print(f"  - Or use the cross-platform fallback: python scheduler.py --install-python")


def remove() -> None:
    """Remove the scheduled task."""
    cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' removed successfully.")
    else:
        if "cannot find" in result.stderr.lower():
            print(f"Task '{TASK_NAME}' not found — nothing to remove.")
        else:
            print(f"Error removing task: {result.stderr}")


def status() -> None:
    """Check if the scheduled task is registered."""
    cmd = ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' is registered:\n")
        print(result.stdout)
    else:
        print(f"Task '{TASK_NAME}' is NOT registered.")
        print("Run: python scheduler.py --install --time 08:00")


def run_now() -> None:
    """Trigger the task immediately (for testing)."""
    cmd = ["schtasks", "/Run", "/TN", TASK_NAME]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' triggered successfully.")
    else:
        print(f"Failed to trigger task: {result.stderr}")


def install_python_fallback(run_time: str = "08:00") -> None:
    """
    Cross-platform fallback using the 'schedule' library.
    Runs in the foreground (keep terminal open / use with a process manager).
    Install: pip install schedule
    """
    try:
        import schedule
        import time as time_module
        import subprocess as sp
    except ImportError:
        print("Install schedule: pip install schedule")
        return

    agent_script = Path(__file__).parent / "agent.py"
    python_exe   = sys.executable

    def run_agent():
        print(f"\n[Scheduler] Running agent at {run_time}...")
        sp.run([python_exe, str(agent_script)])

    schedule.every().day.at(run_time).do(run_agent)
    print(f"Python scheduler running. Agent will execute daily at {run_time}.")
    print("Keep this terminal open. Press Ctrl+C to stop.\n")

    while True:
        schedule.run_pending()
        time_module.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Agent Scheduler")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="Register daily task")
    group.add_argument("--remove",  action="store_true", help="Remove scheduled task")
    group.add_argument("--status",  action="store_true", help="Check task status")
    group.add_argument("--run-now", action="store_true", help="Trigger immediately")
    group.add_argument("--install-python", action="store_true", help="Cross-platform Python fallback")

    parser.add_argument("--time", default="08:00", help="Daily run time HH:MM (default: 08:00)")

    args = parser.parse_args()

    if sys.platform != "win32" and (args.install or args.remove or args.status or args.run_now):
        print("Windows Task Scheduler is only available on Windows.")
        print("Use --install-python for a cross-platform fallback.")
        sys.exit(1)

    if args.install:
        install(args.time)
    elif args.remove:
        remove()
    elif args.status:
        status()
    elif args.run_now:
        run_now()
    elif args.install_python:
        install_python_fallback(args.time)
