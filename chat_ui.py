import os

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from battlecards import library, valid_link
from desk_chat import EXAMPLES, answer, synthesize


def setting(name):
    try:
        return st.secrets.get(name) or os.environ.get(name)
    except StreamlitSecretNotFoundError:
        return os.environ.get(name)


def render_turn(turn, index):
    with st.chat_message("user"):
        st.text(turn["question"])
    with st.chat_message("assistant"):
        st.text(turn["text"])
        if turn.get("notice"):
            st.caption(turn["notice"])
        if turn["sources"]:
            if not turn.get("generated"):
                for number, source in enumerate(turn["sources"], 1):
                    st.caption(f"{source['company']} · {source['label']} [{number}]")
                    st.text(source["text"])
            with st.expander("Citations and battlecards"):
                evidence(turn["sources"], index, show_text=turn.get("generated", False))


def evidence(sources, index, show_text):
    for number, source in enumerate(sources, 1):
        with st.container(border=True):
            st.caption(f"[{number}]  {source['company']}  ·  {source['label']}")
            if show_text:
                st.text(source["text"])
            st.caption(" · ".join(filter(None, [source["source"], source["date"]])))
            if source["key"]:
                if st.button("Open battlecard", key=f"chat_card_{index}_{number}"):
                    st.session_state.open_company = source["key"]
                    st.rerun()
            elif valid_link(source["url"]):
                st.link_button("Open monitored source", source["url"])


def chat():
    st.html('<div class="sd-heading">Signal Desk <span>Research chat</span></div>')
    st.title("Ask Signal Desk")
    st.caption(
        "Ask about pricing, capabilities, ratings, or the latest saved source checks. Follow up in the same conversation."
    )
    turns = st.session_state.setdefault("research_chat", [])
    if turns and st.button("New conversation", icon=":material/add:"):
        st.session_state.research_chat = []
        st.rerun()
    api_key = setting("SIGNAL_CHAT_API_KEY")
    generate = False
    if api_key:
        generate = st.toggle(
            "Summarize with AI",
            help="Sends your question, recent questions and selected source excerpts to OpenAI.",
        )
    question = None
    if not turns:
        for example in EXAMPLES:
            if st.button(example):
                question = example
    for index, turn in enumerate(turns):
        render_turn(turn, index)
    typed = st.chat_input("Ask a question about the competition", max_chars=1200)
    question = (typed or question or "").strip()
    if not question:
        return
    with st.spinner("Looking up evidence…"):
        response = answer(question, library(), turns[-1]["context"] if turns else None)
        if generate and response["sources"]:
            try:
                response["text"] = synthesize(
                    question,
                    response["sources"],
                    [t["question"] for t in turns],
                    api_key,
                    setting("SIGNAL_CHAT_MODEL") or "gpt-4o-mini",
                )
                response["generated"] = True
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                response["notice"] = (
                    "AI summary unavailable. The matching source excerpts are shown below."
                )
        response["question"] = question
        st.session_state.research_chat = (turns + [response])[-20:]
    st.rerun()
