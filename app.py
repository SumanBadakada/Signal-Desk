import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from research import DEFAULT, DIRECTORY, LABELS, discover, fetch, now, verify

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SIGNAL_DATA", ROOT / ".signal-data"))
SCENARIO = "Agency reporting pipeline"
log = logging.getLogger("uvicorn.error")


@contextmanager
def database():
    conn = sqlite3.connect(DATA / "research.sqlite3", timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class Brief(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    criteria: list[str] = Field(min_length=1, max_length=len(LABELS))

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if not value.strip():
            raise ValueError("Enter a brief title")
        return value.strip()

    @field_validator("criteria")
    @classmethod
    def valid_keys(cls, keys):
        if len(keys) != len(set(keys)) or set(keys) - LABELS.keys():
            raise ValueError("Choose distinct supported requirements")
        return keys


def enqueue(conn, title, criteria):
    rid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
        (rid, title, json.dumps(criteria), now(), "queued", "[]", "[]"),
    )
    return rid


def research(run):
    issues = []

    def snapshot(url):
        with database() as conn:
            row = conn.execute(
                "SELECT body, checked FROM sources WHERE run=? AND url=?",
                (run["id"], url),
            ).fetchone()
            if row:
                return row["body"], row["checked"]
        try:
            body = fetch(url)
        except (OSError, ValueError) as exc:
            log.warning("Source unavailable %s (%s)", url, type(exc).__name__)
            issues.append(url)
            return None, None
        checked = now()
        with database() as conn:
            conn.execute(
                "INSERT INTO sources VALUES(?,?,?,?)", (run["id"], url, body, checked)
            )
        return body, checked

    profiles = []
    try:
        body, _ = snapshot(DIRECTORY)
        if body is None:
            raise ValueError("Candidate directory unavailable")
        profiles = discover(body)
        for profile in profiles:
            profile["facts"] = verify(
                profile["name"], json.loads(run["criteria"]), snapshot
            )
        state = "partial" if issues else "complete"
    except Exception as exc:
        log.exception("Research run failed")
        issues.append(str(exc))
        state = "failed"
    with database() as conn:
        conn.execute(
            "UPDATE runs SET status=?, profiles=?, issues=? WHERE id=?",
            (state, json.dumps(profiles), json.dumps(issues), run["id"]),
        )


def worker(stop):
    with (DATA / "worker.lock").open("a") as lock:
        while not stop.is_set():
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                stop.wait(1)
        while not stop.is_set():
            with database() as conn:
                run = conn.execute(
                    "SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY rowid LIMIT 1"
                ).fetchone()
                if run:
                    conn.execute(
                        "UPDATE runs SET status='running' WHERE id=?", (run["id"],)
                    )
            if run:
                research(run)
            else:
                stop.wait(0.4)


@asynccontextmanager
async def lifespan(app):
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
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM runs LIMIT 1").fetchone():
            enqueue(conn, SCENARIO, DEFAULT)
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(stop,), daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(2)


app = FastAPI(title="Signal Desk", version="1", lifespan=lifespan)


@app.get("/api/v1/workspace")
def workspace():
    with database() as conn:
        rows = conn.execute(
            "SELECT id,title,created,status FROM runs ORDER BY rowid DESC LIMIT 100"
        ).fetchall()
    return {"runs": [dict(r) for r in rows], "criteria": LABELS, "default": DEFAULT}


@app.post("/api/v1/runs", status_code=202)
def create(brief: Brief):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if (
            conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            >= 5
        ):
            raise HTTPException(429, "Research queue is full")
        return {"id": enqueue(conn, brief.title, brief.criteria)}


def get_run(conn, rid):
    row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "Research run missing")
    return dict(row)


def fingerprint(fact):
    return hashlib.sha256((fact["url"] + "\n" + fact["quote"]).encode()).hexdigest()


@app.get("/api/v1/runs/{rid}")
def detail(rid: str):
    with database() as conn:
        run = get_run(conn, rid)
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


@app.get("/api/v1/runs/{rid}/snapshot", response_class=PlainTextResponse)
def snapshot_text(rid: str, url: str):
    with database() as conn:
        row = conn.execute(
            "SELECT body FROM sources WHERE run=? AND url=?", (rid, url)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Snapshot missing")
    return PlainTextResponse(row["body"], headers={"X-Content-Type-Options": "nosniff"})


class Review(BaseModel):
    vendor: str
    criterion: str
    decision: str = Field(pattern="^(approved|dismissed|unreviewed)$")


@app.put("/api/v1/runs/{rid}/review")
def review(rid: str, edit: Review):
    with database() as conn:
        profiles = json.loads(get_run(conn, rid)["profiles"])
        fact = next(
            (
                p["facts"].get(edit.criterion)
                for p in profiles
                if p["name"] == edit.vendor
            ),
            None,
        )
        if not fact:
            raise HTTPException(404, "Finding missing")
        conn.execute(
            "INSERT OR REPLACE INTO reviews VALUES(?,?,?,?)",
            (edit.vendor, edit.criterion, fingerprint(fact), edit.decision),
        )
    return {"decision": edit.decision}


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="site")
