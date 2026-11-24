"""Browse, research, and contribute competitive battlecards in Streamlit."""

from datetime import date
from html import escape
import json
import streamlit as st

from product_monitor import feeds

from battlecards import (
    directory_entries,
    draft_from_directory,
    identity,
    library,
    matching,
    save_card,
    supplied,
    valid_link,
    wikipedia,
)

PRICES = ["Pricing model", "Structure", "Public pricing", "Predictability"]


@st.cache_data(ttl=86400, show_spinner=False)
def directory(provider):
    return directory_entries(provider)


@st.cache_data(ttl=3600, max_entries=128, show_spinner=False)
def lookup(query):
    matches, unavailable = [], []
    for provider in ("Integration directory", "YC directory", "Wikipedia"):
        try:
            results = (
                wikipedia(query)
                if provider == "Wikipedia"
                else matching(directory(provider), query)
            )
            matches.extend(
                (name, detail, url, provider)
                for name, detail, url in results
                if valid_link(url)
            )
        except (OSError, ValueError, KeyError):
            unavailable.append(provider)
    return matches, unavailable


def comparison(rows, company, column):
    if not rows:
        st.caption("No comparison recorded.")
        return

    def cell(value):
        return (
            escape(value)
            if value.strip()
            else '<span class="bc-missing">Not provided</span>'
        )

    header = "".join(
        f'<th scope="col">{escape(h)}</th>' for h in (column, company, "MADTECH.AI")
    )
    body = "".join(
        f'<tr><th scope="row">{cell(k)}</th><td>{cell(a)}</td><td>{cell(b)}</td></tr>'
        for k, a, b in rows
    )
    st.html(
        f'<div class="bc-table" tabindex="0" aria-label="{column} comparison"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'
    )


def points(heading, items):
    if items:
        entries = "".join(f"<li>{escape(item)}</li>" for item in items if item)
        st.html(
            f'<section class="bc-notes"><h3>{escape(heading)}</h3><ul>{entries}</ul></section>'
        )


def comparison_editor(label, rows, cols):
    data = [dict(zip(cols, row)) for row in rows]
    edited = st.data_editor(
        data,
        column_order=cols,
        column_config={k: st.column_config.TextColumn(k, max_chars=1200) for k in cols},
        num_rows="dynamic",
        hide_index=True,
        key=label,
        width="stretch",
    )
    return [
        [str(row.get(k) or "").strip() for k in cols]
        for row in edited
        if str(row.get(cols[0]) or "").strip() and any(row.get(k) for k in cols[1:])
    ]


@st.dialog("Company battlecard", width="large")
def editor(initial, gate, previous=None):
    gate()
    st.caption("Saved cards are visible to other workspace visitors.")
    with st.form("company_card_editor"):
        a, b = st.columns(2)
        name = a.text_input(
            "Company name",
            value=initial.get("name", ""),
            max_chars=100,
            disabled=bool(initial.get("name")),
        )
        source = b.text_input(
            "Source URL", value=initial.get("source", ""), max_chars=1000
        )
        overview = st.text_area(
            "Company overview", value=initial.get("overview", ""), max_chars=4000
        )
        headline = st.text_input(
            "Positioning against MADTECH.AI",
            value=initial.get("headline", ""),
            max_chars=500,
        )
        with st.expander("Capabilities and pricing", expanded=previous is None):
            st.caption("Enter one comparison per row. Leave unknown values blank.")
            features = comparison_editor(
                "Feature inputs",
                initial.get("features") or [["", "", ""]],
                ["Capability", "Company", "MADTECH.AI"],
            )
            pricing = comparison_editor(
                "Pricing inputs",
                initial.get("pricing") or [[k, "", ""] for k in PRICES],
                ["Parameter", "Company", "MADTECH.AI"],
            )
        with st.expander("Sales notes and customer feedback"):
            notes = {}
            for key, title in (
                ("challenges", "Competitor challenges"),
                ("difference", "MADTECH.AI differentiators"),
                ("value", "Pricing advantages"),
            ):
                notes[key] = st.text_area(
                    title, value="\n".join(initial.get(key, [])), max_chars=4000
                ).splitlines()
            reviews = initial.get("reviews", {})
            ratings = {}
            for site, col in zip(("G2", "Capterra"), st.columns(2)):
                old = reviews.get("ratings", {}).get(site)
                score = col.number_input(
                    f"{site} score out of 5",
                    min_value=0.0,
                    max_value=5.0,
                    step=0.1,
                    value=float(old) if old else None,
                )
                if score is not None:
                    ratings[site] = str(score)
            period = st.text_input(
                "Review period or checked date",
                value=reviews.get("period", ""),
                max_chars=100,
            )
            likes = st.text_area(
                "What customers like",
                value="\n".join(reviews.get("likes", [])),
                max_chars=4000,
            ).splitlines()
            concerns = st.text_area(
                "Customer concerns",
                value="\n".join(reviews.get("concerns", [])),
                max_chars=4000,
            ).splitlines()
        save = st.form_submit_button("Save battlecard", type="primary")
    if save:
        if not name.strip() or not overview.strip() or not valid_link(source.strip()):
            st.error(
                "Enter a company name, an overview, and an HTTP or HTTPS source URL."
            )
            return
        card = dict(
            name=name.strip(),
            overview=overview.strip(),
            headline=headline.strip(),
            source=source.strip(),
            origin="Added company",
            updated=str(date.today()),
            evidence=initial.get("evidence", {}),
            features=features,
            pricing=pricing,
            reviews=dict(
                likes=likes, concerns=concerns, ratings=ratings, period=period
            ),
            **notes,
        )
        try:
            save_card(card, previous)
        except ValueError as exc:
            st.error(str(exc))
            return
        key = identity(name)
        st.session_state.open_company = "analyst:" + key if key in supplied() else key
        st.rerun()


