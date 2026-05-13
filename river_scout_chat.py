#!/usr/bin/env python3
"""
river_scout_chat.py — RiverScout public Streamlit chatbot.

Wraps river_scout_api.py with an Anthropic API agentic loop + streaming UI.
No MCP server needed: tools are defined directly in Anthropic API format
and dispatched in Python.

Deploy: Streamlit Community Cloud (share.streamlit.io)
Secrets: ANTHROPIC_API_KEY in .streamlit/secrets.toml
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from typing import Generator

import streamlit as st
import anthropic

from river_scout_api import (
    get_all_conditions,
    get_gauge_data,
    get_river_conditions,
    get_weather,
    list_rivers,
    resolve_river_id,
)

# ── Config ────────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW = 3600  # seconds

# ── Tool definitions (Anthropic API format: input_schema, not inputSchema) ────
# Descriptions copied verbatim from river_scout_mcp_server.py TOOLS manifest.

TOOLS = [
    {
        "name": "get_river_conditions",
        "description": (
            "Fetch current gauge and weather data for a New Hampshire whitewater river section. "
            "Returns stage (ft), flow (CFS), 24-hour stage history, trend (rising/falling/steady), "
            "runnability within the configured range, current weather, hourly forecast, and "
            "24-hour precipitation totals. This is the primary tool — prefer it over calling "
            "get_gauge_data and get_weather separately since it fetches both concurrently. "
            "Accepts canonical IDs (e.g. 'pemi_bristol_above') or common aliases "
            "(e.g. 'ayers island', 'east branch', 'freight train', 'wild ammo'). "
            "Call list_rivers to discover all valid IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "river_id": {
                    "type": "string",
                    "description": (
                        "Canonical river_id (e.g. 'pemi_bristol_above') or a common alias "
                        "(e.g. 'ayers island', 'contoo', 'eb pemi'). Aliases are resolved "
                        "automatically. Use list_rivers to see all options."
                    ),
                }
            },
            "required": ["river_id"],
        },
    },
    {
        "name": "get_gauge_data",
        "description": (
            "Fetch only gauge data for a river section (stage, flow, 24hr history, trend). "
            "No weather or precipitation included. Use this when you only need gauge readings "
            "and want a faster response. "
            "gauge_status values: 'ok', 'ice' (safety-critical — reading unreliable), "
            "'stale' (reading >3hr old), 'error' (equipment fault or HTTP failure), 'no_data'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "river_id": {
                    "type": "string",
                    "description": "Canonical river_id or alias.",
                }
            },
            "required": ["river_id"],
        },
    },
    {
        "name": "get_weather",
        "description": (
            "Fetch weather and precipitation data for a river's put-in coordinates. "
            "Sources: NWS (current conditions + 24hr hourly forecast) and Open-Meteo "
            "(observed precipitation for the past 24 hours). The two sources fail independently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "river_id": {
                    "type": "string",
                    "description": "Canonical river_id or alias.",
                }
            },
            "required": ["river_id"],
        },
    },
    {
        "name": "list_rivers",
        "description": (
            "List all configured New Hampshire river sections. Returns canonical river_id, "
            "river name, section description, USGS site ID, whether the gauge is a correlation "
            "gauge (proxy from a different river), dam control status, and AW URL. "
            "Call this first to discover valid river_id values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "resolve_river_id",
        "description": (
            "Map a fuzzy river name or common alias to a canonical river_id. "
            "Returns null if no match is found. Useful for natural-language inputs like "
            "'freight train', 'ayers island', 'eb pemi', 'wild ammo lower', 'croydon'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "River name, section name, or alias to look up.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_all_conditions",
        "description": (
            "Fetch current gauge and weather conditions for ALL 16 NH river sections "
            "concurrently. Returns a compact summary (no 24hr history) by default — "
            "use get_river_conditions for a single river's full detail including history. "
            "Ideal for overview reports and comparisons across rivers. "
            "Results are sorted alphabetically by river name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "include_history": {
                    "type": "boolean",
                    "description": (
                        "Include 24hr stage/flow history and hourly forecast arrays. "
                        "Default false (omitted for compact summary view)."
                    ),
                }
            },
            "required": [],
        },
    },
]

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are RiverScout, an assistant for New Hampshire whitewater paddlers. "
    "Use the available tools to answer questions about river conditions, gauge data, and weather.\n\n"
    "When the user asks what rivers are running, wants an overview, or asks about general conditions — "
    "always call get_all_conditions and format results into clear sections: runnable rivers first, "
    "then watch list (close to runnable range), then not running. "
    "For a specific river, use get_river_conditions.\n\n"
    "Safety rules:\n"
    "- If gauge_status is 'ice', warn the user explicitly — this means the sensor may be frozen "
    "and readings are unreliable, which is a safety-critical flag.\n"
    "- If gauge_status is 'stale', note that data is >3 hours old and may not reflect conditions.\n"
    "- Always remind users that this tool is for trip planning only and conditions must be "
    "verified in person before paddling.\n\n"
    "Keep responses focused on NH whitewater river conditions. "
    "Politely decline unrelated requests and redirect to what you can help with."
)

# ── Rate limiting (module-level dict, shared within a single server process) ──
# Per-session sliding window. Users who refresh get a new session_id, which is
# an acceptable trade-off for a personal/demo app.

_rate_limits: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(session_id: str) -> bool:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_limits[session_id] = [t for t in _rate_limits[session_id] if t > cutoff]
    if len(_rate_limits[session_id]) >= RATE_LIMIT_REQUESTS:
        return False
    _rate_limits[session_id].append(now)
    return True


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _dispatch_tool(name: str, tool_input: dict) -> str:
    if name == "get_all_conditions":
        result = get_all_conditions(tool_input.get("include_history", False))
    elif name == "get_river_conditions":
        result = get_river_conditions(tool_input["river_id"])
    elif name == "get_gauge_data":
        result = get_gauge_data(tool_input["river_id"])
    elif name == "get_weather":
        result = get_weather(tool_input["river_id"])
    elif name == "list_rivers":
        result = list_rivers()
    elif name == "resolve_river_id":
        resolved = resolve_river_id(tool_input["query"])
        result = {"river_id": resolved, "found": resolved is not None}
    else:
        result = {"error": f"Unknown tool: {name!r}"}
    return json.dumps(result)


# ── Streaming agentic loop ────────────────────────────────────────────────────

def response_stream(messages: list[dict]) -> Generator[str, None, None]:
    """
    Generator that yields text chunks for st.write_stream().

    Handles multi-round tool use internally by mutating `messages` in place
    (appending assistant + tool_result turns). The generator exits when
    stop_reason is not "tool_use" (i.e., Claude is done calling tools).

    The "Fetching river data..." status line is yielded during tool execution
    so the user always sees progress rather than silence.
    """
    client: anthropic.Anthropic = st.session_state.client

    while True:
        with client.messages.stream(
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            max_tokens=MAX_TOKENS,
        ) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()

        # Append this turn to history (SDK ContentBlock objects accepted by the API)
        messages.append({"role": "assistant", "content": final.content})

        if final.stop_reason != "tool_use":
            break

        yield "\n\n*Fetching river data...*\n\n"

        tool_results = []
        for block in final.content:
            if block.type == "tool_use":
                result = _dispatch_tool(block.name, dict(block.input))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})


# ── History rendering ─────────────────────────────────────────────────────────

def _extract_text(content) -> str:
    """Extract displayable text from message content (str, list of SDK objects, or dicts)."""
    if isinstance(content, str):
        return content
    texts = []
    for block in content:
        if hasattr(block, "type"):  # Anthropic SDK ContentBlock object
            if block.type == "text":
                texts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))
    return "".join(texts)


def render_history(messages: list[dict]) -> None:
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        # Skip tool_result turns — these are API plumbing, not user-visible text
        if isinstance(content, list) and content and isinstance(content[0], dict):
            if content[0].get("type") == "tool_result":
                continue

        text = _extract_text(content)
        if text:
            with st.chat_message(role):
                st.markdown(text)


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="RiverScout — NH Whitewater Conditions",
    page_icon="🛶",
    layout="centered",
)

st.title("🛶 RiverScout")
st.caption("NH whitewater conditions — for trip planning only, always assess conditions in person")

with st.sidebar:
    st.header("About")
    st.write(
        "Real-time gauge data (USGS) and weather (NWS) "
        "for 16 New Hampshire whitewater river sections."
    )
    st.divider()
    st.subheader("Try asking:")
    st.markdown(
        "- *What rivers are running right now?*\n"
        "- *How's the Pemi East Branch looking?*\n"
        "- *Check freight train conditions*\n"
        "- *What's the weather at Wild Ammo?*\n"
        "- *Show me all available rivers*"
    )
    st.divider()
    st.subheader("Supported Rivers")
    river_list = sorted(list_rivers(), key=lambda r: r["river_name"])
    st.markdown("\n".join(f"- {r['river_name']}" for r in river_list))
    st.divider()
    st.caption("Rate limit: 20 requests / hour per session.")

# ── Session state ─────────────────────────────────────────────────────────────

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

if "client" not in st.session_state:
    st.session_state.client = anthropic.Anthropic(
        api_key=st.secrets["ANTHROPIC_API_KEY"]
    )

# ── Render conversation history ───────────────────────────────────────────────

render_history(st.session_state.messages)

# Show a welcome prompt if this is a fresh session
if not st.session_state.messages:
    with st.chat_message("assistant"):
        st.markdown(
            "Hey! Ask me what rivers are running, or check conditions for a specific section. "
            "Try *'What's running right now?'* to get a full overview."
        )

# ── Handle new user input ─────────────────────────────────────────────────────

if prompt := st.chat_input("Ask about NH river conditions..."):
    if not check_rate_limit(st.session_state.session_id):
        st.warning(
            "You've reached the rate limit (20 requests / hour). Please try again later."
        )
        st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # response_stream mutates st.session_state.messages in place as it runs,
        # appending tool-use and tool-result turns for the API conversation history.
        st.write_stream(response_stream(st.session_state.messages))
