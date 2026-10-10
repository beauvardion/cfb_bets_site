"""
grade_bets.py
================================================================================
Run after the week's games are finished (e.g. Sunday), BEFORE you commit and
push in GitHub Desktop.

Reads every weekly bet file in the site's bets/ folder, pulls final scores and
closing totals from the CFBD betting-lines endpoint, and writes one graded
file, results.csv, into the site folder. The site's History tab reads it.

For each bet:
    final_total  = home score + away score
    result       = Win / Loss / Push / Pending (game not finished yet)
    profit       = units won or lost. "units" = amount RISKED.
                     win at -110 for 2u -> +1.82u, loss -> -2.00u, push -> 0
    closing_line = closing total that the most CFBD sportsbooks closed at (always a
                   real posted number, e.g. 54 or 54.5 — never an average like 54.75).
                   If books are split evenly, the number closest to the middle wins;
                   if still tied, the one LESS favorable to your bet (conservative CLV).
    clv          = closing line value, in points, from your side:
                     Under: your line - closing line   (positive = you beat the close)
                     Over:  closing line - your line

Re-running is safe: results.csv is rebuilt from scratch every time, so bets
that were Pending get graded once their games finish.

This file lives IN the site folder and runs automatically every morning on
GitHub (see .github/workflows/daily_refresh.yml). Your CFBD key comes from the
GitHub secret CFBD_API_KEY — never paste the key into this file, because this
file is public on GitHub.
================================================================================
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import cfbd
except ImportError as e:
    raise ImportError("Run:  pip install cfbd") from e

# ==============================================================================
# CONFIG
# ==============================================================================
# The key is read ONLY from the CFBD_API_KEY environment variable (a GitHub secret).
# Do NOT paste your key here — this file is public.
SITE_DIR = Path(__file__).resolve().parent     # the site folder this file sits in
DEFAULT_ODDS = -110                           # used only if a bet has no odds recorded

# ==============================================================================
# LOAD ALL BETS
# ==============================================================================
site_dir = Path(SITE_DIR)
bet_files = sorted((site_dir / "bets").glob("*.csv"))
frames = []
for f in bet_files:
    d = pd.read_csv(f)
    if not d.empty:
        d["week_of"] = f.stem
        frames.append(d)
if not frames:
    raise SystemExit(f"No bet files found in {site_dir / 'bets'} — run publish_week.py first.")

bets = pd.concat(frames, ignore_index=True)
def to_local_date(x):
    """Plain calendar date from any game_date format (timezone dropped, not converted)."""
    if x is None or (not isinstance(x, str) and pd.isna(x)):
        return pd.NaT
    try:
        t = pd.Timestamp(x)
    except (ValueError, TypeError):
        return pd.NaT
    if t is pd.NaT:
        return pd.NaT
    return (t.tz_localize(None) if t.tzinfo is not None else t).normalize()


bets["game_date"] = pd.to_datetime(bets["game_date"].map(to_local_date), errors="coerce")
# CFB season year: a January bowl game belongs to the previous season
bets["season"] = np.where(bets["game_date"].dt.month >= 7,
                          bets["game_date"].dt.year, bets["game_date"].dt.year - 1)
print(f"Loaded {len(bets)} bets from {len(frames)} week file(s).")

# ==============================================================================
# FETCH SCORES + CLOSING TOTALS FROM CFBD
# ==============================================================================
api_key = os.environ.get("CFBD_API_KEY")
if not api_key:
    raise ValueError("CFBD_API_KEY isn't set. On GitHub, add it under Settings -> Secrets and "
                     "variables -> Actions. (This script is meant to run there, not on your PC.)")

betting_api = cfbd.BettingApi(cfbd.ApiClient(cfbd.Configuration(access_token=api_key)))

rows = []
for season in sorted(bets["season"].dropna().unique().astype(int)):
    for season_type in ("regular", "postseason"):
        try:
            games = betting_api.get_lines(year=int(season), season_type=season_type)
        except cfbd.ApiException as e:
            print(f"  WARNING: couldn't fetch {season} {season_type} lines ({e.status}: {e.reason})")
            continue
        for g in games:
            totals = [ln.over_under for ln in (g.lines or []) if ln.over_under is not None]
            rows.append({
                "season": season,
                "home_team": g.home_team,
                "away_team": g.away_team,
                "home_score": g.home_score,
                "away_score": g.away_score,
                "closing_totals": [float(t) for t in totals],
            })
        print(f"  {season} {season_type}: {len(games)} games")

lines = pd.DataFrame(rows)

# Allow for home/away being listed the other way round
flipped = lines.rename(columns={"home_team": "away_team", "away_team": "home_team",
                                "home_score": "away_score", "away_score": "home_score"})
lookup = pd.concat([lines, flipped], ignore_index=True).drop_duplicates(
    subset=["season", "home_team", "away_team"]
)

graded = bets.merge(lookup, on=["season", "home_team", "away_team"], how="left")


def pick_closing_line(totals, pick):
    """Most common closing total across books, always a real posted number."""
    if not isinstance(totals, list) or not totals:
        return np.nan
    counts = pd.Series(totals).value_counts()
    tied = counts[counts == counts.max()].index.tolist()
    if len(tied) == 1:
        return tied[0]
    mid = float(np.median(totals))
    nearest = min(abs(t - mid) for t in tied)
    tied = [t for t in tied if abs(t - mid) == nearest]
    # Still tied: take the number that gives the bet LESS closing line value
    # (under -> higher close is worse for you; over -> lower close is worse)
    return max(tied) if str(pick).lower() == "under" else min(tied)


graded["closing_line"] = [pick_closing_line(t, p) for t, p in zip(graded["closing_totals"], graded["pick"])]

# ==============================================================================
# GRADE
# ==============================================================================
graded["final_total"] = graded["home_score"] + graded["away_score"]
done = graded["final_total"].notna()

is_under = graded["pick"].str.lower() == "under"
won = np.where(is_under, graded["final_total"] < graded["line"], graded["final_total"] > graded["line"])
push = graded["final_total"] == graded["line"]

graded["result"] = np.select(
    [~done, push, won],
    ["Pending", "Push", "Win"],
    default="Loss",
)

odds = graded["odds"].fillna(DEFAULT_ODDS).astype(float)
payout_per_unit = np.where(odds < 0, 100 / odds.abs(), odds / 100)
graded["profit"] = np.select(
    [graded["result"] == "Win", graded["result"] == "Loss"],
    [graded["units"] * payout_per_unit, -graded["units"]],
    default=0.0,
).round(2)
graded.loc[graded["result"] == "Pending", "profit"] = np.nan

graded["clv"] = np.where(is_under,
                         graded["line"] - graded["closing_line"],
                         graded["closing_line"] - graded["line"]).round(1)

# ==============================================================================
# SAVE
# ==============================================================================
out_cols = ["week_of", "season", "week", "game_date", "kickoff_utc", "away_team", "home_team",
            "pick", "line", "odds", "book", "units", "model_total", "edge",
            "home_score", "away_score", "final_total", "result", "profit",
            "closing_line", "clv"]
# Older bet files may not have every column (e.g. week, kickoff_utc, model_total) — leave them blank
graded = graded.reindex(columns=list(dict.fromkeys(list(graded.columns) + out_cols)))
graded = graded.sort_values(["game_date", "home_team"])[out_cols]
graded["graded_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
graded["game_date"] = graded["game_date"].dt.strftime("%Y-%m-%d")

out_path = site_dir / "results.csv"
graded.to_csv(out_path, index=False)

done_df = graded[graded["result"].isin(["Win", "Loss", "Push"])]
print("=" * 78)
print(f"Graded {len(done_df)} bets, {int((graded['result'] == 'Pending').sum())} pending.")
if len(done_df):
    w, l, p = [(done_df["result"] == r).sum() for r in ("Win", "Loss", "Push")]
    print(f"Record {w}-{l}-{p} | Profit {done_df['profit'].sum():+.2f}u | "
          f"Avg CLV {done_df['clv'].mean():+.2f} pts")
no_match = graded[graded["closing_line"].isna() & graded["final_total"].isna()]
if len(no_match):
    print("\nNo CFBD game found for these (check team names or game not in CFBD yet):")
    for _, r in no_match.iterrows():
        print(f"  {r['game_date']}  {r['away_team']} @ {r['home_team']}")
print(f"\nSaved -> {out_path}")
print("Done. (On GitHub, the workflow commits and pushes results.csv automatically.)")
