"""
river_scout_api.py — Real-time river condition data fetcher for NH whitewater.

Fetches gauge data from USGS Instantaneous Values API, weather from the NWS
API, and precipitation history from Open-Meteo. Returns structured, unfiltered
dicts — including error states and ice flags — for downstream LLM consumption.

No exceptions propagate to the caller; all failure modes surface via status
and error fields in the returned dicts.

APIs used:
  - USGS IV:     https://waterservices.usgs.gov/nwis/iv/ (no key)
  - NWS:         https://api.weather.gov/ (no key; User-Agent required)
  - Open-Meteo:  https://api.open-meteo.com/v1/forecast (no key, no auth)
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests


# ── Module-level constants ────────────────────────────────────────────────────

USER_AGENT = "RiverScout/1.0 (maxwell.m.auger@gmail.com)"
TIMEOUT = 10  # seconds — applied to every HTTP call

_NWS_GRID_CACHE: dict[tuple[float, float], dict] = {}

_WIND_DIRS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


# ── River configuration ───────────────────────────────────────────────────────

RIVER_CONFIG: dict[str, dict] = {
    "ammo_lower": {
        "river_name": "Ammonoosuc",
        "section": "Lower (Pierce Bridge to NH 116)",
        "usgs_site_id": "01137500",
        "run_min_ft": 3.1, "run_max_ft": 6.0,
        "run_min_cfs": 350, "run_max_cfs": 1800,
        "lat": 44.27205, "lon": -71.63078,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Natural flow - direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/3689/main",
    },
    "ammo_upper": {
        "river_name": "Ammonoosuc",
        "section": "Upper (Bretton Woods to Twin Mountain)",
        "usgs_site_id": "01137500",
        "run_min_ft": 2.0, "run_max_ft": 7.0,
        "run_min_cfs": 120, "run_max_cfs": 2800,
        "lat": 44.27205, "lon": -71.63078,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Natural flow - direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1154/main",
    },
    "ash_lower": {
        "river_name": "Ashuelot",
        "section": "Lower (Ashuelot to Hinsdale)",
        "usgs_site_id": "01161000",
        "run_min_ft": 4.5, "run_max_ft": 7.0,
        "run_min_cfs": 650, "run_max_cfs": 3500,
        "lat": 42.77655, "lon": -72.42335,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Natural flow - direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1158/main",
    },
    "contoo": {
        "river_name": "Contoocook",
        "section": "Freight Train",
        "usgs_site_id": "01085000",
        "run_min_ft": 6.0, "run_max_ft": 14.9,
        "run_min_cfs": 150, "run_max_cfs": 15000,
        "lat": 43.14352, "lon": -71.87004,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Natural flow - direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1167/main",
    },
    "croyden_brook": {
        "river_name": "Croyden Brook (North Branch of the Sugar)",
        "section": "Newport",
        "usgs_site_id": "01152500",
        "run_min_ft": 3.0, "run_max_ft": 6.5,
        "run_min_cfs": 300, "run_max_cfs": 1200,
        "lat": 43.43288, "lon": -72.16062,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Natural flow - direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/4013/main",
    },
    "pemi_bristol_above": {
        "river_name": "Pemigewasset - Bristol (Ayers Island)",
        "section": "Plymouth Gauge — Inflow to dam above Ayers Island",
        "usgs_site_id": "01076500",
        "run_min_ft": 0.9, "run_max_ft": 9.3,
        "run_min_cfs": 300, "run_max_cfs": 12000,
        "lat": 43.59855, "lon": -71.72011,
        "is_correlation_gauge": False,
        "dam_controlled": True,
        "notes": (
            "Dam controlled. This gauge reads INFLOW to the dam above Ayers Island. "
            "If rising: the dam may release soon. If dropping: the dam may cut the release early. "
            "Always check trend direction, not just current reading."
        ),
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1178/main",
    },
    "pemi_eb": {
        "river_name": "Pemigewasset, East Branch",
        "section": "Franconia Falls to Woodstock",
        "usgs_site_id": "01074520",
        "run_min_ft": 4.52, "run_max_ft": 8.09,
        "run_min_cfs": 350, "run_max_cfs": 1800,
        "lat": 44.06400, "lon": -71.58799,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": (
            "Natural flow - direct read. If it looks like there is enough water, there is probably too much. "
            "4.52 ft is the bare minimum; optimal medium flows are 5.5–6.5 ft."
        ),
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1179/main",
    },
    "swift_upper": {
        "river_name": "Swift River",
        "section": "Upper (Bear Notch Road to Rocky Gorge)",
        "usgs_site_id": "01074520",
        "run_min_ft": 5.25, "run_max_ft": 5.8,
        "run_min_cfs": 550, "run_max_cfs": 800,
        "lat": 43.99683, "lon": -71.32613,
        "is_correlation_gauge": True,
        "dam_controlled": False,
        "notes": (
            "Correlation gauge referencing East Branch of the Pemi at Lincoln (USGS 01074520). "
            "The Swift Upper does not have its own gauge — runnable range is calibrated against the EB Pemi reading."
        ),
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1189/main",
    },
    "swift_middle": {
        "river_name": "Swift River",
        "section": "Middle (Rocky Gorge to Lower Falls)",
        "usgs_site_id": "01074520",
        "run_min_ft": 5.35, "run_max_ft": 5.9,
        "run_min_cfs": 600, "run_max_cfs": 850,
        "lat": 44.00387, "lon": -71.27741,
        "is_correlation_gauge": True,
        "dam_controlled": False,
        "notes": (
            "Correlation gauge referencing East Branch of the Pemi at Lincoln (USGS 01074520). "
            "The Swift Middle does not have its own gauge — runnable range is calibrated against the EB Pemi reading."
        ),
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1191/main",
    },
    "bear": {
        "river_name": "Bearcamp River",
        "section": "Bennett Corner to Whittier",
        "usgs_site_id": "01064801",
        "run_min_ft": 4.8, "run_max_ft": 6.6,
        "run_min_cfs": 280, "run_max_cfs": 1400,
        "lat": 43.83016, "lon": -71.32867,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Natural flow - direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1162/main",
    },
    "the_warner": {
        "river_name": "Warner River",
        "section": "Melvin Mills to Warner",
        "usgs_site_id": "01086000",
        "run_min_ft": 4.7, "run_max_ft": 7.5,
        "run_min_cfs": 250, "run_max_cfs": 1800,
        "lat": 43.26720, "lon": -71.91861,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Natural flow - direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/3603/main",
    },
    "wild_ammo_upper": {
        "river_name": "Wild Ammonoosuc",
        "section": "Upper (Picnic Area to Wildwood Water Supply Dam)",
        "usgs_site_id": "01137500",
        "run_min_ft": 6.5, "run_max_ft": 9.5,
        "run_min_cfs": 2200, "run_max_cfs": 6000,
        "lat": 44.10797, "lon": -71.89833,
        "is_correlation_gauge": True,
        "dam_controlled": False,
        "notes": (
            "Correlation gauge referencing the Ammonoosuc at Bethlehem (USGS 01137500). "
            "The Wild Ammo is flashy — it rises and drops fast. Upper section needs more water than the lower. "
            "Catch it on a rising 7 ft at the Bethlehem gauge."
        ),
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/3737/flow",
    },
    "wild_ammo_lower": {
        "river_name": "Wild Ammonoosuc",
        "section": "Lower (Wildwood Water Supply Dam to Route 302)",
        "usgs_site_id": "01137500",
        "run_min_ft": 5.5, "run_max_ft": 8.5,
        "run_min_cfs": 2200, "run_max_cfs": 6000,
        "lat": 44.10549, "lon": -71.86790,
        "is_correlation_gauge": True,
        "dam_controlled": False,
        "notes": (
            "Correlation gauge referencing the Ammonoosuc at Bethlehem (USGS 01137500). "
            "If the Bethlehem gauge is rising past 6 ft — Go Now. "
            "If you missed the spike, try to catch it falling from 8 ft. "
            "Below 6 ft: bring your rock boat."
        ),
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1193/main",
    },
    "winni_lower": {
        "river_name": "Winnipesaukee River",
        "section": "Lower (Cross Mill Bridge Road to Franklin)",
        "usgs_site_id": "01081000",
        "run_min_ft": 3.0, "run_max_ft": 7.0,
        "run_min_cfs": 600, "run_max_cfs": 3020,
        "lat": 43.44302, "lon": -71.62168,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/3057/main",
    },
    "sugar": {
        "river_name": "Sugar River",
        "section": "North Newport to Rt 103",
        "usgs_site_id": "01152500",
        "run_min_ft": 2.0, "run_max_ft": 5.0,
        "run_min_cfs": 350, "run_max_cfs": 1600,
        "lat": 43.39105, "lon": -72.19534,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": "Natural flow - direct read.",
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1187/main",
    },
    "wonalancet_tamworth": {
        "river_name": "Wonalancet Brook",
        "section": "Route 113A to Tamworth",
        "usgs_site_id": "01064801",
        "run_min_ft": 4.7, "run_max_ft": 6.0,
        "run_min_cfs": 250, "run_max_cfs": 900,
        "lat": 43.90850, "lon": -71.35100,
        "is_correlation_gauge": True,
        "dam_controlled": False,
        "notes": (
            "Correlation gauge referencing the Bearcamp at South Tamworth (USGS 01064801). "
            "4.7–4.9 ft (bridge clearance ~0.5 ft) = minimum runnable / scraping. "
            "5.0+ ft (bridge clearance ~1.0+ ft) = good paddling."
        ),
        "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/1194/main",
    },
}


ALIASES: dict[str, str] = {
    # Pemi Bristol / Ayers Island
    "pemi bristol": "pemi_bristol_above",
    "ayers island": "pemi_bristol_above",
    "bristol": "pemi_bristol_above",
    "pemi plymouth": "pemi_bristol_above",

    # East Branch Pemi
    "east branch": "pemi_eb",
    "eb pemi": "pemi_eb",
    "pemi eb": "pemi_eb",
    "franconia falls": "pemi_eb",

    # Swift River
    "swift upper": "swift_upper",
    "swift middle": "swift_middle",
    "bear notch": "swift_upper",
    "rocky gorge": "swift_middle",

    # Ammonoosuc
    "ammo lower": "ammo_lower",
    "ammo upper": "ammo_upper",
    "lower ammo": "ammo_lower",
    "upper ammo": "ammo_upper",
    "ammonoosuc lower": "ammo_lower",
    "ammonoosuc upper": "ammo_upper",

    # Wild Ammonoosuc
    "wild ammo upper": "wild_ammo_upper",
    "wild ammo lower": "wild_ammo_lower",
    "wild ammo": "wild_ammo_lower",

    # Others
    "contoocook": "contoo",
    "freight train": "contoo",
    "bearcamp": "bear",
    "bear camp": "bear",
    "warner": "the_warner",
    "winnipesaukee": "winni_lower",
    "winni": "winni_lower",
    "sugar river": "sugar",
    "croyden": "croyden_brook",
    "croydon": "croyden_brook",
    "wonalancet": "wonalancet_tamworth",
    "ashuelot": "ash_lower",
    "lower ash": "ash_lower",
}


# ── Private helpers ───────────────────────────────────────────────────────────

def _parse_usgs_dt(s: str) -> datetime:
    """Parse a USGS dateTime string (may include milliseconds) to a UTC datetime."""
    s = re.sub(r"\.\d+", "", s)  # strip ".000" sub-second component
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _degrees_to_cardinal(degrees: float) -> str:
    """Convert a wind direction in degrees to a 16-point compass string."""
    idx = int((degrees + 11.25) / 22.5) % 16
    return _WIND_DIRS[idx]


def _safe_float(value: object) -> float | None:
    """Cast a USGS value field to float, returning None for nulls and sentinels."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if f == -999999.0 else f
    except (TypeError, ValueError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_river_id(query: str) -> str | None:
    """Map a user-facing river name or alias to a canonical river_id.

    Normalizes the input (lowercase, punctuation stripped) and checks against
    RIVER_CONFIG keys and the ALIASES dict. Returns None if no match is found.
    """
    q = query.strip()
    if q in RIVER_CONFIG:
        return q

    # Normalize: lowercase, convert hyphens/underscores to spaces, strip punctuation
    normalized = q.lower()
    normalized = re.sub(r"[-_]", " ", normalized)
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    # Match against RIVER_CONFIG key slugs (underscores → spaces)
    for key in RIVER_CONFIG:
        if key.replace("_", " ") == normalized:
            return key

    return ALIASES.get(normalized)


def list_rivers() -> list[dict]:
    """Return the full routing table as a list of dicts, one per river section."""
    return [
        {
            "river_id": river_id,
            "river_name": cfg["river_name"],
            "section": cfg["section"],
            "usgs_site_id": cfg["usgs_site_id"],
            "is_correlation_gauge": cfg.get("is_correlation_gauge", False),
            "dam_controlled": cfg.get("dam_controlled", False),
            "aw_url": cfg.get("aw_url"),
        }
        for river_id, cfg in RIVER_CONFIG.items()
    ]


def get_gauge_data(river_id: str) -> dict:
    """Fetch current and 24-hour gauge data for the given river section.

    Pulls stage (parameterCd 00065) and flow (00060) from the USGS Instantaneous
    Values API using a single P1D request. Computes trend from the 3-hour delta.

    Returns a dict with all required keys; never raises exceptions.
    """
    fetch_ts = datetime.now(timezone.utc).isoformat()

    base: dict = {
        "river_id": river_id,
        "river_name": None,
        "section": None,
        "usgs_site_id": None,
        "gauge_status": "error",
        "stage_ft": None,
        "flow_cfs": None,
        "stage_qualifier": None,
        "flow_qualifier": None,
        "trend": None,
        "stage_history_24hr": [],
        "flow_history_24hr": [],
        "run_min_ft": None,
        "run_max_ft": None,
        "run_min_cfs": None,
        "run_max_cfs": None,
        "is_runnable": None,
        "is_correlation_gauge": False,
        "dam_controlled": False,
        "notes": None,
        "aw_url": None,
        "timestamp_utc": None,
        "fetch_timestamp_utc": fetch_ts,
        "error_detail": None,
    }

    if river_id not in RIVER_CONFIG:
        base["error_detail"] = f"Unknown river_id: {river_id!r}"
        return base

    cfg = RIVER_CONFIG[river_id]
    base.update({
        "river_name": cfg["river_name"],
        "section": cfg["section"],
        "usgs_site_id": cfg["usgs_site_id"],
        "run_min_ft": cfg.get("run_min_ft"),
        "run_max_ft": cfg.get("run_max_ft"),
        "run_min_cfs": cfg.get("run_min_cfs"),
        "run_max_cfs": cfg.get("run_max_cfs"),
        "is_correlation_gauge": cfg.get("is_correlation_gauge", False),
        "dam_controlled": cfg.get("dam_controlled", False),
        "notes": cfg.get("notes"),
        "aw_url": cfg.get("aw_url"),
    })

    site_id = cfg["usgs_site_id"]
    url = (
        "https://waterservices.usgs.gov/nwis/iv/"
        f"?format=json&sites={site_id}&parameterCd=00065,00060&period=P1D&siteStatus=all"
    )

    try:
        resp = requests.get(url, timeout=TIMEOUT)
    except Exception as exc:
        base["error_detail"] = f"Request failed: {exc}"
        return base

    if resp.status_code != 200:
        base["error_detail"] = f"HTTP {resp.status_code}: {resp.text[:500]}"
        return base

    try:
        data = resp.json()
    except Exception as exc:
        base["error_detail"] = f"JSON parse error: {exc}"
        return base

    time_series = data.get("value", {}).get("timeSeries", [])

    def _find_ts(param_code: str) -> dict | None:
        return next(
            (
                ts for ts in time_series
                if ts.get("variable", {})
                   .get("variableCode", [{}])[0]
                   .get("value") == param_code
            ),
            None,
        )

    stage_ts = _find_ts("00065")
    flow_ts = _find_ts("00060")

    if stage_ts is None and flow_ts is None:
        base["gauge_status"] = "no_data"
        return base

    def _parse_readings(ts_obj: dict, value_key: str) -> list[dict]:
        readings = []
        raw_values = ts_obj.get("values", [{}])[0].get("value", [])
        for reading in raw_values:
            raw_dt = reading.get("dateTime", "")
            qualifiers = reading.get("qualifiers", [])
            qualifier_str = ", ".join(qualifiers) if qualifiers else None

            try:
                ts_utc_str = _parse_usgs_dt(raw_dt).isoformat()
            except Exception:
                ts_utc_str = raw_dt

            readings.append({
                "timestamp_utc": ts_utc_str,
                value_key: _safe_float(reading.get("value")),
                "qualifier": qualifier_str,
            })
        return readings

    stage_history = _parse_readings(stage_ts, "stage_ft") if stage_ts else []
    flow_history = _parse_readings(flow_ts, "flow_cfs") if flow_ts else []

    base["stage_history_24hr"] = stage_history
    base["flow_history_24hr"] = flow_history

    # Most recent readings
    last_stage = stage_history[-1] if stage_history else {}
    last_flow = flow_history[-1] if flow_history else {}

    stage_ft: float | None = last_stage.get("stage_ft")
    stage_qualifier: str | None = last_stage.get("qualifier")
    stage_ts_utc: str | None = last_stage.get("timestamp_utc")

    flow_cfs: float | None = last_flow.get("flow_cfs")
    flow_qualifier: str | None = last_flow.get("qualifier")

    base["stage_ft"] = stage_ft
    base["flow_cfs"] = flow_cfs
    base["stage_qualifier"] = stage_qualifier
    base["flow_qualifier"] = flow_qualifier
    base["timestamp_utc"] = stage_ts_utc

    # Collect all individual qualifier tokens from both channels for status checks
    all_qual_tokens: list[str] = []
    for q_str in [stage_qualifier, flow_qualifier]:
        if q_str:
            all_qual_tokens.extend(tok.strip() for tok in q_str.split(","))

    # Status determination — priority: ice → error → stale → ok
    if any("ice" in tok.lower() for tok in all_qual_tokens):
        base["gauge_status"] = "ice"
    elif (
        any(tok in ("Eqp", "Mnt") for tok in all_qual_tokens)
        or (stage_ft is None and bool(stage_history))
    ):
        base["gauge_status"] = "error"
        base["error_detail"] = stage_qualifier or flow_qualifier
    elif stage_ts_utc:
        try:
            reading_dt = datetime.fromisoformat(stage_ts_utc)
            age_secs = (datetime.now(timezone.utc) - reading_dt).total_seconds()
            base["gauge_status"] = "stale" if age_secs > 3 * 3600 else "ok"
        except Exception:
            base["gauge_status"] = "ok"
    else:
        base["gauge_status"] = "no_data"

    # Trend computation — only when status is ok
    if (
        base["gauge_status"] == "ok"
        and stage_history
        and stage_ft is not None
        and stage_ts_utc
    ):
        try:
            most_recent_dt = datetime.fromisoformat(stage_ts_utc)
            target_dt = most_recent_dt - timedelta(hours=3)

            best_entry: dict | None = None
            best_delta: float | None = None
            for entry in stage_history[:-1]:  # skip the most recent entry itself
                if entry.get("stage_ft") is None or not entry.get("timestamp_utc"):
                    continue
                try:
                    entry_dt = datetime.fromisoformat(entry["timestamp_utc"])
                    delta = abs((entry_dt - target_dt).total_seconds())
                    if best_delta is None or delta < best_delta:
                        best_delta = delta
                        best_entry = entry
                except Exception:
                    continue

            if best_entry is not None:
                diff = stage_ft - best_entry["stage_ft"]
                if diff > 0.1:
                    base["trend"] = "rising"
                elif diff < -0.1:
                    base["trend"] = "falling"
                else:
                    base["trend"] = "steady"
        except Exception:
            base["trend"] = None

    # Runnability — only deterministic when gauge is ok and min/max are defined
    run_min = cfg.get("run_min_ft")
    run_max = cfg.get("run_max_ft")
    if (
        base["gauge_status"] == "ok"
        and stage_ft is not None
        and run_min is not None
        and run_max is not None
    ):
        base["is_runnable"] = run_min <= stage_ft <= run_max

    return base


def get_weather(river_id: str) -> dict:
    """Fetch weather and precipitation data for the given river's put-in coordinates.

    Uses NWS for current conditions and hourly forecast (3 calls, grid-point
    cached). Uses Open-Meteo for 24-hour precipitation history (1 call). The
    two sources fail independently. Never raises exceptions.
    """
    fetch_ts = datetime.now(timezone.utc).isoformat()

    base: dict = {
        "river_id": river_id,
        "coordinates": None,
        "current_conditions": {
            "temperature_f": None,
            "weather_description": None,
            "wind_speed_mph": None,
            "wind_direction": None,
            "precip_last_1hr_in": None,
        },
        "precip_last_24hr_in": None,
        "precip_forecast_daily": [],
        "forecast_7day": [],
        "fetch_timestamp_utc": fetch_ts,
        "error_detail": None,
        "precip_error_detail": None,
    }

    if river_id not in RIVER_CONFIG:
        base["error_detail"] = f"Unknown river_id: {river_id!r}"
        return base

    cfg = RIVER_CONFIG[river_id]
    lat: float = cfg["lat"]
    lon: float = cfg["lon"]
    base["coordinates"] = {"lat": lat, "lon": lon}

    nws_headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}

    # ── NWS path ─────────────────────────────────────────────────────────────

    grid_key = (lat, lon)
    if grid_key not in _NWS_GRID_CACHE:
        try:
            resp = requests.get(
                f"https://api.weather.gov/points/{lat},{lon}",
                headers=nws_headers,
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                props = resp.json().get("properties", {})
                _NWS_GRID_CACHE[grid_key] = {
                    "forecast_hourly": props.get("forecastHourly"),
                    "observation_stations": props.get("observationStations"),
                }
            else:
                base["error_detail"] = (
                    f"NWS points HTTP {resp.status_code}: {resp.text[:500]}"
                )
        except Exception as exc:
            base["error_detail"] = f"NWS points request failed: {exc}"

    grid = _NWS_GRID_CACHE.get(grid_key)

    if grid:
        # Hourly forecast
        forecast_url = grid.get("forecast_hourly")
        if forecast_url:
            try:
                resp = requests.get(forecast_url, headers=nws_headers, timeout=TIMEOUT)
                if resp.status_code == 200:
                    periods = resp.json().get("properties", {}).get("periods", [])
                    forecast = []
                    for period in periods:
                        pop = period.get("probabilityOfPrecipitation") or {}
                        precip_pct = pop.get("value")
                        try:
                            start_utc = (
                                datetime.fromisoformat(period["startTime"])
                                .astimezone(timezone.utc)
                                .isoformat()
                            )
                        except Exception:
                            start_utc = period.get("startTime")
                        forecast.append({
                            "period_start_utc": start_utc,
                            "temperature_f": period.get("temperature"),
                            "precip_chance_pct": (
                                int(precip_pct) if precip_pct is not None else None
                            ),
                            "short_forecast": period.get("shortForecast"),
                        })
                    base["forecast_7day"] = forecast
                else:
                    if base["error_detail"] is None:
                        base["error_detail"] = (
                            f"NWS forecast HTTP {resp.status_code}: {resp.text[:300]}"
                        )
            except Exception as exc:
                if base["error_detail"] is None:
                    base["error_detail"] = f"NWS forecast request failed: {exc}"

        # Observation stations → latest observation
        stations_url = grid.get("observation_stations")
        if stations_url:
            try:
                resp = requests.get(stations_url, headers=nws_headers, timeout=TIMEOUT)
                if resp.status_code == 200:
                    features = resp.json().get("features", [])
                    if features:
                        station_id = features[0]["properties"]["stationIdentifier"]
                        obs_resp = requests.get(
                            f"https://api.weather.gov/stations/{station_id}/observations/latest",
                            headers=nws_headers,
                            timeout=TIMEOUT,
                        )
                        if obs_resp.status_code == 200:
                            obs = obs_resp.json().get("properties", {})

                            temp_c = (obs.get("temperature") or {}).get("value")
                            temp_f = (
                                round(temp_c * 9 / 5 + 32, 1)
                                if temp_c is not None else None
                            )

                            wind_ms = (obs.get("windSpeed") or {}).get("value")
                            wind_mph = (
                                round(wind_ms * 2.23694, 1)
                                if wind_ms is not None else None
                            )

                            wind_deg = (obs.get("windDirection") or {}).get("value")
                            wind_dir = (
                                _degrees_to_cardinal(wind_deg)
                                if wind_deg is not None else None
                            )

                            precip_mm = (obs.get("precipitationLastHour") or {}).get("value")
                            precip_1hr = (
                                round(precip_mm / 25.4, 3)
                                if precip_mm is not None else None
                            )

                            base["current_conditions"] = {
                                "temperature_f": temp_f,
                                "weather_description": obs.get("textDescription"),
                                "wind_speed_mph": wind_mph,
                                "wind_direction": wind_dir,
                                "precip_last_1hr_in": precip_1hr,
                            }
                        else:
                            if base["error_detail"] is None:
                                base["error_detail"] = (
                                    f"NWS obs HTTP {obs_resp.status_code}"
                                )
            except Exception as exc:
                if base["error_detail"] is None:
                    base["error_detail"] = f"NWS observation request failed: {exc}"

    # ── Open-Meteo path (independent of NWS) ─────────────────────────────────

    try:
        om_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=precipitation&daily=precipitation_sum&past_days=1&forecast_days=7&timezone=UTC"
        )
        resp = requests.get(om_url, timeout=TIMEOUT)
        if resp.status_code == 200:
            om_data = resp.json()
            times: list[str] = om_data.get("hourly", {}).get("time", [])
            precips: list = om_data.get("hourly", {}).get("precipitation", [])

            now_utc = datetime.now(timezone.utc)
            cutoff = now_utc - timedelta(hours=24)

            total_mm = 0.0
            for t, p in zip(times, precips):
                try:
                    # Open-Meteo returns naive UTC strings; attach tzinfo explicitly
                    dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
                    if cutoff <= dt <= now_utc and p is not None:
                        total_mm += p
                except Exception:
                    continue

            base["precip_last_24hr_in"] = round(total_mm / 25.4, 3)

            # Daily precipitation forecast (today onward, in inches)
            daily_times = om_data.get("daily", {}).get("time", [])
            daily_precips = om_data.get("daily", {}).get("precipitation_sum", [])
            today = datetime.now(timezone.utc).date()
            daily_forecast = []
            for d, p in zip(daily_times, daily_precips):
                try:
                    if datetime.fromisoformat(d).date() >= today and p is not None:
                        daily_forecast.append({
                            "date": d,
                            "precip_in": round(p / 25.4, 3),
                        })
                except Exception:
                    continue
            base["precip_forecast_daily"] = daily_forecast
        else:
            base["precip_error_detail"] = (
                f"Open-Meteo HTTP {resp.status_code}: {resp.text[:500]}"
            )
    except Exception as exc:
        base["precip_error_detail"] = f"Open-Meteo request failed: {exc}"

    return base


