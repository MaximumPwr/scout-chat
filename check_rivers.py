#!/usr/bin/env python3
"""
check_rivers.py — RiverScout smoke test.

Verifies structural invariants of RIVER_CONFIG and ALIASES, then performs
live API calls for each river to confirm no exceptions propagate and that all
required response fields are present.

Usage:
    python check_rivers.py                  # test all rivers (live API, ~30s)
    python check_rivers.py pemi_eb          # test one river by ID
    python check_rivers.py --no-network     # structural checks only, no HTTP

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import sys

try:
    from river_scout_api import (
        RIVER_CONFIG,
        ALIASES,
        get_gauge_data,
        get_weather,
        get_all_conditions,
        resolve_river_id,
        list_rivers,
    )
except ImportError as exc:
    print(f"FAIL: Cannot import river_scout_api: {exc}")
    sys.exit(1)


failures: list[str] = []


def _pass(label: str) -> None:
    print(f"  PASS  {label}")


def _fail(label: str, detail: str = "") -> None:
    msg = f"  FAIL  {label}" + (f": {detail}" if detail else "")
    print(msg)
    failures.append(msg)


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        _pass(label)
    else:
        _fail(label, detail)


REQUIRED_CONFIG_FIELDS = [
    "river_name", "section", "usgs_site_id",
    "run_min_ft", "run_max_ft", "run_min_cfs", "run_max_cfs",
    "lat", "lon", "is_correlation_gauge", "dam_controlled",
    "notes", "aw_url",
]

REQUIRED_GAUGE_KEYS = [
    "river_id", "river_name", "section", "usgs_site_id",
    "gauge_status", "stage_ft", "flow_cfs", "trend",
    "run_min_ft", "run_max_ft", "is_runnable",
    "is_correlation_gauge", "dam_controlled",
    "notes", "aw_url", "fetch_timestamp_utc", "error_detail",
]

REQUIRED_WEATHER_KEYS = [
    "river_id", "coordinates", "current_conditions",
    "precip_last_24hr_in", "precip_forecast_daily",
    "forecast_7day", "fetch_timestamp_utc",
    "error_detail", "precip_error_detail",
]

VALID_GAUGE_STATUSES = {"ok", "ice", "stale", "error", "no_data"}

# Bounding box for New Hampshire (degrees)
NH_LAT = (42.6, 45.4)
NH_LON = (-72.6, -70.5)


def run_structural_checks(river_ids: list[str]) -> None:
    print("\n=== Structural checks (no network) ===")

    print("\n-- RIVER_CONFIG field completeness --")
    for river_id in river_ids:
        cfg = RIVER_CONFIG[river_id]
        for field in REQUIRED_CONFIG_FIELDS:
            check(f"{river_id}.{field}", field in cfg, "missing required field")

    print("\n-- Stage range sanity (run_min_ft < run_max_ft) --")
    for river_id in river_ids:
        cfg = RIVER_CONFIG[river_id]
        mn, mx = cfg.get("run_min_ft"), cfg.get("run_max_ft")
        if mn is not None and mx is not None:
            check(f"{river_id}: run_min_ft < run_max_ft", mn < mx, f"{mn} >= {mx}")

    print("\n-- Coordinate sanity (NH bounding box) --")
    for river_id in river_ids:
        cfg = RIVER_CONFIG[river_id]
        lat, lon = cfg.get("lat", 0), cfg.get("lon", 0)
        check(f"{river_id}: lat in NH range", NH_LAT[0] <= lat <= NH_LAT[1], f"lat={lat}")
        check(f"{river_id}: lon in NH range", NH_LON[0] <= lon <= NH_LON[1], f"lon={lon}")

    print("\n-- ALIASES integrity (all values are valid RIVER_CONFIG keys) --")
    for alias, target in ALIASES.items():
        check(
            f"ALIAS '{alias}' -> '{target}'",
            target in RIVER_CONFIG,
            f"target '{target}' not in RIVER_CONFIG",
        )

    print("\n-- resolve_river_id round-trip --")
    for river_id in river_ids:
        resolved = resolve_river_id(river_id)
        check(
            f"resolve_river_id('{river_id}')",
            resolved == river_id,
            f"returned {resolved!r}",
        )

    print("\n-- list_rivers completeness --")
    listed_ids = {r["river_id"] for r in list_rivers()}
    for river_id in river_ids:
        check(f"list_rivers includes '{river_id}'", river_id in listed_ids)


def run_live_checks(river_ids: list[str]) -> None:
    print("\n=== Live API checks ===")

    for river_id in river_ids:
        print(f"\n-- {river_id} --")

        # Gauge
        try:
            gauge = get_gauge_data(river_id)
        except Exception as exc:
            _fail(f"{river_id}: get_gauge_data raised exception", str(exc))
            continue

        check("get_gauge_data returns dict", isinstance(gauge, dict))
        for key in REQUIRED_GAUGE_KEYS:
            check(f"gauge has key '{key}'", key in gauge)

        status = gauge.get("gauge_status", "")
        check(f"gauge_status valid (got '{status}')", status in VALID_GAUGE_STATUSES)

        # Invariant: is_runnable contract
        is_runnable = gauge.get("is_runnable")
        stage = gauge.get("stage_ft")
        run_min = gauge.get("run_min_ft")
        run_max = gauge.get("run_max_ft")

        if status == "ok" and stage is not None and run_min is not None and run_max is not None:
            expected = run_min <= stage <= run_max
            check(
                f"is_runnable consistent with stage ({stage:.2f} ft in [{run_min}, {run_max}])",
                is_runnable == expected,
                f"is_runnable={is_runnable!r}, expected={expected}",
            )
        elif status != "ok":
            check(
                f"is_runnable is None when gauge_status='{status}'",
                is_runnable is None,
                f"got is_runnable={is_runnable!r}",
            )

        # Weather
        try:
            weather = get_weather(river_id)
        except Exception as exc:
            _fail(f"{river_id}: get_weather raised exception", str(exc))
            continue

        check("get_weather returns dict", isinstance(weather, dict))
        for key in REQUIRED_WEATHER_KEYS:
            check(f"weather has key '{key}'", key in weather)

    # get_all_conditions: count and sort order
    if set(river_ids) == set(RIVER_CONFIG.keys()):
        print("\n-- get_all_conditions --")
        try:
            all_results = get_all_conditions()
        except Exception as exc:
            _fail("get_all_conditions raised exception", str(exc))
            return

        check(
            f"get_all_conditions returns {len(RIVER_CONFIG)} results",
            len(all_results) == len(RIVER_CONFIG),
            f"got {len(all_results)}",
        )
        names = [r["gauge"]["river_name"] for r in all_results]
        check(
            "get_all_conditions sorted by river_name",
            names == sorted(names),
            f"actual order: {names}",
        )


def main() -> None:
    args = sys.argv[1:]
    no_network = "--no-network" in args
    args = [a for a in args if not a.startswith("--")]

    if args:
        for arg in args:
            if arg not in RIVER_CONFIG:
                print(f"Unknown river_id: {arg!r}")
                print(f"Valid IDs: {', '.join(sorted(RIVER_CONFIG.keys()))}")
                sys.exit(1)
        river_ids = args
    else:
        river_ids = list(RIVER_CONFIG.keys())

    mode = "structural only" if no_network else "structural + live API"
    print(f"RiverScout smoke test — {len(river_ids)} river(s), {mode}")

    run_structural_checks(river_ids)

    if not no_network:
        run_live_checks(river_ids)

    print("\n" + "=" * 50)
    if failures:
        print(f"FAILED — {len(failures)} check(s) failed:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    else:
        print("PASSED — all checks passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
