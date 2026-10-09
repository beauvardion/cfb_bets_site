"""
app.py — Streamlit site showing each week's best bets and how past bets did.

bets/YYYY-MM-DD.csv  one file per week, written by publish_week.py
results.csv          every bet graded, written by grade_bets.py

Run locally:   python -m streamlit run app.py
"""

from pathlib import Path

import pandas as pd
import streamlit as st

SITE = Path(__file__).parent
BETS_DIR = SITE / "bets"
RESULTS_PATH = SITE / "results.csv"

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
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@st.cache_data(ttl=600)
def load_results() -> pd.DataFrame:
    if not RESULTS_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(RESULTS_PATH)
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    return df


def fmt_odds(x) -> str:
    if pd.isna(x):
        return ""
    x = int(x)
    return f"+{x}" if x > 0 else str(x)


def short_date(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s, errors="coerce")
    # built by hand because "%-d" doesn't work on Windows
    return d.dt.strftime("%a %b ") + d.dt.day.astype("Int64").astype(str)


def pretty_date(stem: str) -> str:
    d = pd.to_datetime(stem, errors="coerce")
    return f"{d:%b} {d.day}, {d.year}" if pd.notna(d) else stem


def add_labels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["Date"] = short_date(out["game_date"])
    out["Game"] = out["away_team"] + " @ " + out["home_team"]
    out["Bet"] = out["pick"] + " " + out["line"].map(lambda v: f"{v:g}")
    out["Odds"] = out["odds"].map(fmt_odds)
    return out


def record(df: pd.DataFrame) -> str:
    w, l, p = [(df["result"] == r).sum() for r in ("Win", "Loss", "Push")]
    return f"{w}-{l}-{p}"


def roi(df: pd.DataFrame) -> float:
    risked = df.loc[df["result"].isin(["Win", "Loss"]), "units"].sum()
    return df["profit"].sum() / risked * 100 if risked else 0.0


def color_result(v):
    return {"Win": "color: #1F7A3A; font-weight: 600",
            "Loss": "color: #B3261E; font-weight: 600"}.get(v, "")


def color_signed(v):
    if pd.isna(v) or v == 0:
        return ""
    return "color: #1F7A3A" if v > 0 else "color: #B3261E"


def styled(df: pd.DataFrame, result_cols=(), signed_cols=()):
    s = df.style
    style_fn = s.map if hasattr(s, "map") else s.applymap  # pandas < 2.1 fallback
    if result_cols:
        s = style_fn(color_result, subset=list(result_cols))
        style_fn = s.map if hasattr(s, "map") else s.applymap
    if signed_cols:
        s = style_fn(color_signed, subset=list(signed_cols))
    return s


# ==============================================================================
# PAGE
# ==============================================================================
all_bets = load_all_weeks()
results = load_results()

st.title("CFB best bets")

if all_bets.empty:
    st.info("No bets posted yet. Run publish_week.py to add this week's file to the bets folder.")
    st.stop()

weeks = sorted(all_bets["week_of"].unique(), reverse=True)
tab_week, tab_history = st.tabs(["This week", "History"])

# ------------------------------------------------------------------------------
# THIS WEEK
# ------------------------------------------------------------------------------
with tab_week:
    week = st.selectbox("Week of", weeks, format_func=pretty_date)
    wk = all_bets[all_bets["week_of"] == week]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bets", len(wk))
    c2.metric("Total units", int(wk["units"].sum()))
    c3.metric("Unders", int((wk["pick"] == "Under").sum()))
    c4.metric("Overs", int((wk["pick"] == "Over").sum()))

    for units in sorted(wk["units"].unique(), reverse=True):
        tier = add_labels(wk[wk["units"] == units]).rename(columns={"book": "Book", "units": "Units"})
        st.subheader(f"{units} {'unit' if units == 1 else 'units'}")
        st.dataframe(tier[["Date", "Game", "Bet", "Odds", "Book", "Units"]],
                     hide_index=True, width="stretch")

    st.download_button("Download this week as CSV", wk.drop(columns="week_of").to_csv(index=False),
                       file_name=f"bets_{week}.csv", mime="text/csv")

# ------------------------------------------------------------------------------
# HISTORY
# ------------------------------------------------------------------------------
with tab_history:
    if results.empty:
        st.info("No graded bets yet. Run grade_bets.py after the games finish, then commit and push.")
        st.stop()

    done = results[results["result"].isin(["Win", "Loss", "Push"])].copy()
    pending = results[results["result"] == "Pending"].copy()

    if done.empty:
        st.info("Nothing graded yet — every bet so far is still pending.")
    else:
        # --- headline numbers -------------------------------------------------
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Record", record(done))
        c2.metric("Profit", f"{done['profit'].sum():+.2f}u")
        c3.metric("ROI", f"{roi(done):+.1f}%")
        clv_known = done["clv"].dropna()
        c4.metric("Avg CLV", f"{clv_known.mean():+.2f} pts" if len(clv_known) else "–")
        c5.metric("Beat the close", f"{(clv_known > 0).mean() * 100:.0f}%" if len(clv_known) else "–")

        # --- profit over time -------------------------------------------------
        st.subheader("Profit over time")
        daily = (done.groupby("game_date")["profit"].sum().sort_index().cumsum()
                 .rename("Cumulative profit (units)"))
        st.line_chart(daily, x_label="Game date", y_label="Units")

        # --- by bet type ------------------------------------------------------
        st.subheader("By bet type")
        done["Type"] = done["pick"] + " " + done["units"].astype(int).astype(str) + "u"
        by_type = (
            done.groupby("Type")
            .apply(lambda g: pd.Series({
                "Bets": len(g),
                "Record": record(g),
                "Profit (u)": round(g["profit"].sum(), 2),
                "ROI %": round(roi(g), 1),
                "Avg CLV": round(g["clv"].mean(), 2) if g["clv"].notna().any() else None,
            }))
            .reset_index()
            .sort_values("Type", ascending=False)
        )
        st.dataframe(styled(by_type, signed_cols=["Profit (u)", "ROI %", "Avg CLV"]),
                     hide_index=True, width="stretch")

        # --- every completed bet ----------------------------------------------
        st.subheader("Completed bets")
        table = add_labels(done).sort_values("game_date", ascending=False)
        table = table.rename(columns={
            "book": "Book", "units": "Units", "final_total": "Final total",
            "result": "Result", "profit": "Profit (u)", "closing_line": "Closing line",
            "clv": "CLV (pts)",
        })[["Date", "Game", "Bet", "Odds", "Book", "Units", "Final total",
            "Result", "Profit (u)", "Closing line", "CLV (pts)"]]
        st.dataframe(
            styled(table, result_cols=["Result"], signed_cols=["Profit (u)", "CLV (pts)"])
            .format({"Profit (u)": "{:+.2f}", "CLV (pts)": "{:+.1f}",
                     "Closing line": "{:g}", "Final total": "{:.0f}"}, na_rep="–"),
            hide_index=True, width="stretch",
        )

    if not pending.empty:
        st.subheader("Pending")
        p = add_labels(pending).rename(columns={"book": "Book", "units": "Units"})
        st.dataframe(p[["Date", "Game", "Bet", "Odds", "Book", "Units"]],
                     hide_index=True, width="stretch")

    st.caption("Units are the amount risked. CLV is how many points better your number was than "
               "the closing total (positive = you beat the close).")

st.caption("Model picks for entertainment and research. Lines and odds are the best available "
           "when the file was posted and may have moved.")
