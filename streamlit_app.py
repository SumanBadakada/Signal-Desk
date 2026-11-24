"""Streamlit front end for Signal Desk (for Streamlit Community Cloud).

Reuses the research engine and SQLite storage from app.py; runs research
inline instead of through the FastAPI background worker.
"""

import hmac
import json
import os
import re
from datetime import datetime
from html import escape
from pathlib import Path

import streamlit as st

from chat_ui import chat
from intelligence_ui import dashboard, monitor
from battlecard_ui import battlecards
from app import DATA, SCENARIO, database, enqueue, fingerprint, research
from research import DEFAULT, LABELS

st.set_page_config(page_title="Signal Desk", page_icon=":material/insights:", layout="wide")

PATTERNS = {
    "ads": r"google ads|adwords",
    "snow": r"snowflake",
    "big": r"bigquery",
    "daily": r"daily|every (morning|night|day|24 hours)",
    "fast": r"15[- ]min|quarter.hour",
    "history": r"90[- ]days?",
    "python": r"python",
}


def secret_password():
    try:
        return st.secrets.get("SIGNAL_PASSWORD") or os.environ.get("SIGNAL_PASSWORD")
    except Exception:
        return os.environ.get("SIGNAL_PASSWORD")


def gate():
    password = secret_password()
    if not password or st.session_state.get("authed"):
        return
    st.title("Signal Desk")
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state.authed = True
            return
        st.error("Incorrect password")
    st.stop()


