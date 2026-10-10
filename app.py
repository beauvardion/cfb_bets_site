"""
app.py — Streamlit site showing each week's best bets and how past bets did.

Files the site reads (all in this folder):
    bets/YYYY-MM-DD.csv   one file per week, written by publish_week.py
    results.csv           every bet graded, written by grade_bets.py
    team_colors.csv       team colors for the cards, written by make_team_colors.py

Live scores come from the CFBD scoreboard, using the key stored in Streamlit's
Secrets as CFBD_API_KEY (never in this repo). Without it the site still works;
it just can't show live scores.

Run locally:   python -m streamlit run app.py
"""

import html
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

SITE = Path(__file__).parent
BETS_DIR = SITE / "bets"
RESULTS_PATH = SITE / "results.csv"
COLORS_PATH = SITE / "team_colors.csv"
ET = "America/New_York"
LIVE_REFRESH_SECONDS = 120      # how often live scores refresh (also how long they're cached)
DEFAULT_ODDS = -110
FALLBACK_COLOR = "#9AA0A6"

st.set_page_config(page_title="CFB Best Bets", page_icon="🏈", layout="wide")


def to_local_date(s: pd.Series) -> pd.Series:
    """Turn any game_date format into a plain calendar date (no timezone).

    Handles '2026-10-10', '2026-10-10 19:30:00-04:00', '2026-10-10T23:30:00Z',
    and files that mix those formats. The date is kept as written (the timezone
    is dropped, not converted), so a 7:30 PM ET game stays on its own day.
    """
    def one(x):
        if x is None or (not isinstance(x, str) and pd.isna(x)):
            return pd.NaT
        try:
            t = pd.Timestamp(x)
        except (ValueError, TypeError):
            return pd.NaT
        if t is pd.NaT:
            return pd.NaT
        if t.tzinfo is not None:
            t = t.tz_localize(None)
        return t.normalize()
    return pd.to_datetime(s.map(one), errors="coerce")


# ==============================================================================
# LOADING
# ==============================================================================
@st.cache_data(ttl=600)
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
    df = pd.concat(frames, ignore_index=True)
    # Older files may be missing the newer columns — add them as blanks
    for col in ("kickoff_utc", "season", "week", "model_total", "edge", "published_at"):
        if col not in df.columns:
            df[col] = pd.NA
    df["kickoff"] = pd.to_datetime(df["kickoff_utc"], utc=True, errors="coerce", format="mixed")
    df["game_date"] = to_local_date(df["game_date"])
    return df


@st.cache_data(ttl=600)
def load_results() -> pd.DataFrame:
    if not RESULTS_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(RESULTS_PATH)
    df["game_date"] = to_local_date(df["game_date"])
    return df


@st.cache_data(ttl=3600)
def load_team_colors() -> dict:
    if not COLORS_PATH.exists():
        return {}
    df = pd.read_csv(COLORS_PATH)
    colors = {}
    for _, r in df.iterrows():
        primary, alt = clean_hex(r.get("color")), clean_hex(r.get("alt_color"))
        # Near-white primaries disappear on a white card — use the alternate instead
        if primary and luminance(primary) > 0.85 and alt:
            primary = alt
        colors[r["team"]] = primary or alt or FALLBACK_COLOR
    return colors


@st.cache_data(ttl=LIVE_REFRESH_SECONDS, show_spinner=False)
def fetch_scoreboard():
    """Current FBS scoreboard from CFBD. Returns (DataFrame, fetched_at) — empty if unavailable."""
    try:
        api_key = st.secrets["CFBD_API_KEY"]
    except Exception:
        return pd.DataFrame(), None
    try:
        import cfbd
        api = cfbd.GamesApi(cfbd.ApiClient(cfbd.Configuration(access_token=api_key)))
        games = api.get_scoreboard(classification="fbs")
    except Exception:
        return pd.DataFrame(), None

    rows = []
    for g in games:
        status = getattr(g.status, "value", g.status)
        rows.append({
            "home_team": g.home_team.name, "away_team": g.away_team.name,
            "home_points": g.home_team.points, "away_points": g.away_team.points,
            "status": status, "period": g.period, "clock": g.clock, "tv": g.tv,
        })
    return pd.DataFrame(rows), datetime.now(timezone.utc)


