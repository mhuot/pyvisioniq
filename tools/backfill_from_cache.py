#!/usr/bin/env python3
"""Backfill battery and location rows from cache snapshots the CSVs missed.

The web app refreshes the API cache when it expires, but only the collector
writes CSV rows. Any fresh fetch triggered from the web path therefore lands
in cache and nowhere else — an audit found 143 such snapshots in a 31-day
cache window, each a real reading sitting about an hour from the nearest
stored row.

This is additive only. It appends rows for cache snapshots that have no
battery row within ``TOLERANCE`` of their timestamp, then sorts and
deduplicates. It deliberately does NOT replay snapshots through
``store_vehicle_data``: that path runs the charging-session state machine,
and feeding it historical readings out of order would corrupt current
session tracking. (For the same reason, do not use
``tools/reprocess_cache_complete.py`` unless the CSVs are lost entirely —
it discards them and rebuilds from cache alone, which now spans only the
retention window.)

Temperature columns: ``vehicle_temp`` is recovered from the snapshot's
``airTemp`` (reported in Fahrenheit). ``meteo_temp`` is left empty — it was
fetched live from the weather service at collection time and cannot be
recovered retroactively. ``temperature`` is set to the vehicle reading so
the row is not blank, and ``is_cached`` is preserved from the snapshot.

Usage:
    python tools/backfill_from_cache.py            # dry run, prints the plan
    python tools/backfill_from_cache.py --write    # append, sort, dedupe
"""

import argparse
import glob
import json
import shutil
from datetime import datetime

import numpy as np
import pandas as pd

BATTERY_CSV = "data/battery_status.csv"
LOCATIONS_CSV = "data/locations.csv"
TOLERANCE_MINUTES = 10.0


def fahrenheit_to_celsius(value):
    """Convert the snapshot's airTemp, which the API reports in Fahrenheit."""
    try:
        return round((float(value) - 32.0) * 5.0 / 9.0, 1)
    except (TypeError, ValueError):
        return None


def load_snapshots():
    """Read every history cache file into (timestamp, payload) pairs."""
    snapshots = []
    for path in sorted(glob.glob("cache/history_*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            stamp = pd.Timestamp(payload["timestamp"])
        except (OSError, ValueError, KeyError):
            continue
        snapshots.append((stamp, payload))
    return snapshots


def missing_snapshots(snapshots, battery_df):
    """Snapshots with no battery row within TOLERANCE of their timestamp."""
    stored = np.sort(battery_df["t"].values)
    missing = []
    for stamp, payload in snapshots:
        target = np.datetime64(stamp)
        index = np.searchsorted(stored, target)
        nearest = min(
            abs((stored[j] - target) / np.timedelta64(1, "m"))
            for j in (max(0, index - 1), min(len(stored) - 1, index))
        )
        if nearest > TOLERANCE_MINUTES:
            missing.append((stamp, payload))
    return missing


def battery_row(stamp, payload):
    """Build a battery_status row from a cache snapshot."""
    battery = payload.get("battery") or {}
    air = ((payload.get("raw_data") or {}).get("airTemp") or {}).get("value")
    celsius = fahrenheit_to_celsius(air)
    return {
        "timestamp": stamp.isoformat(),
        "battery_level": battery.get("level"),
        "is_charging": bool(battery.get("is_charging")),
        "charging_power": battery.get("charging_power"),
        "remaining_time": battery.get("remaining_time"),
        "range": battery.get("range"),
        "temperature": celsius,
        "odometer": payload.get("odometer"),
        "meteo_temp": None,
        "vehicle_temp": celsius,
        "is_cached": bool(payload.get("is_cached")),
    }


def location_row(stamp, payload):
    """Build a locations row, or None when the snapshot has no fix."""
    location = payload.get("location") or {}
    if location.get("latitude") is None:
        return None
    return {
        "timestamp": stamp.isoformat(),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "last_updated": location.get("last_updated"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply instead of dry run")
    args = parser.parse_args()

    battery = pd.read_csv(BATTERY_CSV)
    battery["t"] = pd.to_datetime(battery["timestamp"], errors="coerce", format="mixed")
    battery = battery.dropna(subset=["t"])

    snapshots = load_snapshots()
    missing = missing_snapshots(snapshots, battery)
    print(f"cache snapshots: {len(snapshots)}; without a battery row: {len(missing)}")
    if not missing:
        print("nothing to backfill")
        return

    new_battery = pd.DataFrame([battery_row(s, p) for s, p in missing])
    new_locations = pd.DataFrame([row for row in (location_row(s, p) for s, p in missing) if row])
    print(f"would append {len(new_battery)} battery rows, {len(new_locations)} location rows")
    print(
        new_battery[["timestamp", "battery_level", "is_charging", "odometer"]]
        .head(8)
        .to_string(index=False)
    )

    if not args.write:
        print("\ndry run; use --write to apply")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for path in (BATTERY_CSV, LOCATIONS_CSV):
        shutil.copy(path, f"{path}.backup_{stamp}")
        print(f"backed up {path} -> {path}.backup_{stamp}")

    merged = pd.concat([battery.drop(columns=["t"]), new_battery], ignore_index=True)
    merged["_t"] = pd.to_datetime(merged["timestamp"], errors="coerce", format="mixed")
    merged = (
        merged.dropna(subset=["_t"])
        .sort_values("_t")
        .drop_duplicates(subset=["timestamp"])
        .drop(columns=["_t"])
    )
    merged.to_csv(BATTERY_CSV, index=False)
    print(f"battery_status.csv: {len(battery)} -> {len(merged)} rows")

    locations = pd.read_csv(LOCATIONS_CSV)
    merged_loc = pd.concat([locations, new_locations], ignore_index=True)
    merged_loc["_t"] = pd.to_datetime(merged_loc["timestamp"], errors="coerce", format="mixed")
    merged_loc = (
        merged_loc.dropna(subset=["_t"])
        .sort_values("_t")
        .drop_duplicates(subset=["timestamp"])
        .drop(columns=["_t"])
    )
    merged_loc.to_csv(LOCATIONS_CSV, index=False)
    print(f"locations.csv: {len(locations)} -> {len(merged_loc)} rows")


if __name__ == "__main__":
    main()
