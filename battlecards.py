"""Battlecard storage and public company directory lookup."""

import json
import sqlite3
from functools import cache, lru_cache
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from app import DATA, database
from research import DIRECTORY, LABELS, discover, fetch, now, verify

YC = "https://raw.githubusercontent.com/yc-oss/api/main/companies/all.json"


def identity(name):
    return " ".join(name.split()).casefold()


@lru_cache(maxsize=1)
def supplied():
    path = Path(__file__).parent / "data" / "battlecards.json"
    return {identity(c["name"]): c for c in json.loads(path.read_text())}


def library():
    DATA.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS company_cards (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )
        additions = db.execute("SELECT id, body FROM company_cards").fetchall()
    cards = supplied().copy()
    for row in additions:
        key=row["id"]
        cards["analyst:"+key if key in cards else key]=json.loads(row["body"])
    return cards


def save_card(card, previous=None):
    key = identity(card["name"])
    body = json.dumps(card, ensure_ascii=False)
    if not key or (key in supplied() and previous is None):
        raise ValueError("A supplied card already uses that company name.")
    with database() as db:
        if previous is not None:
            old = json.dumps(previous, ensure_ascii=False)
            if (
                db.execute(
                    "UPDATE company_cards SET body=? WHERE id=? AND body=?",
                    (body, key, old),
                ).rowcount
                != 1
            ):
                raise ValueError(
                    "Another session updated this card. Reopen it before editing."
                )
        else:
            try:
                db.execute("INSERT INTO company_cards VALUES (?,?)", (key, body))
            except sqlite3.IntegrityError:
                raise ValueError("This company already has a battlecard.") from None


def valid_link(value):
    try:
        u = urlsplit(value)
        return (
            u.scheme in {"http", "https"}
            and bool(u.hostname)
            and not u.username
            and not u.password
        )
    except ValueError:
        return False


def read_json(url, limit):
    with urlopen(
        Request(url, headers={"User-Agent": "SignalDesk/1.0 (company research)"}),
        timeout=8,
    ) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Company directory response is too large")
    return json.loads(raw)


def directory_entries(provider):
    if provider == "YC directory":
        return [
            (c["name"], c.get("one_liner") or "", c["url"])
            for c in read_json(YC, 24_000_000)
            if c.get("name") and c.get("url")
        ]
    return [
        (c["name"], c["description"], DIRECTORY) for c in discover(fetch(DIRECTORY))
    ]


def wikipedia(query):
    params = urlencode(
        dict(
            action="query",
            generator="search",
            gsrsearch=query,
            gsrlimit=5,
            prop="extracts|info",
            inprop="url",
            exintro=1,
            explaintext=1,
            exsentences=2,
            exlimit=5,
            format="json",
        )
    )
    result = read_json("https://en.wikipedia.org/w/api.php?" + params, 1_000_000)
    if "error" in result:
        raise ValueError("Wikipedia search unavailable")
    pages = result.get("query", {}).get("pages", {}).values()
    return [
        (p["title"], p.get("extract", ""), p["fullurl"])
        for p in sorted(pages, key=lambda p: p["index"])
    ]


def matching(entries, query):
    term = identity(query)
    hits = [e for e in entries if term in identity(e[0])]
    hits.sort(
        key=lambda hit: (
            identity(hit[0]) != term,
            not identity(hit[0]).startswith(term),
            len(hit[0]),
        )
    )
    return hits[:12]


def draft_from_directory(name, description, url):
    @cache
    def snapshot(source):
        try:
            return fetch(source), now()
        except (ValueError, OSError):
            return None, None

    facts = verify(name, list(LABELS), snapshot)
    return {
        "name": name,
        "overview": description,
        "source": url,
        "features": [[LABELS[key], "Documented", ""] for key in facts],
        "evidence": {LABELS[key]: fact for key, fact in facts.items()},
    }
