---
name: Bug report
about: Something is broken — wrong gauge reading, bad river data, app crash
title: "[BUG] "
labels: bug
assignees: ""
---

## What happened?

_Describe the problem clearly. What did you see? What did you expect?_

## River section affected (if applicable)

_River name and section (e.g., "Pemi East Branch", "Wild Ammo Lower")_

## Steps to reproduce

1.
2.
3.

## Gauge / data details

- **Reported stage:** _e.g., 5.2 ft_
- **Reported status:** _e.g., "ok" / "stale" / "ice" / "error"_
- **Approximate time (UTC):** _e.g., 2025-04-12 14:30 UTC_
- **USGS gauge page:** _e.g., https://waterdata.usgs.gov/nwis/uv?site_no=01074520_

## What you observed on the water (for wrong-range bugs)

_Was the river actually runnable at that reading? What were conditions like?_

## Environment

- [ ] Live app
- [ ] Local dev (`streamlit run river_scout_chat.py`)
- [ ] Direct API call (`python river_scout_api.py <river_id>`)

---

> **Tip:** If the gauge shows stale or missing data, check the USGS gauge page
> directly first — USGS sometimes takes gauges offline for maintenance, which
> is outside this app's control.
