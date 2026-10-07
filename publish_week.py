"""
publish_week.py
================================================================================
Run AFTER total_bets.py. Copies this week's bets into the site's bets/ folder
(named by the first game date, e.g. bets/2026-10-10.csv) and, optionally,
pushes it to GitHub so the hosted site updates automatically.
================================================================================
"""

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd

# ==============================================================================
# CONFIG
# ==============================================================================
BETS_FILE = "this_weeks_total_bets.csv"     # output of total_bets.py
SITE_DIR = r"C:\Users\beauv\cfb_bets_site"   # the folder holding app.py
AUTO_PUSH = False                            # False = you push with GitHub Desktop (recommended)

# ==============================================================================
# COPY
# ==============================================================================
site_dir = Path(SITE_DIR)
bets_dir = site_dir / "bets"
bets_dir.mkdir(parents=True, exist_ok=True)

df = pd.read_csv(BETS_FILE)
dates = pd.to_datetime(df.get("game_date"), errors="coerce").dropna() if not df.empty else pd.Series(dtype="datetime64[ns]")
week_label = dates.min().strftime("%Y-%m-%d") if len(dates) else date.today().isoformat()

dest = bets_dir / f"{week_label}.csv"
shutil.copyfile(BETS_FILE, dest)
print(f"Copied {len(df)} bets -> {dest}")
if not AUTO_PUSH:
    print("NEXT: open GitHub Desktop -> type a summary -> Commit to main -> Push origin.")

# ==============================================================================
# PUSH
# ==============================================================================
if AUTO_PUSH:
    def git(*args):
        result = subprocess.run(["git", *args], cwd=site_dir, capture_output=True, text=True)
        print(f"$ git {' '.join(args)}\n{result.stdout}{result.stderr}".strip())
        return result.returncode

    git("add", f"bets/{week_label}.csv")
    if git("commit", "-m", f"Bets for week of {week_label}") == 0:
        if git("push") == 0:
            print("Pushed. The site will pick up the new week within a minute or two.")
        else:
            print("Push failed — check the message above (login, remote, or network).")
    else:
        print("Nothing new to commit (file unchanged), or commit failed — see above.")
