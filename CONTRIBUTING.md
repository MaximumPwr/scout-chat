# Contributing to RiverScout

Thank you for contributing! This guide covers the two main ways to help:

1. **Adding a river section** — the most common contribution, low risk
2. **Modifying core logic** — requires care; read the invariants section first

---

## How Contributions Work

Contributions go through a standard GitHub fork → pull request flow:

1. **Fork** the repo to your own GitHub account
2. Make your changes in your fork (clone it locally, edit, push)
3. **Open a Pull Request** back to this repo
4. The maintainer reviews your code diff and approves or requests changes
5. When the PR is merged, Streamlit Cloud **automatically redeploys** the live app

**Nothing reaches production until the maintainer approves the PR.**

### You never share API keys

The data layer (`river_scout_api.py`) — the only file most contributors will
ever need to touch — uses three entirely public APIs (USGS, NWS, Open-Meteo)
that require no credentials. API keys stay out of PRs entirely.

If you want to run the full chat UI locally to test your changes end-to-end,
you'll need an Anthropic API key — but that key goes in your local
`.streamlit/secrets.toml`, which is gitignored and never included in a PR.
See the [Quick Start in README.md](README.md#quick-start-local-development).

---

## Table of Contents

- [Safe to Touch vs. Handle With Care](#safe-to-touch-vs-handle-with-care)
- [How to Add a River Section](#how-to-add-a-river-section)
  - [Step 1: Find the USGS Gauge ID](#step-1-find-the-usgs-gauge-id)
  - [Step 2: Find the Runnable Stage Range](#step-2-find-the-runnable-stage-range)
  - [Step 3: Add the Config Entry](#step-3-add-the-config-entry)
  - [Step 4: Add Aliases](#step-4-add-aliases)
  - [Step 5: Verify Your Changes](#step-5-verify-your-changes)
- [Core Invariants — Do Not Break](#core-invariants--do-not-break)
- [PR Checklist](#pr-checklist)
- [Reporting Bugs](#reporting-bugs)

---

## Safe to Touch vs. Handle With Care

### Safe to touch

These are pure data — adding or editing entries cannot break existing behavior:

- **`RIVER_CONFIG`** in `river_scout_api.py` — add new river sections here
- **`ALIASES`** in `river_scout_api.py` — add natural-language aliases here
- **`SYSTEM_PROMPT`** in `river_scout_chat.py` — adjust Claude's persona or safety language (be conservative)
- **`requirements.txt`** — add a dependency only if a new API function requires it

### Handle with care

These contain logic with safety implications. Changes require a detailed PR
description explaining the reasoning and edge cases you considered:

| Location | Why It's Sensitive |
|----------|--------------------|
| `river_scout_api.py:528–545` — `gauge_status` determination block | Priority chain `ice → error → stale → ok` is safety-critical |
| `river_scout_api.py:328` — `_safe_float()` | Sentinel handling for USGS ice values; changing this affects ice detection |
| `river_scout_api.py:583–592` — `is_runnable` assignment | Must only be True when status is "ok" AND stage is in the configured range |
| `get_all_conditions()` sort | Must remain sorted by `river_name` |
| `_dispatch_tool()` in `river_scout_chat.py` | Tool dispatch table; adding a tool here requires a corresponding entry in `TOOLS` |

---

## How to Add a River Section

All changes happen in `river_scout_api.py`. The chat layer picks up new rivers
automatically — no changes to `river_scout_chat.py` are needed.

**Weather is automatic.** The `lat`/`lon` fields in your config entry drive all
weather lookups. Once they're set, `get_weather()` queries NWS and Open-Meteo
for that location automatically — no additional weather configuration needed.

**The sidebar updates automatically.** The left-hand river list in the Streamlit
app reads directly from `RIVER_CONFIG` at startup and sorts by river name. Your
new river will appear in the sidebar as soon as the config entry is added.

### Step 1: Find the USGS Gauge ID

1. Go to the [USGS National Water Dashboard](https://dashboard.waterdata.usgs.gov/app/nwd/en/)
   and navigate to the watershed on the map.
2. Click on a gauge point. The **Site Number** (8 digits, e.g., `01074520`) is
   the value you need for `usgs_site_id`.
3. Confirm the gauge measures **stage** (parameter code `00065`), and ideally
   **discharge** (code `00060`), by viewing the gauge's data page:
   `https://waterdata.usgs.gov/nwis/uv?site_no=XXXXXXXX`

**Correlation gauges:** Some sections don't have their own gauge. If the
American Whitewater page cites a nearby river's gauge as a proxy (e.g., the
Swift River sections use the EB Pemi gauge), set `"is_correlation_gauge": True`
and explain the relationship in `notes`. See the existing Swift River entries
in `RIVER_CONFIG` for examples.

### Step 2: Find the Runnable Stage Range

The [American Whitewater](https://www.americanwhitewater.org) gauge page for each
run shows historical readings color-coded by runnability — use these as your
starting point for `run_min_ft` / `run_max_ft`.

If AW doesn't have the section, check:
- [NH Paddler forums](https://nhpaddler.com/)
- Local paddling club trip reports
- Your own experience on the water (document your observations in `notes`)

### Step 3: Add the Config Entry

Add an entry to the `RIVER_CONFIG` dict in `river_scout_api.py`. Use an existing
entry as a template. The key must be a short, lowercase slug with underscores
(e.g., `"swift_upper"`, `"contoo"`, `"saco_upper"`).

**All fields are required.** Here is a full field-by-field reference:

```python
"your_river_key": {
    # Human-readable river name — used in the sidebar and sorted output
    "river_name": "Your River Name",

    # Paddling section description (put-in to take-out or landmark)
    "section": "Put-in Town to Take-out Town",

    # 8-digit USGS site number — see Step 1
    "usgs_site_id": "01234567",

    # Stage window for runnability (feet) — see Step 2
    "run_min_ft": 3.5,
    "run_max_ft": 7.0,

    # Flow window (CFS) — include even if not currently used in runnability logic
    "run_min_cfs": 400,
    "run_max_cfs": 2000,

    # Put-in coordinates in decimal degrees (used for weather lookup)
    # Get from Google Maps or the USGS gauge page — use the put-in, not the gauge station
    "lat": 43.12345,
    "lon": -71.98765,

    # True if this gauge measures a DIFFERENT river used as a proxy for this section
    "is_correlation_gauge": False,

    # True if a dam significantly controls flow on this section
    "dam_controlled": False,

    # Free-text note shown to Claude and included in responses.
    # Include: data source quirks, correlation notes, dam behavior, safety warnings.
    "notes": "Natural flow - direct read.",

    # American Whitewater URL for this section
    "aw_url": "https://www.americanwhitewater.org/content/River/view/river-detail/XXXX/main",
},
```

**Correlation gauge example** (section uses a gauge on a different river):

```python
"is_correlation_gauge": True,
"notes": (
    "Correlation gauge referencing East Branch of the Pemi at Lincoln (USGS 01074520). "
    "The Swift Upper does not have its own gauge — runnable range is calibrated "
    "against the EB Pemi reading."
),
```

**Dam-controlled example** (flow is regulated, not natural):

```python
"dam_controlled": True,
"notes": (
    "Dam controlled. This gauge reads INFLOW to the dam above Ayers Island. "
    "If rising: the dam may release soon. If dropping: the dam may cut early. "
    "Always check trend direction, not just current reading."
),
```

### Step 4: Add Aliases

Add one or more natural-language aliases to the `ALIASES` dict so users can
refer to the river by common names, nicknames, or local shorthand:

```python
# In the ALIASES dict (all keys must be lowercase):
"your river":       "your_river_key",
"common nickname":  "your_river_key",
```

Rules:
- Keys must be **lowercase**
- Each value must be a valid key in `RIVER_CONFIG` — the smoke test catches broken aliases
- Multiple aliases pointing to the same key are fine
- Avoid aliases that could ambiguously match multiple sections

### Step 5: Verify Your Changes

Run the smoke test before opening a PR:

```bash
# Structural checks only — no network required, runs in seconds
python check_rivers.py --no-network

# Live API call for your new river
python check_rivers.py your_river_key

# Full suite (all 16 rivers, ~30 seconds, requires internet)
python check_rivers.py
```

You can also inspect the raw JSON output directly:

```bash
python river_scout_api.py your_river_key
```

The output must be valid JSON with a `gauge_status` of `ok`, `stale`, `ice`,
`error`, or `no_data` — never a Python exception or traceback.

---

## Core Invariants — Do Not Break

These behaviors are load-bearing. Any PR that changes them must explicitly
justify the change and update this document.

### 1. `gauge_status` Priority Chain

`gauge_status` uses a strict priority chain:

```
ice  >  error  >  stale  >  ok
```

`ice` always wins. If a reading carries an ice qualifier, it is reported as
`ice` — not `ok`, not `stale`, not `error`. This is safety-critical: a
frozen sensor may report a plausible-looking stage value that is actually wrong.

**Location:** `river_scout_api.py:528–545`

Do not introduce new statuses that take priority over `ice` without a maintainer
sign-off.

### 2. `is_runnable` Is Only True Under Two Conditions

```
is_runnable = True  only when  gauge_status == "ok"
                               AND run_min_ft <= stage_ft <= run_max_ft
```

When `gauge_status` is anything other than `"ok"` — even `"stale"` with a
believable reading — `is_runnable` must be `None` (unknown). A stale or
error-flagged reading cannot safely drive a go/no-go decision.

**Location:** `river_scout_api.py:583–592`

### 3. Ice Detection via USGS Sentinel and Qualifiers

Two independent paths detect ice:
1. `_safe_float()` at line 328 maps `-999999.0` (USGS bad-data sentinel) to `None`
2. The qualifier-scanning loop at line 529 checks for `"ice"` in USGS qualifier tokens

Both paths must remain intact. A stage reading of `-999999.0` must never be
treated as a real water level.

### 4. No Exceptions Propagate from Public Functions

All public functions (`get_gauge_data`, `get_weather`, `get_river_conditions`,
`get_all_conditions`, `list_rivers`, `resolve_river_id`) must return structured
dicts — never raise exceptions. All HTTP calls, JSON parsing, and datetime math
must be wrapped in try/except. Errors surface in `error_detail` or
`precip_error_detail` fields.

This contract keeps the chatbot fault-tolerant: a broken gauge or a transient
NWS outage does not crash the session.

### 5. All `ALIASES` Values Must Be Valid `RIVER_CONFIG` Keys

Every value in `ALIASES` must be a key that exists in `RIVER_CONFIG`. The smoke
test (`check_rivers.py --no-network`) checks this explicitly.

### 6. `get_all_conditions()` Must Return Results Sorted by `river_name`

The tool description guarantees alphabetical ordering. The sort key is
`result["gauge"]["river_name"]`. Claude's summary responses depend on this order.

---

## PR Checklist

Before opening a pull request:

- [ ] `python check_rivers.py --no-network` passes with no failures
- [ ] `python river_scout_api.py <your_river_key>` returns valid JSON (no traceback)
- [ ] All `ALIASES` values in your change resolve to valid `RIVER_CONFIG` keys
- [ ] `run_min_ft` < `run_max_ft` for your new entry
- [ ] `is_correlation_gauge` is set correctly (`True` only if the gauge is on a different river)
- [ ] `dam_controlled` is set correctly
- [ ] `notes` field explains any non-obvious gauge behavior, correlation, or dam quirks
- [ ] `aw_url` links to the correct American Whitewater section page
- [ ] `lat`/`lon` are the **put-in** coordinates, not the gauge station coordinates
- [ ] No changes to `gauge_status` logic, `_safe_float()`, or `is_runnable` assignment
  _(unless your PR is specifically about those — in that case, describe the change in detail)_

---

## Reporting Bugs

Use the [GitHub Issues tab](https://github.com/MaximumPwr/scout-chat/issues).

You can also submit feedback directly from the chat interface by typing
"feedback" or clicking the feedback option in the chatbot.

**Wrong runnable range?** Include the river section name, the stage reading at
the time, and what you observed on the water.

**Broken gauge or stale data?** Check the USGS gauge page directly first —
`https://waterdata.usgs.gov/nwis/uv?site_no=XXXXXXXX` — USGS sometimes takes
gauges offline for maintenance, which is not a bug in this app.
