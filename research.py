"""Discover leads from a directory and verify narrowly scoped first-party claims."""

import re
import time
from urllib.error import HTTPError, URLError
from datetime import UTC, datetime
from urllib.request import Request, urlopen
from urllib.parse import urlparse

RAW = "https://raw.githubusercontent.com/"
DIRECTORY = RAW + "pawl/awesome-etl/master/README.md"
AIR = "airbytehq/airbyte/master/docs/"
AZURE = "MicrosoftDocs/azure-docs/main/articles/data-factory/"
LABELS = {
    "ads": "Google Ads source",
    "snow": "Snowflake destination",
    "big": "BigQuery destination",
    "daily": "Daily schedule",
    "fast": "15-minute schedule",
    "history": "90-day ad backfill",
    "python": "Custom Python connectors",
}
AWS_SCHEDULE = "awsdocs/aws-glue-developer-guide/master/doc_source/monitor-data-warehouse-schedule.md"
STITCH = "stitchdata/docs/master/"
DEFAULT = ["ads", "snow", "daily", "history"]
# Anchor phrases are scoped to one specific first-party document and capability.
# A changed phrase goes to review as unknown until verified again.
SOURCES = {
    "Airbyte": {
        "ads": (
            AIR + "integrations/sources/google-ads.md",
            "source connector.",
            "Google Ads",
        ),
        "snow": (
            AIR + "integrations/destinations/snowflake.md",
            "step-by-step process of setting up the Snowflake destination connector.",
            "",
        ),
        "big": (
            AIR + "integrations/destinations/bigquery.md",
            "guides you through setting up the BigQuery destination connector.",
            "",
        ),
        "daily": (
            AIR + "platform/using-airbyte/core-concepts/sync-schedules.md",
            "Every 24 hours (most common)",
            "",
        ),
        "fast": (
            AIR + "platform/using-airbyte/core-concepts/sync-schedules.md",
            "Every 15 minutes (Airbyte Cloud Plus)",
            "",
        ),
    },
    "Microsoft Azure Data Factory": {
        "ads": (
            AZURE + "connector-google-adwords.md",
            "to copy data from Google Ads.",
            "",
        ),
        "snow": (
            AZURE + "connector-snowflake.md",
            "copy data from and to Snowflake",
            "",
        ),
    },
    "Stitch": {
        "snow": (
            STITCH + "_destinations/snowflake/guides/connecting-snowflake.md",
            "Connect a Snowflake database to your Stitch account as a destination.",
            "",
        ),
        "ads": (
            STITCH + "_saas-integrations/google-ads/v2/google-ads-v2.md",
            "approve Stitch’s access to your Google Ads data.",
            "",
        ),
    },
    "AWS Glue": {
        "daily": (AWS_SCHEDULE, "to run on a schedule of every day at 12:15 UTC", ""),
        "fast": (AWS_SCHEDULE, "Run every 15 minutes", "0/15"),
    },
    "Fivetran": {
        "ads": (
            "fivetran/dbt_google_ads/main/README.md",
            "transforms data from Fivetran's Google Ads connector",
            "",
        ),
        "python": (
            "fivetran/fivetran_connector_sdk/main/README.md",
            "code a custom data connector using Python",
            "",
        ),
    },
}


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def fetch(url):
    request = Request(url, headers={"User-Agent": "SignalDesk-Research/1"})
    for attempt in range(2):
        try:
            with urlopen(request, timeout=8) as response:
                body = response.read(2_000_001)
            break
        except HTTPError as error:
            if error.code not in {502, 503, 504} or attempt:
                raise
        except (URLError, TimeoutError):
            if attempt:
                raise
        time.sleep(0.5)
    if len(body) > 2_000_000:
        raise ValueError("Source exceeds size limit")
    return body.decode("utf-8")


def discover(body):
    section = re.search(r"^## Cloud Services\s*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    if not section:
        raise ValueError("Directory format changed")
    profiles = []
    for name, site, description in re.findall(
        r"^- \[([^\]]+)\]\((https?://[^)]+)\) - (.+)$", section[1], re.M
    ):
        if re.search(
            r"integration|data movement|ETL|integrate data|data sources|data preparation",
            description,
            re.I,
        ):
            profiles.append(
                dict(
                    name=name,
                    site=site,
                    domain=urlparse(site).hostname,
                    description=description.strip('"'),
                    directory=DIRECTORY,
                    facts={},
                )
            )
    if not profiles:
        raise ValueError("Directory returned no candidates")
    return profiles[:40]


def verify(name, criteria, snapshot):
    facts = {}
    for key in criteria:
        spec = SOURCES.get(name, {}).get(key)
        if not spec:
            continue
        path, anchor, context = spec
        url = RAW + path
        body, timestamp = snapshot(url)
        if body is None:
            continue
        line = next(
            (s.strip() for s in body.splitlines() if anchor in s and context in s), None
        )
        if line:
            facts[key] = dict(quote=line, url=url, checked=timestamp)
    return facts
