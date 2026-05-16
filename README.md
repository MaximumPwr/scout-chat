# RiverScout — NH Whitewater Conditions Chatbot

A real-time chatbot for New Hampshire whitewater paddlers. Ask it which rivers are
running, check a specific gauge, or get a multi-day precipitation forecast — all
powered by live USGS and NWS data and answered by Claude.

---

## What It Does

RiverScout monitors 16 NH whitewater river sections. For each section it:

- Pulls current **stage (ft) and flow (CFS)** from the USGS Instantaneous Values API
- Determines whether the river is **runnable** based on a configured stage window
- Computes a **3-hour trend** (rising / falling / steady) from gauge history
- Detects **ice conditions** and equipment faults from USGS qualifier codes
- Fetches **current weather, 7-day hourly forecast, and precipitation history** from NWS and Open-Meteo

All 16 rivers are fetched concurrently using `ThreadPoolExecutor`, so a full
overview takes roughly the same wall-clock time as checking a single river.

---

## Who It's For

- **Paddlers** who want a plain-English answer to "what's running right now?"
- **Developers** exploring the [Anthropic Claude API tool-use pattern](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)
- **Contributors** who want to add river sections or extend the data layer

---

## Architecture

```
river_scout_chat.py        ← Streamlit UI + Anthropic API agentic tool-use loop
        │
        └── river_scout_api.py   ← Pure data layer (no Anthropic dependency)
                │
                ├── USGS Instantaneous Values API  (stage, flow, qualifiers)
                ├── NWS API                        (weather + hourly forecast)
                └── Open-Meteo API                 (precipitation history + daily forecast)
```

**Two files. Three public APIs. Zero credentials required for the data layer.**

`river_scout_api.py` has no Anthropic dependency — it can be imported by any
Python script, MCP server, or scheduled job. `river_scout_chat.py` is the only
file that requires an API key.

---

## Exposed Tools (Anthropic Tool-Use)

The chatbot gives Claude access to six tools:

| Tool | Purpose |
|------|---------|
| `get_all_conditions` | Fetch all 16 rivers concurrently; returns a compact summary sorted by river name |
| `get_river_conditions` | Gauge + weather for one river section (the primary single-river tool) |
| `get_gauge_data` | Gauge only (stage, flow, trend, 24-hr history) — faster than full conditions |
| `get_weather` | Weather only (NWS current + 7-day forecast, Open-Meteo precipitation) |
| `list_rivers` | Full routing table — IDs, USGS site IDs, section names, AW URLs |
| `resolve_river_id` | Fuzzy-match a user's text to a canonical `river_id` |

Tool inputs accept both canonical IDs (e.g., `pemi_eb`) and natural-language aliases
(e.g., `"east branch"`, `"freight train"`, `"wild ammo"`).

---

## Quick Start (Local Development)

### 1. Clone

```bash
git clone https://github.com/MaximumPwr/scout-chat.git
cd scout-chat
```

### 2. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.10+ recommended.

### 3. Configure your API key

```bash
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Open .streamlit/secrets.toml and paste your key
```

Get a free API key at [console.anthropic.com](https://console.anthropic.com).

### 4. Run

```bash
streamlit run river_scout_chat.py
```

The app opens at `http://localhost:8501`.

**You can test the data layer without any API key:**

```bash
# Inspect a single river (outputs JSON)
python river_scout_api.py pemi_eb

# Run the full smoke test (structural checks + live API verification)
python check_rivers.py --no-network   # fast, no HTTP
python check_rivers.py pemi_eb        # live call for one river
python check_rivers.py                # all 16 rivers (~30 seconds)
```

---

## Deploying to Streamlit Community Cloud

1. **Fork** this repo to your own GitHub account.
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Click **New app** → select your fork → set **Main file path** to `river_scout_chat.py`.
4. Under **Advanced settings → Secrets**, paste:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."   # required
GITHUB_TOKEN = "ghp_..."           # optional — enables the in-chat feedback button
```

5. Click **Deploy**. Streamlit Cloud installs `requirements.txt` automatically.

The app redeploys automatically whenever you push to the `main` branch of your fork.

> **Note:** `ANTHROPIC_API_KEY` is required — the app crashes on startup without it.
> `GITHUB_TOKEN` is optional; without it the feedback feature shows a graceful error
> but the rest of the app works normally.
>
> Never commit `.streamlit/secrets.toml` — it is gitignored by default.

---

## Data Sources

| Source | What It Provides | Auth |
|--------|-----------------|------|
| [USGS Instantaneous Values API](https://waterservices.usgs.gov/nwis/iv/) | Stage (ft), flow (CFS), qualifiers, 24-hr history | None |
| [NWS API](https://api.weather.gov/) | Current conditions, 7-day hourly forecast | None (User-Agent required) |
| [Open-Meteo](https://api.open-meteo.com/) | 24-hr observed precipitation, daily forecast | None |

All three APIs are free, require no registration, and have no restrictive rate limits
for this use pattern.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add river sections, where to
find USGS gauge IDs, and which parts of the codebase are safe to modify.

The sidebar river list and weather data both update automatically when you add a
new entry to `RIVER_CONFIG` — no changes to the chat file are needed.

---

## License

MIT