def company_search(cards, gate):
    st.subheader("Find a company")
    with st.form("search_companies"):
        query = st.text_input(
            "Company or product name", placeholder="e.g. Fivetran", max_chars=100
        )
        search = st.form_submit_button("Search directories", type="primary")
    if search and not query.strip():
        st.session_state.pop("company_hits", None)
        st.info("Enter a company name to search.")
    if search and query.strip():
        with st.spinner("Searching public company directories…"):
            st.session_state.company_hits = lookup(query.strip())
    if "company_hits" in st.session_state:
        hits, failed = st.session_state.company_hits
        if failed:
            st.caption("Unavailable sources · " + ", ".join(failed))
        if hits:
            selected = st.selectbox(
                "Directory matches",
                range(len(hits)),
                format_func=lambda i: f"{hits[i][0]} · {hits[i][3]}",
            )
            name, description, url, provider = hits[selected]
            st.text(description)
            st.link_button(f"Open {provider} source", url)
            if identity(name) in cards:
                if st.button("View existing battlecard"):
                    st.session_state.open_company = identity(name)
                    st.rerun()
            elif st.button("Add this company", type="primary"):
                with st.spinner("Checking available vendor documentation…"):
                    draft = draft_from_directory(name, description, url)
                editor(draft, gate)
        else:
            st.info("No matches returned. You can add a company using a source URL.")
    if st.button("Add a company manually"):
        editor({}, gate)


def open_search():
    st.session_state.workspace_view = "Find and add companies"


def positioning_map(card):
    if "map" not in card:
        return ""
    points = []
    legend = []
    for name, (speed, completeness) in card["map"].items():
        style = "mdi" if name == "MADTECH.AI" else "vendor"
        points.append(
            f'<i class="bc-dot {style}" style="left:{speed}%;bottom:{completeness}%"></i>'
        )
        legend.append(f'<span class="{style}">{escape(name)}</span>')
    return f"""<figure class="bc-map" aria-label="Qualitative positioning from the supplied card">
        <div class="bc-axis">Completeness ↑</div><div class="bc-plot">{"".join(points)}</div>
        <div class="bc-axis right">Speed to value →</div>
        <figcaption>{"".join(legend)}</figcaption></figure>"""


