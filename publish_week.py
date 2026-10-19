"""
publish_week.py
================================================================================
Run AFTER total_bets.py. Copies this week's bets into the site's bets/ folder
and stamps the time it was posted ("Bets posted ..." on the site).

Files are named by season and week, e.g. bets/2026_week08.csv, so one week can
never overwrite another. Re-publishing the same week replaces that week's file.

Safety checks (the script stops instead of publishing if either fails):
  - Most of the bets' games are already in the past -> the bets file is stale.
  - Another week's file already holds these same games.
================================================================================
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ==============================================================================
# CONFIG
# ==============================================================================
BETS_FILE = "this_weeks_total_bets.csv"     # output of total_bets.py
SITE_DIR = r"C:\Users\beauv\cfb_bets_site"   # the folder holding app.py
AUTO_PUSH = False                            # False = you push with GitHub Desktop (recommended)


def plain_date(x):
    t = pd.Timestamp(x)
    return (t.tz_localize(None) if t.tzinfo is not None else t).normalize()


# ==============================================================================
# LOAD + CHECK
# ==============================================================================
site_dir = Path(SITE_DIR)
bets_dir = site_dir / "bets"
bets_dir.mkdir(parents=True, exist_ok=True)

df = pd.read_csv(BETS_FILE)
if df.empty:
    raise SystemExit(f"{BETS_FILE} has no bets — nothing to publish.")

# 1) Stale file: most games already played
today_et = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
dates = df["game_date"].dropna().map(plain_date)
n_past = int((dates < today_et).sum())
if n_past > len(df) / 2:
    raise SystemExit(
        f"\nSTOPPED: {n_past} of {len(df)} bets in {BETS_FILE} are for games before today "
        f"({dates.min():%b} {dates.min().day}–{dates.max():%b} {dates.max().day}).\n"
        f"This looks like last week's picks. Re-run the weekly pipeline (an earlier step "
        f"probably failed), then run total_bets.py and this script again. Nothing was published."
    )

# 2) File name from season + week
if {"season", "week"}.issubset(df.columns) and df["week"].notna().any():
    season = int(df["season"].dropna().mode()[0])
    week = int(df["week"].dropna().mode()[0])
    name = f"{season}_week{week:02d}"
else:
    name = dates.min().strftime("%Y-%m-%d")
    print("NOTE: no week number in the bets file — naming it by date instead.")

dest = bets_dir / f"{name}.csv"

# 3) Don't let these games land in a second file
games = set(zip(df["home_team"], df["away_team"]))
for other in bets_dir.glob("*.csv"):
    if other == dest:
        continue
    o = pd.read_csv(other)
    if {"home_team", "away_team"}.issubset(o.columns):
        overlap = games & set(zip(o["home_team"], o["away_team"]))
        if len(overlap) > len(games) / 2:
            raise SystemExit(
                f"\nSTOPPED: {len(overlap)} of these games are already in {other.name}. "
                f"This would publish the same games as a second week. Nothing was published."
            )

# ==============================================================================
# WRITE
# ==============================================================================
df["published_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
replacing = dest.exists()
df.to_csv(dest, index=False)
print(f"{'Updated' if replacing else 'Created'} {dest}  ({len(df)} bets)")
if not AUTO_PUSH:
    print("NEXT: open GitHub Desktop -> type a summary -> Commit to main -> Push origin.")

# ==============================================================================
# PUSH (only if AUTO_PUSH = True and Git is installed)
# ==============================================================================
if AUTO_PUSH:
    def git(*args):
        result = subprocess.run(["git", *args], cwd=site_dir, capture_output=True, text=True)
        print(f"$ git {' '.join(args)}\n{result.stdout}{result.stderr}".strip())
        return result.returncode

    git("add", f"bets/{dest.name}")
    if git("commit", "-m", f"Bets for {name}") == 0:
        if git("push") == 0:
            print("Pushed. The site will pick up the new week within a minute or two.")
        else:
            print("Push failed — check the message above (login, remote, or network).")
    else:
        print("Nothing new to commit (file unchanged), or commit failed — see above.")