# ==============================================================================
# SMALL HELPERS
# ==============================================================================
def clean_hex(c):
    if not isinstance(c, str) or not c.strip():
        return None
    c = c.strip()
    return c if c.startswith("#") else f"#{c}"


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 0.5
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b


def et(ts):
    return ts.tz_convert(ET) if pd.notna(ts) else ts


def fmt_time(ts) -> str:
    """'Sat 7:30 PM' in Eastern time (built by hand — '%-I' doesn't work on Windows)."""
    t = et(ts)
    return f"{t:%a} {t.strftime('%I').lstrip('0')}:{t:%M} {t:%p}"


def fmt_day_time(ts) -> str:
    t = et(ts)
    return f"{t:%a %b} {t.day}, {t.strftime('%I').lstrip('0')}:{t:%M} {t:%p} ET"


def fmt_odds(x) -> str:
    if pd.isna(x):
        return ""
    x = int(x)
    return f"+{x}" if x > 0 else f"−{abs(x)}"


def fmt_ago(ts) -> str:
    mins = int((datetime.now(timezone.utc) - ts).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    return f"{mins // 60}h {mins % 60}m ago"


def fmt_countdown(ts) -> str:
    secs = int((ts - pd.Timestamp.now(tz="UTC")).total_seconds())
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"Kicks off in {d}d {h}h"
    if h:
        return f"Kicks off in {h}h {m}m"
    return f"Kicks off in {max(m, 1)}m"


def week_label(df: pd.DataFrame, stem: str) -> str:
    dates = df["game_date"].dropna()
    rng = ""
    if len(dates):
        a, b = dates.min(), dates.max()
        rng = f"{a:%b} {a.day}" if a == b else (
            f"{a:%b} {a.day}–{b.day}" if a.month == b.month else f"{a:%b} {a.day} – {b:%b} {b.day}")
    wk = df["week"].dropna()
    if len(wk):
        return f"Week {int(wk.iloc[0])} · {rng}"
    return rng or stem


def grade(pick: str, line: float, total: float) -> str:
    if total == line:
        return "Push"
    won = total < line if str(pick).lower() == "under" else total > line
    return "Win" if won else "Loss"


def profit_for(result: str, units: float, odds) -> float:
    if result == "Win":
        o = DEFAULT_ODDS if pd.isna(odds) else float(odds)
        return round(units * (100 / abs(o) if o < 0 else o / 100), 2)
    if result == "Loss":
        return -float(units)
    return 0.0


def record(df: pd.DataFrame) -> str:
    w, l, p = [(df["result"] == r).sum() for r in ("Win", "Loss", "Push")]
    return f"{w}-{l}-{p}"


def roi(df: pd.DataFrame) -> float:
    risked = df.loc[df["result"].isin(["Win", "Loss"]), "units"].sum()
    return df["profit"].sum() / risked * 100 if risked else 0.0


# ==============================================================================
# GAME STATE: upcoming / live / final for every bet
# ==============================================================================
def attach_state(wk: pd.DataFrame, results: pd.DataFrame, board: pd.DataFrame) -> pd.DataFrame:
    """Priority: graded result (results.csv) > live scoreboard > kickoff time."""
    wk = wk.copy()
    now = pd.Timestamp.now(tz="UTC")
    today_et = now.tz_convert(ET).normalize().tz_localize(None)

    started = wk["kickoff"].notna() & (wk["kickoff"] <= now)
    started |= wk["kickoff"].isna() & (wk["game_date"] < today_et)
    wk["state"] = started.map({True: "started", False: "upcoming"})
    for col in ("home_points", "away_points", "period", "clock", "tv", "result", "profit"):
        wk[col] = pd.NA

    # Live scoreboard (also matches games where CFBD lists home/away the other way round)
    if not board.empty:
        flipped = board.rename(columns={"home_team": "away_team", "away_team": "home_team",
                                        "home_points": "away_points", "away_points": "home_points"})
        lookup = pd.concat([board, flipped], ignore_index=True).drop_duplicates(["home_team", "away_team"])
        lookup = lookup.set_index(["home_team", "away_team"])
        for i, r in wk.iterrows():
            key = (r["home_team"], r["away_team"])
            if key not in lookup.index:
                continue
            g = lookup.loc[key]
            wk.at[i, "tv"] = g["tv"]
            if g["status"] == "in_progress":
                wk.at[i, "state"] = "live"
            elif g["status"] == "completed":
                wk.at[i, "state"] = "final"
            if g["status"] in ("in_progress", "completed"):
                for col in ("home_points", "away_points", "period", "clock"):
                    wk.at[i, col] = g[col]

    # Graded results win over everything (they're the official record)
    if not results.empty:
        graded = results[results["result"].isin(["Win", "Loss", "Push"])]
        graded = graded.set_index(["home_team", "away_team", "pick", "line"])
        for i, r in wk.iterrows():
            key = (r["home_team"], r["away_team"], r["pick"], r["line"])
            if key in graded.index:
                g = graded.loc[key]
                g = g.iloc[0] if isinstance(g, pd.DataFrame) else g
                wk.at[i, "state"] = "final"
                wk.at[i, "home_points"], wk.at[i, "away_points"] = g["home_score"], g["away_score"]

    # Grade anything final (from either source)
    for i, r in wk.iterrows():
        if r["state"] == "final" and pd.notna(wk.at[i, "home_points"]):
            total = float(wk.at[i, "home_points"]) + float(wk.at[i, "away_points"])
            res = grade(r["pick"], r["line"], total)
            wk.at[i, "result"] = res
            wk.at[i, "profit"] = profit_for(res, r["units"], r["odds"])
    return wk


# ==============================================================================
# CARDS
# ==============================================================================
CARD_CSS = """<style>
.bet-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin:8px 0 24px}
.bet-card{position:relative;background:#fff;border:1px solid #E3E7E1;border-radius:12px;overflow:hidden;color:#1B1F1C;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.bet-card .stripe{height:6px}
.bet-card .body{padding:14px 16px 12px}
.bet-card .top{display:flex;align-items:center;gap:8px;font-size:.82rem;color:#5B635D}
.bet-card .when{flex:1}
.badge{font-weight:800;font-size:.8rem;letter-spacing:.04em;padding:3px 9px;border-radius:999px;border:2px solid #1F5C3A}
.badge.u3{background:#1F5C3A;color:#fff}
.badge.u2{background:#5E9C6E;border-color:#5E9C6E;color:#fff}
.badge.u1{background:#fff;color:#1F5C3A}
.pill{font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:999px;letter-spacing:.05em}
.pill.live{background:#B3261E;color:#fff}
.pill.final{background:#E3E7E1;color:#3A413C}
.pill.started{background:#E3E7E1;color:#3A413C}
.teams{margin:12px 0 10px;font-size:1.05rem;font-weight:600;line-height:1.55}
.teams .chip{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:8px;vertical-align:middle}
.teams .at{color:#8A928C;font-weight:500;margin-right:4px}
.betline{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.betline .pick{font-size:1.45rem;font-weight:800}
.betline .odds{color:#5B635D;font-size:.9rem}
.why{margin-top:6px;font-size:.85rem;color:#5B635D}
.status{margin-top:12px;padding-top:10px;border-top:1px dashed #E3E7E1;font-size:.88rem;color:#3A413C}
.status .good{color:#1F7A3A;font-weight:700}
.status .bad{color:#B3261E;font-weight:700}
.stamp{display:inline-block;margin-top:6px;font-weight:800;font-size:.85rem;padding:3px 10px;border-radius:6px}
.stamp.Win{background:#E3F2E7;color:#1F7A3A}
.stamp.Loss{background:#FBE6E4;color:#B3261E}
.stamp.Push{background:#EEF0EE;color:#3A413C}
.bet-card.closed .stripe,.bet-card.closed .teams,.bet-card.closed .betline,.bet-card.closed .why,.bet-card.closed .badge,.bet-card.closed .when{opacity:.45;filter:grayscale(.8)}
.bet-card.closed{background:#F7F8F6}
</style>"""


def status_html(r) -> str:
    away, home = html.escape(r["away_team"]), html.escape(r["home_team"])
    state = r["state"]
    if state == "upcoming":
        return fmt_countdown(r["kickoff"]) if pd.notna(r["kickoff"]) else "Upcoming"
    if pd.isna(r["home_points"]):
        return "Betting closed · score not available yet"

    hp, ap = int(r["home_points"]), int(r["away_points"])
    total = hp + ap
    score = f"{away} {ap}, {home} {hp} · Total {total}"

    if state == "live":
        p = r["period"]
        quarter = "" if pd.isna(p) else ("OT" if int(p) > 4 else f"Q{int(p)}")
        clock = "" if pd.isna(r["clock"]) else f" {r['clock']}"
        gap = r["line"] - total
        if str(r["pick"]).lower() == "under":
            msg = (f'<span class="good">{gap:g} under the line</span>' if gap > 0
                   else f'<span class="bad">{-gap:g} over the line</span>')
        else:
            msg = ('<span class="good">Over has hit</span>' if gap < 0
                   else f"Needs {gap + 0.5:g} more")
        return f"<b>{quarter}{clock}</b> · {score}<br>{msg}"

    # final
    res, prof = r["result"], r["profit"]
    sign = "+" if prof > 0 else ("−" if prof < 0 else "")
    stamp = f'<span class="stamp {res}">{res.upper()} {sign}{abs(prof):.2f}u</span>'
    return f"Final · {score}<br>{stamp}"


def card_html(r, colors: dict) -> str:
    away_c = colors.get(r["away_team"], FALLBACK_COLOR)
    home_c = colors.get(r["home_team"], FALLBACK_COLOR)
    units = int(r["units"])
    state = r["state"]
    closed = " closed" if state != "upcoming" else ""

    when = fmt_time(r["kickoff"]) if pd.notna(r["kickoff"]) else (
        f"{r['game_date']:%a %b} {r['game_date'].day}" if pd.notna(r["game_date"]) else "")
    if isinstance(r["tv"], str) and r["tv"]:
        when += f" · {html.escape(r['tv'])}"

    pill = {"live": '<span class="pill live">LIVE</span>',
            "final": '<span class="pill final">FINAL</span>',
            "started": '<span class="pill started">STARTED</span>'}.get(state, "")

    why = ""
    if pd.notna(r["model_total"]):
        why = f'<div class="why">Model total {r["model_total"]:g} · Edge {r["edge"]:+g} pts</div>'

    return (
        f'<div class="bet-card{closed}">'
        f'<div class="stripe" style="background:linear-gradient(90deg,{away_c} 0 50%,{home_c} 50% 100%)"></div>'
        f'<div class="body">'
        f'<div class="top"><span class="badge u{units}">{units}U</span>'
        f'<span class="when">{html.escape(when)}</span>{pill}</div>'
        f'<div class="teams">'
        f'<div><span class="chip" style="background:{away_c}"></span>{html.escape(r["away_team"])}</div>'
        f'<div><span class="chip" style="background:{home_c}"></span><span class="at">@</span>{html.escape(r["home_team"])}</div>'
        f'</div>'
        f'<div class="betline"><span class="pick">{html.escape(str(r["pick"]))} {r["line"]:g}</span>'
        f'<span class="odds">{fmt_odds(r["odds"])} · {html.escape(str(r["book"]))}</span></div>'
        f'{why}'
        f'<div class="status">{status_html(r)}</div>'
        f'</div></div>'
    )


def table_view(wk: pd.DataFrame) -> pd.DataFrame:
    def when(r):
        if pd.notna(r["kickoff"]):
            return fmt_time(r["kickoff"])
        return f"{r['game_date']:%a %b} {r['game_date'].day}" if pd.notna(r["game_date"]) else ""

    def status(r):
        if r["state"] == "final" and pd.notna(r["result"]):
            p = r["profit"]
            return f"{r['result']} ({'+' if p > 0 else ''}{p:.2f}u)"
        if r["state"] == "live":
            return f"Live · total {int(r['home_points']) + int(r['away_points'])}" if pd.notna(r["home_points"]) else "Live"
        return {"started": "Started", "final": "Final"}.get(r["state"], "Upcoming")

    return pd.DataFrame({
        "Kickoff (ET)": wk.apply(when, axis=1),
        "Game": wk["away_team"] + " @ " + wk["home_team"],
        "Bet": wk["pick"] + " " + wk["line"].map(lambda v: f"{v:g}"),
        "Odds": wk["odds"].map(fmt_odds),
        "Book": wk["book"],
        "Units": wk["units"].astype(int),
        "Model total": wk["model_total"],
        "Edge": wk["edge"],
        "Status": wk.apply(status, axis=1),
    })


# ==============================================================================
# THIS WEEK (re-runs itself every LIVE_REFRESH_SECONDS for the current week)
# ==============================================================================
def render_week(wk: pd.DataFrame, is_latest: bool, view: str):
    results = load_results()
    board, fetched_at = fetch_scoreboard() if is_latest else (pd.DataFrame(), None)
    wk = attach_state(wk, results, board)
    wk = wk.sort_values(["kickoff", "game_date", "units"], ascending=[True, True, False],
                        na_position="last")

    # --- last updated line ----------------------------------------------------
    parts = []
    posted = pd.to_datetime(wk["published_at"], utc=True, errors="coerce", format="mixed").dropna()
    if len(posted):
        parts.append(f"Bets posted {fmt_day_time(posted.max())}")
    if fetched_at is not None:
        parts.append(f"Live scores updated {fmt_ago(fetched_at)}")
    if not results.empty and "graded_at" in results.columns:
        graded = pd.to_datetime(results["graded_at"], utc=True, errors="coerce", format="mixed").dropna()
        if len(graded):
            parts.append(f"Results graded {fmt_day_time(graded.max())}")
    if parts:
        st.caption(" · ".join(parts))

    # --- week summary -----------------------------------------------------------
    done = wk[wk["state"] == "final"].dropna(subset=["result"])
    pending_units = int(wk.loc[wk["state"] != "final", "units"].sum())
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Bets", len(wk))
    c2.metric("Units risked", int(wk["units"].sum()))
    c3.metric("Record this week", record(done) if len(done) else "–")
    c4.metric("Profit this week", f"{done['profit'].sum():+.2f}u" if len(done) else "–")
    c5.metric("Units still pending", pending_units)

    # --- bets -------------------------------------------------------------------
    if view == "Cards":
        colors = load_team_colors()
        cards = "".join(card_html(r, colors) for _, r in wk.iterrows())
        st.markdown(CARD_CSS + f'<div class="bet-grid">{cards}</div>', unsafe_allow_html=True)
    else:
        table = table_view(wk)
        st.dataframe(
            table, hide_index=True, width="stretch",
            height=36 * (len(table) + 1) + 4,   # tall enough to show every row
            column_config={
                "Model total": st.column_config.NumberColumn(format="%.1f"),
                "Edge": st.column_config.NumberColumn(format="%+.1f"),
            },
        )


# ==============================================================================
# PAGE
# ==============================================================================
all_bets = load_all_weeks()
st.title("CFB best bets")

if all_bets.empty:
    st.info("No bets posted yet. Run publish_week.py to add this week's file to the bets folder.")
    st.stop()

weeks = sorted(all_bets["week_of"].unique(), reverse=True)
labels = {w: week_label(all_bets[all_bets["week_of"] == w], w) for w in weeks}

tab_week, tab_history = st.tabs(["This week", "History"])

with tab_week:
    left, right = st.columns([3, 1])
    week = left.selectbox("Week", weeks, format_func=lambda w: labels[w])
    view = right.segmented_control("View", ["Cards", "Table"], default="Cards") or "Cards"
    is_latest = week == weeks[0]

    week_bets = all_bets[all_bets["week_of"] == week]
    if is_latest:
        st.fragment(render_week, run_every=LIVE_REFRESH_SECONDS)(week_bets, is_latest, view)
    else:
        render_week(week_bets, is_latest, view)

# ------------------------------------------------------------------------------
# HISTORY
# ------------------------------------------------------------------------------
def short_date(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s, errors="coerce")
    return d.dt.strftime("%a %b ") + d.dt.day.astype("Int64").astype(str)


def color_result(v):
    return {"Win": "color: #1F7A3A; font-weight: 600",
            "Loss": "color: #B3261E; font-weight: 600"}.get(v, "")


def color_signed(v):
    if pd.isna(v) or v == 0:
        return ""
    return "color: #1F7A3A" if v > 0 else "color: #B3261E"


def styled(df: pd.DataFrame, result_cols=(), signed_cols=()):
    s = df.style
    fn = s.map if hasattr(s, "map") else s.applymap  # pandas < 2.1 fallback
    if result_cols:
        s = fn(color_result, subset=list(result_cols))
        fn = s.map if hasattr(s, "map") else s.applymap
    if signed_cols:
        s = fn(color_signed, subset=list(signed_cols))
    return s


with tab_history:
    results = load_results()
    if results.empty:
        st.info("No graded bets yet. Run grade_bets.py after the games finish, then commit and push.")
        st.stop()

    done = results[results["result"].isin(["Win", "Loss", "Push"])].copy()
    pending = results[results["result"] == "Pending"].copy()

    if done.empty:
        st.info("Nothing graded yet — every bet so far is still pending.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Record", record(done))
        c2.metric("Profit", f"{done['profit'].sum():+.2f}u")
        c3.metric("ROI", f"{roi(done):+.1f}%")
        clv_known = done["clv"].dropna()
        c4.metric("Avg CLV", f"{clv_known.mean():+.2f} pts" if len(clv_known) else "–")
        c5.metric("Beat the close", f"{(clv_known > 0).mean() * 100:.0f}%" if len(clv_known) else "–")

        st.subheader("Profit over time")
        daily = (done.groupby("game_date")["profit"].sum().sort_index().cumsum()
                 .rename("Cumulative profit (units)"))
        st.line_chart(daily, x_label="Game date", y_label="Units")

        st.subheader("By bet type")
        done["Type"] = done["pick"] + " " + done["units"].astype(int).astype(str) + "u"
        by_type = pd.DataFrame([
            {
                "Type": t,
                "Bets": len(g),
                "Record": record(g),
                "Profit (u)": round(g["profit"].sum(), 2),
                "ROI %": round(roi(g), 1),
                "Avg CLV": round(g["clv"].mean(), 2) if g["clv"].notna().any() else None,
            }
            for t, g in done.groupby("Type")
        ]).sort_values("Type", ascending=False)
        st.dataframe(styled(by_type, signed_cols=["Profit (u)", "ROI %", "Avg CLV"]),
                     hide_index=True, width="stretch")

        st.subheader("Completed bets")
        table = done.sort_values("game_date", ascending=False).copy()
        table["Date"] = short_date(table["game_date"])
        table["Game"] = table["away_team"] + " @ " + table["home_team"]
        table["Bet"] = table["pick"] + " " + table["line"].map(lambda v: f"{v:g}")
        table["Odds"] = table["odds"].map(fmt_odds)
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
            height=36 * (len(table) + 1) + 4,
        )

    if not pending.empty:
        st.subheader("Pending")
        p = pending.sort_values("game_date").copy()
        p["Date"] = short_date(p["game_date"])
        p["Game"] = p["away_team"] + " @ " + p["home_team"]
        p["Bet"] = p["pick"] + " " + p["line"].map(lambda v: f"{v:g}")
        p["Odds"] = p["odds"].map(fmt_odds)
        st.dataframe(p.rename(columns={"book": "Book", "units": "Units"})[
            ["Date", "Game", "Bet", "Odds", "Book", "Units"]], hide_index=True, width="stretch")

    st.caption("Units are the amount risked. CLV is how many points better your number was than "
               "the closing total (positive = you beat the close).")

st.caption("Model picks for entertainment and research. Lines and odds are the best available "
           "when the file was posted and may have moved.")
