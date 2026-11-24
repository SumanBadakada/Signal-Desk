"""Import the refreshed sales cards and public citations from the July OneDrive archive."""

import json
import re
import sys
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


def document(archive, name):
    with ZipFile(BytesIO(archive.read(name))) as doc:
        return ET.fromstring(doc.read("word/document.xml")).find("{*}body")


def paragraphs(element):
    return [
        v
        for p in element.findall(".//{*}p")
        if (v := "".join(t.text or "" for t in p.findall(".//{*}t")).strip())
    ]


def rows(table):
    return [
        ["\n".join(paragraphs(c)) for c in row.findall("{*}tc")]
        for row in table.findall("{*}tr")
    ]


def normalized(name):
    return re.sub("[^a-z0-9]", "", name.lower())


def import_card(archive, filename):
    header, quadrant, insight, ratings, features, pricing, apart, _, _, architecture = (
        document(archive, filename).findall("{*}tbl")
    )
    _, name, *headline = paragraphs(header)
    overview = rows(quadrant)[0][-1].splitlines()[1:]
    ratings_text = paragraphs(ratings)
    scores = {}
    for platform in ("G2", "Capterra"):
        if platform in ratings_text:
            i = ratings_text.index(platform)
            excerpt = ratings_text[i + 1 : i + 3]
            if excerpt and re.match(r"^[0-5](?:\.\d+)?\s*/\s*5", excerpt[0]):
                scores[platform] = " ".join(
                    s for s in excerpt if s not in ("G2", "Capterra")
                )
    prices = rows(pricing)
    feature_rows = [r for r in rows(features)[2:] if len(r) == 3]
    for r in feature_rows:
        r[2] = re.sub(r"\niPaaS\s+DI\s*$|\nDI\s*$", "", r[2])
    tech = [r for r in rows(architecture)[2:] if len(r) == 3]
    pairs = [r for r in prices if len(r) == 2 and re.match(r"^1?\d[.\s]", r[0])]
    assert (len(feature_rows), len(tech), len(pairs)) == (20, 8, 4), name
    category = re.search(r"beyond (.+?)\.?$", " ".join(headline), re.I)
    category = category.group(1) if category else ""
    if "confirm before" in category.lower():
        category = ""
    pitch = paragraphs(insight)[1:2]
    return dict(
        name=name,
        headline=category or "Competitive product profile",
        overview="\n\n".join(overview),
        insight=pitch[0] if pitch else "",
        features=feature_rows,
        pricing=prices[2:6],
        architecture=tech,
        challenges=[r[0] for r in pairs],
        value=[r[1] for r in pairs],
        difference=[v for r in rows(apart)[1:4] for v in r if v],
        reviews={
            "ratings": scores,
            "period": "Reported in the July 2026 source edition",
            "note": next(
                (v.lstrip("⚠ ") for v in ratings_text if v.startswith("⚠")), ""
            ),
        },
        source=Path(filename).name,
        origin="Supplied battlecard",
        edition="July 2026",
    )


def citations(archive, path, cards):
    names = {normalized(c["name"]): c["name"] for c in cards}
    result = defaultdict(list)
    current = None
    for el in document(archive, path):
        if not el.tag.endswith("}p"):
            continue
        s = "".join(t.text or "" for t in el.findall(".//{*}t")).strip()
        if normalized(s) in names:
            current = names[normalized(s)]
        elif current and re.match(r"^\[\d+\]", s):
            match = re.search(r"https?://[^\s|<>]+", s)
            if match:
                result[current].append(
                    {
                        "label": s[: match.start()].removesuffix("URL:").strip(),
                        "url": match[0].rstrip(".,;)"),
                    }
                )
    return result


def main():
    with ZipFile(sys.argv[1]) as archive:
        names = archive.namelist()
        paths = [
            n
            for n in names
            if "/MADTECH_Battlecards_Updated_" in n
            and "/Battlecards/MADTECH_vs_" in n
            and n.endswith(".docx")
        ]
        cards = [import_card(archive, n) for n in sorted(paths)]
        assert len(cards) == len({c["name"] for c in cards}) == 91
        sources = citations(
            archive,
            next(
                n
                for n in names
                if "Updated_" in n and n.endswith("_Sources_and_Citations.docx")
            ),
            cards,
        )
        for c in cards:
            c["citations"] = sources[c["name"]]
    target = Path(sys.argv[2])
    aliases = {"fuseai": "fuse", "keendecisionsystems": "keen"}
    if target.exists():
        prior = {normalized(c["name"]): c for c in json.loads(target.read_text())}
        for card in cards:
            key = normalized(card["name"])
            old = prior.get(key) or prior.get(aliases.get(key))
            if not old:
                continue
            if old.get("earlier_voice"):
                card["earlier_voice"] = old["earlier_voice"]
            elif old["reviews"].get("likes"):
                card["earlier_voice"] = {
                    k: old["reviews"][k] for k in ("likes", "concerns", "period")
                }
                card["earlier_voice"]["source"] = old["source"]
    target.write_text(json.dumps(cards, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
