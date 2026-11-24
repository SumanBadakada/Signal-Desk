"""Time-stamped observations from the curated product-source catalog."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import DATA, database


@lru_cache(maxsize=1)
def feeds():
    return json.loads((Path(__file__).parent / "data/source_monitor.json").read_text())


def init_monitor():
    DATA.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.executescript(
            """
        CREATE TABLE IF NOT EXISTS source_observations (
            id INTEGER PRIMARY KEY, company TEXT NOT NULL, url TEXT NOT NULL,
            ts INTEGER NOT NULL, outcome TEXT NOT NULL, summary TEXT NOT NULL, digest TEXT
        );
        CREATE INDEX IF NOT EXISTS source_times ON source_observations(url,ts DESC);
        CREATE TABLE IF NOT EXISTS source_lease (url TEXT PRIMARY KEY, until_ts INTEGER);
        """
        )


class VisibleText(HTMLParser):
    skip = {"script", "style", "template", "noscript", "nav", "footer", "svg", "header"}
    blocks = {"p", "div", "section", "li", "td", "h1", "h2", "h3", "h4", "title", "br"}

    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.skip:
            self.hidden += 1
        if not self.hidden and tag in self.blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skip:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def observe(feed):
    req = Request(
        feed["url"],
        headers={"User-Agent": "SignalDesk/1.0 (public product source monitor)"},
    )
    with urlopen(req, timeout=7) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Source exceeds the snapshot limit")
        text = raw.decode(
            response.headers.get_content_charset() or "utf-8", errors="replace"
        )
    if feed["kind"] == "release":
        obj = json.loads(text)
        info = obj.get("info", obj)
        version = info.get("version")
        if not isinstance(version, str) or not version:
            raise ValueError("Version unavailable")
        return f"{feed['label']} · {version}"
    parser = VisibleText()
    parser.feed(text)
    lines = list(
        dict.fromkeys(
            " ".join(line.split())
            for line in "".join(parser.parts).splitlines()
            if line.strip()
        )
    )
    if len(" ".join(lines)) < 100 or re.search(
        r"just a moment|verify you are human|access denied|checking your browser",
        " ".join(lines[:6]),
        re.I,
    ):
        raise ValueError("Page requires a browser or is unavailable")
    pattern = (
        r"[$€£]\s*\d|per (month|user|seat)|billed annually|contact sales|free trial"
        if feed["label"] == "Pricing page"
        else r"connector|integrat|pipeline|platform|analytics|product|introduc|release"
    )
    selected = [line[:360] for line in lines if re.search(pattern, line, re.I)]
    if not selected:
        raise ValueError("No readable matching product text")
    return "\n".join(selected[:8])


def check_source(company, feed, interval):
    url = feed["url"]
    start = int(time.time())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        last = db.execute(
            "SELECT MAX(ts) FROM source_observations WHERE url=?", (url,)
        ).fetchone()[0]
        lease = db.execute(
            "SELECT until_ts FROM source_lease WHERE url=?", (url,)
        ).fetchone()
        if (last and start - last < interval) or (lease and lease[0] > start):
            return
        db.execute("INSERT OR REPLACE INTO source_lease VALUES(?,?)", (url, start + 90))
    digest = None
    try:
        summary = observe(feed)
        digest = sha256(summary.encode()).hexdigest()
    except HTTPError as exc:
        summary = f"Provider returned HTTP {exc.code}"
    except (OSError, ValueError, LookupError) as exc:
        summary = str(exc)[:220]
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        last = db.execute(
            "SELECT digest FROM source_observations WHERE url=? AND digest IS NOT NULL ORDER BY id DESC LIMIT 1",
            (url,),
        ).fetchone()
        if digest is None:
            outcome = "Unavailable"
        elif not last:
            outcome = "Baseline"
        elif last[0] == digest:
            outcome = "Unchanged"
        else:
            outcome = "Changed"
        db.execute(
            "INSERT INTO source_observations(company,url,ts,outcome,summary,digest) VALUES(?,?,?,?,?,?)",
            (company, url, int(time.time()), outcome, summary, digest),
        )
        db.execute("DELETE FROM source_lease WHERE url=?", (url,))


def refresh(company, manual):
    sources = feeds()[company]
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        list(
            pool.map(
                lambda feed: check_source(company, feed, 30 if manual else 300), sources
            )
        )


def history(company):
    with database() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM source_observations WHERE company=? ORDER BY id DESC LIMIT 500",
                (company,),
            )
        ]


def latest_checks():
    with database() as db:
        return [
            dict(r)
            for r in db.execute(
                """
            SELECT company,url,ts,outcome,summary FROM source_observations
            WHERE id IN (SELECT MAX(id) FROM source_observations GROUP BY url)
            ORDER BY ts DESC,id DESC LIMIT 5
        """
            )
        ]


def last_success(url):
    with database() as db:
        row = db.execute(
            "SELECT * FROM source_observations WHERE url=? AND digest IS NOT NULL ORDER BY id DESC LIMIT 1",
            (url,),
        ).fetchone()
        return dict(row) if row else None


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %Y, %H:%M:%S UTC")