def get_river_conditions(river_id: str) -> dict:
    """Fetch gauge and weather data concurrently for the given river section.

    Runs get_gauge_data() and get_weather() in parallel via ThreadPoolExecutor.
    Returns {"gauge": ..., "weather": ...}. Never raises exceptions.
    """
    with ThreadPoolExecutor(max_workers=2) as executor:
        gauge_future = executor.submit(get_gauge_data, river_id)
        weather_future = executor.submit(get_weather, river_id)
        return {
            "gauge": gauge_future.result(),
            "weather": weather_future.result(),
        }


def get_all_conditions(include_history: bool = False) -> list[dict]:
    """Fetch current gauge and weather conditions for all rivers concurrently.

    Uses ThreadPoolExecutor(max_workers=8) so all 16 rivers are fetched in
    parallel — wall-clock time is roughly the same as fetching one river.

    History arrays (stage_history_24hr, flow_history_24hr, forecast_7day)
    are stripped by default to keep the response compact for summary views.
    Pass include_history=True to retain them. Results are sorted by river name.
    Never raises exceptions.
    """
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            river_id: executor.submit(get_river_conditions, river_id)
            for river_id in RIVER_CONFIG
        }
        results = []
        for river_id, future in futures.items():
            result = future.result()
            if not include_history:
                result["gauge"].pop("stage_history_24hr", None)
                result["gauge"].pop("flow_history_24hr", None)
                result["weather"].pop("forecast_7day", None)
            results.append(result)

    return sorted(results, key=lambda r: r["gauge"]["river_name"])


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python river_scout_api.py <river_id>\n"
            f"Known river IDs: {', '.join(RIVER_CONFIG.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)

    result = get_river_conditions(sys.argv[1])
    print(json.dumps(result, indent=2, default=str))
