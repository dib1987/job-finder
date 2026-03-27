"""
One-time LinkedIn session setup.
Run this directly: python setup_linkedin.py
"""
import ctypes
import sys
import os
sys.path.insert(0, '.')
sys.path.insert(0, '..')

from dotenv import load_dotenv
load_dotenv()

from playwright.sync_api import sync_playwright
from pathlib import Path

session_path = "config/linkedin_session.json"
Path(session_path).parent.mkdir(parents=True, exist_ok=True)

print("\nOpening LinkedIn login page in browser...")
print("Log in, then click OK on the popup dialog.\n")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    )
    page = context.new_page()
    page.goto("https://www.linkedin.com/login")

    # Windows popup — click OK after logging in
    ctypes.windll.user32.MessageBoxW(
        0,
        "Log into LinkedIn in the browser window.\n\nOnce you see your LinkedIn feed, click OK here.",
        "Job Agent — LinkedIn Setup",
        0x40  # MB_ICONINFORMATION
    )

    context.storage_state(path=session_path)
    browser.close()

print(f"\nSession saved to: {session_path}")
print("LinkedIn setup complete. You can now run: python agent.py --dry-run\n")
