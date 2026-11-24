from datetime import datetime, timezone
import re

import altair as alt
import pandas as pd
import streamlit as st

from battlecards import library
from product_monitor import (
    feeds,
    history,
    init_monitor,
    refresh,
    utc,
    latest_checks,
    last_success,
)

STATES = ["Supported", "Limited", "Unavailable", "Unverified"]
COLORS = ["#087E83", "#F1AA41", "#738398", "#D6DFE9"]


def support(text):
    s = text.strip()
    if re.search(
        r"not verified|unverified|confirm before|not publicly confirmed|not captured|not confirmed|insufficient evidence",
        s,
        re.I,
    ) or re.fullmatch(r"[✓!✗]?\s*[0-5]\.\d", s):
        return "Unverified"
    if s.startswith(("✓", "✅", "V  ", "Supported")) or s == "Documented":
        return "Supported"
    if s.startswith(("!", "⚠", "Limited")):
        return "Limited"
    if s.startswith(("✗", "❌", "X  ", "Unavailable")):
        return "Unavailable"
    return "Unverified"


def feature_rows(cards):
    return [
        {
            "Company": c["name"],
            "Capability": (
                "Pre-built data connectors"
                if label.lower()
                in {"pre-built marketing data connectors", "pre-built data connectors"}
                else label
            ),
            "Status": support(v),
            "MADTECH.AI": support(m),
        }
        for c in cards
        for label, v, m in c["features"]
    ]


def dashboard():
    cards = library()
    st.html(
        '<div class="sd-heading">Signal Desk <span>Market intelligence</span></div>'
    )
    st.html(
        """<section class="sd-dashboard-heading">
        <div class="sd-label">July 2026 research edition</div><h1>Competitive landscape</h1>
        <p>Compare product capabilities and review the evidence behind each assessment.</p>
        </section>"""
    )
    canonical = [c for c in cards.values() if c.get("edition")]
    records = pd.DataFrame(feature_rows(canonical))
    choices = sorted(records["Capability"].unique())
    selection = st.multiselect(
        "Capabilities to compare", choices, placeholder="All capabilities"
    )
    selected = records[records["Capability"].isin(selection)] if selection else records
    metrics = st.columns(3)
    metrics[0].metric("Products in view", selected.Company.nunique())
    metrics[1].metric("Capability assessments", f"{len(selected):,}")
    metrics[2].metric(
        "Unverified assessments", int((selected.Status == "Unverified").sum())
    )
    with st.container(border=True):
        st.subheader("How products compare")
        st.caption(
            "Up to 12 products, ranked by supported capabilities. Each segment counts assessments in the current selection."
        )
        counts = (
            selected.groupby(["Company", "Status"])
            .size()
            .unstack(fill_value=0)
            .reindex(columns=STATES, fill_value=0)
        )
        top = counts.sort_values(
            ["Supported", "Limited"], ascending=False, kind="stable"
        ).head(12)
        chartdata = top.reset_index().melt(
            id_vars="Company", var_name="Status", value_name="Assessments"
        )
        chartdata["Order"] = chartdata.Status.map({v: i for i, v in enumerate(STATES)})
        chart = (
            alt.Chart(chartdata)
            .mark_bar(cornerRadiusEnd=2)
            .encode(
                y=alt.Y(
                    "Company:N",
                    sort=list(top.index),
                    title=None,
                    axis=alt.Axis(labelLimit=160, labelOverlap=False),
                ),
                x=alt.X(
                    "Assessments:Q",
                    stack="zero",
                    axis=alt.Axis(tickMinStep=1, grid=True),
                ),
                color=alt.Color(
                    "Status:N",
                    scale=alt.Scale(domain=STATES, range=COLORS),
                    legend=alt.Legend(orient="bottom"),
                ),
                order=alt.Order("Order:Q"),
                tooltip=["Company", "Status", "Assessments"],
            )
        )
        st.altair_chart(chart.properties(height=28 * len(top) + 12), width="stretch")
        with st.expander("View all products and counts"):
            st.dataframe(counts, width="stretch")
    analysis = st.segmented_control(
        "Detailed analysis",
        ["Capability coverage", "Customer ratings", "Recent source checks"],
        default="Capability coverage",
        selection_mode="single",
    )
    if analysis == "Customer ratings":
        ratings = []
        for card in canonical:
            if card["name"] not in counts.index:
                continue
            scores = card["reviews"].get("ratings", {})
            if all(key in scores for key in ("G2", "Capterra")):
                ratings.append(
                    {
                        "Company": card["name"],
                        **{
                            key: float(scores[key].split("/")[0])
                            for key in ("G2", "Capterra")
                        },
                    }
                )
        st.caption(
            "Source-edition ratings on a five-point scale. Only products with numeric scores on both platforms are plotted."
        )
        if ratings:
            scatter = (
                alt.Chart(pd.DataFrame(ratings))
                .mark_circle(size=95, color="#087E83", opacity=0.75)
                .encode(
                    x=alt.X("G2:Q", scale=alt.Scale(domain=[0, 5])),
                    y=alt.Y("Capterra:Q", scale=alt.Scale(domain=[0, 5])),
                    tooltip=["Company:N", "G2:Q", "Capterra:Q"],
                )
            )
            st.altair_chart(scatter.properties(height=280), width="stretch")
            with st.expander("Rating details"):
                st.dataframe(ratings, hide_index=True)
        else:
            st.info("No products in this selection have both ratings.")
    elif analysis == "Recent source checks":
        init_monitor()
        latest = latest_checks()
        st.caption(
            "Latest observations across monitored sources, independent of the capability filter."
        )
        if latest:
            st.dataframe(
                [
                    {
                        "Product": r["company"],
                        "Checked": utc(r["ts"]),
                        "Result": r["outcome"],
                        "Source": r["url"],
                    }
                    for r in latest
                ],
                hide_index=True,
            )
        else:
            st.info("No source checks recorded yet.")
        if st.button("Open live monitor"):
            st.session_state.goto_monitor = True
            st.rerun()
    else:
        st.subheader("Capability coverage")
        st.caption(
            "Share marked supported among cards assessing each capability. Hover for the numerator and denominator."
        )
        by_cap = (
            selected.assign(yes=selected.Status.eq("Supported"))
            .groupby("Capability")
            .yes.agg(["sum", "count"])
            .reset_index()
        )
        by_cap["Share"] = by_cap["sum"] / by_cap["count"]
        bar = (
            alt.Chart(by_cap)
            .mark_bar(color="#315AA4")
            .encode(
                x=alt.X(
                    "Share:Q", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])
                ),
                y=alt.Y(
                    "Capability:N",
                    sort="-x",
                    title=None,
                    axis=alt.Axis(labelLimit=215, labelOverlap=False),
                ),
                tooltip=[
                    "Capability",
                    alt.Tooltip("sum:Q", title="Supported"),
                    alt.Tooltip("count:Q", title="Assessed"),
                    alt.Tooltip("Share:Q", format=".0%"),
                ],
            )
        )
        st.altair_chart(
            bar.properties(height=max(90, len(by_cap) * 22)), width="stretch"
        )
    company = st.selectbox("Explore a battlecard", sorted(c["name"] for c in canonical))
    if st.button("Open battlecard"):
        st.session_state.open_company = next(
            k for k, c in cards.items() if c["name"] == company and c.get("edition")
        )
        st.rerun()


