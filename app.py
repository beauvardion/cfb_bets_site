"""
app.py — Streamlit site showing each week's best bets.

Every CSV in the bets/ folder is one week (named YYYY-MM-DD.csv, written by
publish_week.py). The newest week shows by default; older weeks are in the
dropdown, and the History tab shows the whole season.

Run locally:   streamlit run app.py
"""

from pathlib import Path

import pandas as pd
import streamlit as st

BETS_DIR = Path(__file__).parent / "bets"

st.set_page_config(page_title="CFB Best Bets", page_icon="🏈", layout="wide")


# ==============================================================================
# DATA
# ==============================================================================
@st.cache_data(ttl=600)  # re-read files at most every 10 minutes
def load_all_weeks() -> pd.DataFrame:
    frames = []
    for path in sorted(BETS_DIR.glob("*.csv")):
        df = pd.read_csv(path)
        if df.empty:
            continue
        df["week_of"] = path.stem
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def fmt_odds(x) -> str:
    if pd.isna(x):
        return ""
    x = int(x)
    return f"+{x}" if x > 0 else str(x)


def pretty_date(stem: str) -> str:
    d = pd.to_datetime(stem, errors="coerce")
    return f"{d:%b} {d.day}, {d.year}" if pd.notna(d) else stem


def display_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["Game"] = out["away_team"] + " @ " + out["home_team"]
    out["Bet"] = out["pick"] + " " + out["line"].map(lambda v: f"{v:g}")
    out["Odds"] = out["odds"].map(fmt_odds)
    d = pd.to_datetime(out["game_date"], errors="coerce")
    # (built by hand because "%-d" doesn't work on Windows)
    out["Date"] = d.dt.strftime("%a %b ") + d.dt.day.astype("Int64").astype(str)
    out = out.rename(columns={"book": "Book", "units": "Units"})
    return out.sort_values(["Units", "Date"], ascending=[False, True])[
        ["Date", "Game", "Bet", "Odds", "Book", "Units"]
    ]


# ==============================================================================
# PAGE
# ==============================================================================
all_bets = load_all_weeks()

st.title("CFB best bets")

if all_bets.empty:
    st.info("No bets posted yet. Run publish_week.py to add this week's file to the bets folder.")
    st.stop()

weeks = sorted(all_bets["week_of"].unique(), reverse=True)

tab_week, tab_history = st.tabs(["This week", "History"])

with tab_week:
    week = st.selectbox("Week of", weeks, format_func=pretty_date)
    wk = all_bets[all_bets["week_of"] == week]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bets", len(wk))
    c2.metric("Total units", int(wk["units"].sum()))
    c3.metric("Unders", int((wk["pick"] == "Under").sum()))
    c4.metric("Overs", int((wk["pick"] == "Over").sum()))

    for units in sorted(wk["units"].unique(), reverse=True):
        tier = wk[wk["units"] == units]
        label = "unit" if units == 1 else "units"
        st.subheader(f"{units} {label}")
        st.dataframe(display_table(tier), hide_index=True, width="stretch")

    st.download_button(
        "Download this week as CSV",
        wk.drop(columns="week_of").to_csv(index=False),
        file_name=f"bets_{week}.csv",
        mime="text/csv",
    )

with tab_history:
    summary = (
        all_bets.groupby("week_of")
        .agg(bets=("units", "size"), units=("units", "sum"),
             unders=("pick", lambda s: (s == "Under").sum()),
             overs=("pick", lambda s: (s == "Over").sum()))
        .reset_index()
        .sort_values("week_of")
    )
    st.subheader("Units bet per week")
    st.bar_chart(summary.set_index("week_of")["units"])

    summary["week_of"] = summary["week_of"].map(pretty_date)
    st.dataframe(
        summary.rename(columns={"week_of": "Week of", "bets": "Bets", "units": "Units",
                                "unders": "Unders", "overs": "Overs"}),
        hide_index=True, width="stretch",
    )

st.caption("Model picks for entertainment and research. Lines and odds are the best available when the file was posted and may have moved.")