@st.cache_resource
def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    with database() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,title TEXT,criteria TEXT,created TEXT,status TEXT,profiles TEXT,issues TEXT);
        CREATE TABLE IF NOT EXISTS sources(run TEXT,url TEXT,body TEXT,checked TEXT,PRIMARY KEY(run,url));
        CREATE TABLE IF NOT EXISTS reviews(vendor TEXT,criterion TEXT,fingerprint TEXT,decision TEXT,PRIMARY KEY(vendor,criterion,fingerprint));
        """
        )
        if not conn.execute("SELECT 1 FROM runs LIMIT 1").fetchone():
            enqueue(conn, SCENARIO, DEFAULT)
    return True


def process_pending():
    """Run any queued or interrupted research (replaces the background worker)."""
    while True:
        with database() as conn:
            run = conn.execute(
                "SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY rowid LIMIT 1"
            ).fetchone()
            if run:
                conn.execute("UPDATE runs SET status='running' WHERE id=?", (run["id"],))
        if not run:
            return
        with st.spinner(f"Researching “{run['title']}”…"):
            research(run)


def load_run(rid):
    with database() as conn:
        run = dict(conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())
        for key in ("profiles", "criteria", "issues"):
            run[key] = json.loads(run[key])
        for p in run["profiles"]:
            p["score"] = 0
            for criterion, fact in p["facts"].items():
                row = conn.execute(
                    "SELECT decision FROM reviews WHERE vendor=? AND criterion=? AND fingerprint=?",
                    (p["name"], criterion, fingerprint(fact)),
                ).fetchone()
                fact["review"] = row[0] if row else "unreviewed"
                p["score"] += int(fact["review"] != "dismissed")
    run["profiles"].sort(key=lambda p: (-p["score"], p["name"]))
    return run


def save_review(vendor, criterion, fact, decision):
    with database() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reviews VALUES(?,?,?,?)",
            (vendor, criterion, fingerprint(fact), decision),
        )


@st.dialog("New research")
def new_research_form():
    title = st.text_input(
        "Buyer workflow",
        max_chars=100,
        placeholder="e.g. Load Google Ads into Snowflake daily",
        key="brief_title",
    )
    if st.button("Suggest requirements"):
        matches = [k for k, p in PATTERNS.items() if re.search(p, title, re.I)]
        if matches:
            for k in LABELS:
                st.session_state[f"crit_{k}"] = k in matches
            st.info("Review the suggested requirements below.")
        else:
            st.info("Choose the relevant requirements below.")
    st.caption("Requirements to compare")
    criteria = [
        k
        for k, label in LABELS.items()
        if st.checkbox(label, key=f"crit_{k}", value=st.session_state.get(f"crit_{k}", k in DEFAULT))
    ]
    if st.button("Run discovery", type="primary"):
        if not title.strip() or not criteria:
            st.error("Enter a title and select at least one requirement.")
        else:
            with database() as conn:
                busy = conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                if busy >= 5:
                    st.error("Research queue is full")
                    return
                st.session_state.selected = enqueue(conn, title.strip(), criteria)
            st.rerun()



def wordmark():
    return '<i class="sd-mark" aria-hidden="true"><i></i><i></i><i></i></i>'


def research_overview(run):
    criteria = run["criteria"]
    profiles = run["profiles"]
    total = len(criteria)
    chips = "".join(f'<span class="sd-chip">{escape(LABELS[key])}</span>' for key in criteria)
    bars = []
    for vendor in [p for p in profiles if p["score"]][:3]:
        score = vendor["score"]
        bars.append(f"""
            <div class="sd-bar-row"><div class="sd-bar-text"><b>{escape(vendor["name"])}</b>
            <span>{score}/{total}</span></div>
            <div class="sd-track"><i style="width:{100 * score / total:.1f}%"></i></div></div>
        """)
    chart = "".join(bars) or "<p>No documented matches in this run.</p>"
    created = datetime.fromisoformat(run["created"]).strftime("%d %b %Y, %H:%M UTC")
    status = escape(run["status"])
    st.html(f"""
        <section class="sd-brief" aria-label="Research brief">
          <div><div class="sd-label">Research brief</div><h1>{escape(run["title"])}</h1>
          <div class="sd-chips">{chips}</div>
          <div class="sd-meta"><span class="sd-status {status}">{status.capitalize()}</span>
          <time datetime="{escape(run["created"])}">{created}</time></div></div>
          <div class="sd-chart"><div class="sd-chart-title"><b class="sd-label">Top matches</b>
          <small>Criteria met</small></div>{chart}</div>
        </section>
    """)
    metrics = [
        ("Candidate vendors", len(profiles)),
        ("Vendors with evidence", sum(p["score"] > 0 for p in profiles)),
        ("Requirements", total),
    ]
    cards = "".join(f'<div class="sd-stat"><span>{label}</span><strong>{value}</strong></div>'
                    for label, value in metrics)
    st.html(f'<div class="sd-summary">{cards}</div>')


def main():
    st.html(Path(__file__).with_name("streamlit_ui.css"))
    if st.session_state.pop("goto_monitor",False):
        st.session_state.workspace_view="Live source monitor"
    if "open_company" in st.session_state:
        st.session_state.card_selection = st.session_state.pop("open_company")
        st.session_state.workspace_view = "Battlecard library"
    with st.sidebar:
        st.html(f'<div class="sd-brand">{wordmark()} Signal Desk</div>')
        view = st.radio("Workspace", ["Landscape dashboard", "Ask Signal Desk", "Battlecard library", "Live source monitor", "Find and add companies", "Workflow research"], key="workspace_view")
    if view == "Ask Signal Desk":
        chat()
        return
    if view == "Landscape dashboard":
        dashboard()
        return
    if view == "Live source monitor":
        monitor()
        return
    if view != "Workflow research":
        battlecards(gate, view)
        return
    gate()
    init_db()
    process_pending()
    heading, action = st.columns([3, 1], vertical_alignment="center")
    with heading:
        st.html(f'<div class="sd-heading">{wordmark()} Signal Desk <span>Competitor research</span></div>')
    with action:
        if st.button("New research", icon=":material/add:", type="primary", width="stretch"):
            new_research_form()

    with database() as conn:
        runs = [
            dict(r)
            for r in conn.execute(
                "SELECT id,title,created,status FROM runs ORDER BY rowid DESC LIMIT 100"
            )
        ]
    if not runs:
        st.info("No research yet.")
        return
    ids = [r["id"] for r in runs]
    names = {
        r["id"]: f"{r['title']} · {datetime.fromisoformat(r['created']):%b %d, %H:%M}"
        for r in runs
    }
    default = ids.index(st.session_state["selected"]) if st.session_state.get("selected") in ids else 0
    rid = st.sidebar.radio("Research history", ids, index=default, format_func=names.get)
    st.session_state.selected = rid

    run = load_run(rid)
    research_overview(run)
    if run["issues"]:
        st.warning("Some sources were unavailable: " + ", ".join(run["issues"]))

    crit = run["criteria"]
    compare, inspect = st.tabs(["Vendor comparison", "Evidence review"])

    with compare:
        rows = []
        for p in run["profiles"]:
            row = {"Vendor": p["name"], "Coverage": p["score"]}
            for k in crit:
                fact = p["facts"].get(k)
                row[LABELS[k]] = (
                    "Unknown" if not fact
                    else "Dismissed" if fact["review"] == "dismissed"
                    else "Approved" if fact["review"] == "approved"
                    else "Documented"
                )
            rows.append(row)
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch", row_height=40,
                         column_config={
                             "Vendor": st.column_config.TextColumn(pinned=True, width=185),
                             "Coverage": st.column_config.ProgressColumn(
                                 min_value=0, max_value=len(crit), format=f"%d / {len(crit)}"
                             ),
                         })
        else:
            st.info("No vendors returned. Start a new research run to try again.")
        st.caption(
            "Unknown means the collected sources do not establish support. "
            "Coverage counts individual capabilities and does not certify a complete pipeline."
        )

    with inspect:
        with_facts = [p for p in run["profiles"] if p["facts"]]
        if not with_facts:
            st.info("No cited evidence in this run.")
        for p in with_facts:
            with st.expander(f"{p['name']} — {p['score']}/{len(crit)}"):
                st.markdown(f"[{p['domain']}]({p['site']}) · {p['description']}")
                for k, fact in p["facts"].items():
                    st.markdown(f"**{LABELS[k]}**")
                    st.markdown(f"> {fact['quote']}")
                    st.caption(
                        f"[Vendor source ↗]({fact['url']}) · Checked "
                        f"{datetime.fromisoformat(fact['checked']):%d %b %Y, %H:%M UTC}"
                    )
                    options = ["unreviewed", "approved", "dismissed"]
                    choice = st.radio(
                        "Review",
                        options,
                        index=options.index(fact["review"]),
                        key=f"rev_{rid}_{p['name']}_{k}",
                        horizontal=True,
                        label_visibility="collapsed",
                    )
                    if choice != fact["review"]:
                        save_review(p["name"], k, fact, choice)
                        st.rerun()


main()
