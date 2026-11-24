"""Streamlit front end for Signal Desk (for Streamlit Community Cloud).

Reuses the research engine and SQLite storage from app.py; runs research
inline instead of through the FastAPI background worker.
"""

import hmac
import json
import os
import re

import streamlit as st

from app import DATA, SCENARIO, database, enqueue, fingerprint, research
from research import DEFAULT, LABELS

st.set_page_config(page_title="Signal Desk", page_icon="📡", layout="wide")

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
            st.rerun()
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


def new_research_form():
    with st.sidebar.expander("New research", expanded=False):
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


def main():
    gate()
    init_db()
    process_pending()

    st.sidebar.title("📡 Signal Desk")
    st.sidebar.caption("Competitor research for iPaaS sales and product teams")
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
    names = {r["id"]: f"{r['title']} · {r['status']} · {r['created'][:16]}" for r in runs}
    default = ids.index(st.session_state["selected"]) if st.session_state.get("selected") in ids else 0
    rid = st.sidebar.radio("Research history", ids, index=default, format_func=names.get)
    st.session_state.selected = rid

    run = load_run(rid)
    st.title(run["title"])
    st.caption(f"Status: **{run['status']}** · created {run['created']}")
    if run["issues"]:
        st.warning("Some sources were unavailable: " + ", ".join(run["issues"]))

    crit = run["criteria"]
    compare, inspect = st.tabs(["Compare", "Inspect"])

    with compare:
        rows = []
        for p in run["profiles"]:
            row = {"Vendor": p["name"], "Coverage": f"{p['score']}/{len(crit)}"}
            for k in crit:
                fact = p["facts"].get(k)
                row[LABELS[k]] = (
                    "Unknown" if not fact
                    else "Dismissed" if fact["review"] == "dismissed"
                    else "✅ Approved" if fact["review"] == "approved"
                    else "Evidence"
                )
            rows.append(row)
        st.dataframe(rows, hide_index=True, width="stretch")
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
                    st.caption(f"[Source]({fact['url']}) · checked {fact['checked']}")
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