def monitor():
    init_monitor()
    st.html(
        '<div class="sd-heading">Signal Desk <span>Product intelligence</span></div>'
    )
    st.title("Live source monitor")
    st.caption(
        "Track selected text on product and pricing pages, or the published version of a vendor SDK. Each check keeps its source and timestamp."
    )
    names = sorted(feeds())
    company = st.selectbox(
        "Product to monitor",
        names,
        index=names.index("Fivetran"),
        key="monitor_company",
    )
    auto = st.toggle("Check every 5 minutes while this view is open", value=False)

    @st.fragment(run_every="5m" if auto else None)
    def panel():
        pressed = st.button(
            "Refresh sources now", type="primary", icon=":material/refresh:"
        )
        if pressed or auto:
            with st.spinner("Checking vendor sources…"):
                refresh(company, pressed)
        rows = history(company)
        for src in feeds()[company]:
            checks = [r for r in rows if r["url"] == src["url"]]
            with st.container(border=True):
                st.subheader(src["label"])
                st.link_button("Open source", src["url"])
                if not checks:
                    st.caption("Awaiting first check.")
                    continue
                latest = checks[0]
                st.caption(f"{latest['outcome']} · Checked {utc(latest['ts'])}")
                if latest["outcome"] == "Unavailable":
                    st.warning(latest["summary"])
                good = [c for c in checks if c["digest"]]
                if not good:
                    older = last_success(src["url"])
                    if older:
                        good = [older]
                if good:
                    if good[0]["id"] != latest["id"]:
                        st.caption(
                            "Last successful observation · " + utc(good[0]["ts"])
                        )
                    st.text(good[0]["summary"])
                    if good[0]["outcome"] == "Changed" and len(good) > 1:
                        with st.expander("Previous observation"):
                            st.text(good[1]["summary"])
        if rows:
            st.subheader("Observation history")
            series = pd.DataFrame(
                [
                    {
                        "Checked (UTC)": datetime.fromtimestamp(r["ts"], timezone.utc),
                        "Outcome": r["outcome"],
                    }
                    for r in rows
                ]
            )
            trend = (
                alt.Chart(series)
                .mark_bar()
                .encode(
                    x=alt.X(
                        "Checked (UTC):T",
                        title="Check date (UTC)",
                        timeUnit="utcyearmonthdate",
                    ),
                    y=alt.Y(
                        "count():Q", title="Source checks", axis=alt.Axis(tickMinStep=1)
                    ),
                    color="Outcome:N",
                    tooltip=["Outcome:N", "count():Q"],
                )
            )
            st.altair_chart(trend.properties(height=155), width="stretch")
            st.dataframe(
                [
                    {
                        "Checked": utc(r["ts"]),
                        "Outcome": r["outcome"],
                        "Source": r["url"],
                    }
                    for r in rows[:30]
                ],
                hide_index=True,
                width="stretch",
            )
        st.caption(
            "Manual checks share a 30-second cooldown. A change means the tracked excerpt or version changed. Review the source before updating a battlecard."
        )

    panel()