def battlecards(gate, view):
    cards = library()
    if view == "Find and add companies":
        company_search(cards, gate)
        return
    heading, action = st.columns([3, 1], vertical_alignment="center")
    heading.html(
        '<div class="sd-heading">Signal Desk <span>Competitive intelligence</span></div>'
    )
    action.button(
        "Find a company",
        icon=":material/search:",
        on_click=open_search,
        width="stretch",
    )
    selected = st.selectbox(
        f"Battlecard library · {len(cards)} companies",
        sorted(cards),
        format_func=lambda k: cards[k]["name"]
        + (" · Analyst card" if k.startswith("analyst:") else ""),
        key="card_selection",
    )
    card = cards[selected]
    name = card["name"]
    added = card["origin"] == "Added company"
    st.html(
        f"""<section class="bc-cover"><div><div class="sd-label">Competitive battlecard</div>
    <h1>{escape(name)} <span>vs</span> MADTECH.AI</h1>
    <p>{escape(card["headline"] or card["overview"])}</p></div>{positioning_map(card)}</section>"""
    )
    if added:
        st.caption(f"Analyst entry · Updated {card['updated']}")
        st.link_button("Open company source", card["source"])
    else:
        st.caption(
            f"Source: {card['source']} · Pricing, ratings and competitive claims reflect the supplied card."
        )
    overview, features, price, reviews, references = st.tabs(
        ["Brief", "Product", "Pricing", "Reviews", "Sources"]
    )
    with overview:
        if card["overview"]:
            st.subheader("Company overview")
            for paragraph in card["overview"].split("\n\n"):
                st.markdown(escape(paragraph), unsafe_allow_html=False)
        if card.get("insight"):
            st.info(card["insight"])
        a, b = st.columns(2)
        with a:
            points("Competitor challenges", card["challenges"])
        with b:
            points("MADTECH.AI differentiators", card["difference"])
        if card.get("testimonials"):
            with st.expander("MADTECH.AI customer testimonials"):
                for quote in card["testimonials"]:
                    st.text(quote)
    with features:
        section = (
            st.radio(
                "Product comparison",
                ["Capabilities", "Architecture"],
                horizontal=True,
                key=f"compare_{selected}",
            )
            if card.get("architecture")
            else "Capabilities"
        )
        matrix = card["features"] if section == "Capabilities" else card["architecture"]
        query = st.text_input(
            "Filter comparison rows",
            placeholder="e.g. warehouse",
            key=f"filter_{selected}",
        )
        filtered = [
            row for row in matrix if query.casefold() in " ".join(row).casefold()
        ]
        if matrix and not filtered:
            st.info("No comparison rows match your filter.")
        else:
            comparison(
                filtered,
                name,
                "Capability" if section == "Capabilities" else "Dimension",
            )
        if card.get("evidence"):
            with st.expander("Vendor documentation"):
                for label, fact in card["evidence"].items():
                    st.markdown(f"**{label}**")
                    st.text(fact["quote"])
                    st.caption(
                        f"[Vendor source]({fact['url']}) · Checked {fact['checked'][:10]}"
                    )
    with price:
        comparison(card["pricing"], name, "Parameter")
        if card.get("edition"):
            st.caption(
                "July 2026 card terms. The accompanying pricing audit flags potential user licences, cloud charges, setup fees and minimum terms. Confirm the current quote with the vendor."
            )
        points("MADTECH.AI responses", card["value"])
    with reviews:
        data = card["reviews"]
        if not any(data.get(k) for k in ("ratings", "likes", "concerns", "note")):
            st.caption("Customer feedback was not provided for this card.")
        if data.get("note"):
            st.caption(data["note"])
        if data.get("ratings"):
            st.caption(data["period"])
            columns = st.columns(len(data["ratings"]))
            for col, (site, rating) in zip(columns, data["ratings"].items()):
                score, _, count = rating.partition(" ")
                col.metric(site, score)
                if count:
                    col.caption(count.strip("()"))
        a, b = st.columns(2)
        with a:
            points("What users like", data.get("likes", []))
        with b:
            points("What users dislike", data.get("concerns", []))
        earlier = card.get("earlier_voice")
        if earlier:
            with st.expander("Earlier edition · customer feedback"):
                st.caption(f"{earlier['period']} · {earlier['source']}")
                points("What users liked", earlier["likes"])
                points("Customer concerns", earlier["concerns"])
        if card.get("review_position"):
            points("MADTECH.AI response", [card["review_position"]])
    with references:
        st.caption(card["source"])
        for ref in card.get("citations", []):
            if valid_link(ref["url"]):
                st.html(
                    f'<p class="sd-reference"><a href="{escape(ref["url"],quote=True)}" target="_blank" rel="noopener noreferrer">{escape(ref["label"])}</a></p>'
                )
        if not card.get("citations") and not added:
            st.caption(
                "No public URLs were listed for this product in the citation companion."
            )
        if name in feeds() and st.button("Monitor current vendor sources"):
            st.session_state.monitor_company = name
            st.session_state.goto_monitor = True
            st.rerun()
    if added:
        if st.button("Edit company data"):
            editor(card, gate, card)
        st.download_button(
            "Download card data",
            json.dumps(card, indent=2),
            file_name="battlecard.json",
            mime="application/json",
        )
