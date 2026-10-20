"""
mcp_server.py — MCP (Model Context Protocol) front-end for RiverScout.

Exposes the functions in river_scout_api.py as MCP tools over the
Streamable HTTP transport, so any MCP client (Gemini Enterprise, Claude,
MCP Inspector, ...) can call them. The Streamlit app (river_scout_chat.py)
is independent and unaffected.

Run locally:
    pip install -r requirements-mcp.txt
    python mcp_server.py            # serves http://localhost:8080/mcp

Endpoints:
    /mcp      MCP Streamable HTTP endpoint (point MCP clients here)
    /health   Plain 200 OK for load balancers / curl smoke tests
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import PlainTextResponse

import river_scout_api as api

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("river-scout-mcp")

# Cloud Run injects PORT; default matches the Dockerfile's EXPOSE.
PORT = int(os.environ.get("PORT", "8080"))

# stateless_http=True: every request stands alone, with no server-side session
# held in memory. Cloud Run may route consecutive requests to different
# container instances, so session-affine mode would randomly fail.
mcp = FastMCP(
    "RiverScout",
    host="0.0.0.0",  # bind all interfaces so the container port is reachable
    port=PORT,
    stateless_http=True,
)

# Every tool is a read-only lookup against public APIs (USGS, NWS, Open-Meteo).
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,  # talks to external services
)


async def _call(fn, *args) -> str:
    """Run a blocking river_scout_api function off the event loop; return JSON.

    The api functions use `requests` and ThreadPoolExecutor, so they block.
    Running them in a worker thread keeps the server responsive to other
    clients while a slow USGS/NWS call is in flight.
    """
    try:
        result = await asyncio.to_thread(fn, *args)
        return json.dumps(result, default=str)
    except Exception as e:  # api functions promise not to raise; belt and braces
        logger.exception("Tool %s failed", getattr(fn, "__name__", fn))
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def _canonical(river_id: str) -> str:
    """Resolve an alias ('eb pemi') to a canonical id; pass unknowns through
    so the api layer reports its normal error dict for them."""
    return api.resolve_river_id(river_id) or river_id


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


@mcp.tool(annotations=_READ_ONLY)
async def list_rivers() -> str:
    """List all configured New Hampshire river sections.

    Returns canonical river_id, river name, section description, USGS site ID,
    whether the gauge is a correlation gauge (proxy from a different river),
    dam control status, and American Whitewater URL. Call this first to
    discover valid river_id values.
    """
    return await _call(api.list_rivers)


@mcp.tool(annotations=_READ_ONLY)
async def resolve_river_id(query: str) -> str:
    """Map a fuzzy river name or common alias to a canonical river_id.

    Returns {"river_id": null, "found": false} if no match. Useful for
    natural-language input like 'freight train', 'ayers island', 'eb pemi',
    'wild ammo lower', or 'croydon'.

    Args:
        query: River name, section name, or alias to look up.
    """
    def _resolve(q: str) -> dict:
        rid = api.resolve_river_id(q)
        return {"river_id": rid, "found": rid is not None}

    return await _call(_resolve, query)


@mcp.tool(annotations=_READ_ONLY)
async def get_all_conditions(include_history: bool = False) -> str:
    """Fetch current gauge and weather conditions for ALL tracked NH rivers.

    All sections are fetched concurrently and returned sorted by river name.
    Use this for "what's running right now?" overview questions. For one
    river, prefer get_river_conditions.

    Args:
        include_history: If true, keep the 24-hour stage/flow history and
            7-day forecast arrays. Defaults to false for a compact response.
    """
    return await _call(api.get_all_conditions, include_history)


@mcp.tool(annotations=_READ_ONLY)
async def get_river_conditions(river_id: str) -> str:
    """Fetch current gauge AND weather data for one NH whitewater river section.

    Returns stage (ft), flow (CFS), 24-hour history, trend
    (rising/falling/steady), runnability within the configured range, current
    weather, hourly forecast, and 24-hour precipitation totals. This is the
    primary single-river tool; it fetches gauge and weather concurrently.

    Args:
        river_id: Canonical river_id (e.g. 'pemi_bristol_above') or a common
            alias (e.g. 'ayers island', 'contoo', 'eb pemi'). Call list_rivers
            to see all options.
    """
    return await _call(api.get_river_conditions, _canonical(river_id))


@mcp.tool(annotations=_READ_ONLY)
async def get_gauge_data(river_id: str) -> str:
    """Fetch only gauge data for a river section (stage, flow, history, trend).

    No weather included, so it is faster than get_river_conditions.
    gauge_status values: 'ok'; 'ice' (safety-critical, reading unreliable);
    'stale' (reading older than 3 hours); 'error' (equipment fault or HTTP
    failure); 'no_data'.

    Args:
        river_id: Canonical river_id or alias.
    """
    return await _call(api.get_gauge_data, _canonical(river_id))


@mcp.tool(annotations=_READ_ONLY)
async def get_weather(river_id: str) -> str:
    """Fetch weather and precipitation data for a river's put-in coordinates.

    Sources: NWS (current conditions + hourly forecast) and Open-Meteo
    (observed precipitation for the past 24 hours + 7-day daily forecast).
    Use for multi-day outlooks or upcoming rain. The two sources fail
    independently.

    Args:
        river_id: Canonical river_id or alias.
    """
    return await _call(api.get_weather, _canonical(river_id))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
