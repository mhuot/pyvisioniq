#!/usr/bin/env python3
"""
Backfill charging_power in battery_status.csv from cached raw API snapshots.

The collector used to read only batteryStndChrgPower (AC), so DC fast charges
were recorded with charging_power = 0. The cached history_*.json files retain
the raw API payload, including batteryFstChrgPower, so the true power can be
recovered for any reading that still has a cache file.

Usage:
    python tools/backfill_charging_power.py            # dry run, prints changes
    python tools/backfill_charging_power.py --write    # apply changes to the CSV
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

CACHE_DIR = Path("cache")
BATTERY_CSV = Path("data/battery_status.csv")


def extract_true_power(snapshot: dict):
    """Return the corrected charging power from a cached snapshot, or None."""
    ev_status = snapshot.get("raw_data", {}).get("vehicleStatus", {}).get("evStatus", {})
    standard_power = ev_status.get("batteryStndChrgPower")
    fast_power = ev_status.get("batteryFstChrgPower")
    if standard_power is None and fast_power is None:
        return None
    return round(max(standard_power or 0, fast_power or 0), 3)


def collect_corrections():
    """Map collection timestamps to corrected charging power values."""
    corrections = {}
    history_files = sorted(CACHE_DIR.glob("history_*.json"))
    unreadable = 0
    for cache_file in history_files:
        try:
            with open(cache_file, "r", encoding="utf-8") as handle:
                snapshot = json.load(handle)
        except (OSError, json.JSONDecodeError):
            unreadable += 1
            continue

        collected_at = snapshot.get("timestamp")
        true_power = extract_true_power(snapshot)
        if collected_at is None or true_power is None:
            continue
        corrections[collected_at] = true_power
    return corrections, len(history_files), unreadable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply corrections to the CSV (default is a dry run)",
    )
    args = parser.parse_args()

    if not BATTERY_CSV.exists():
        print(f"{BATTERY_CSV} not found; run from the project root")
        return 1

    corrections, total_files, unreadable = collect_corrections()
    print(f"Scanned {total_files} cache files ({unreadable} unreadable)")
    print(f"Extracted charging power for {len(corrections)} snapshots")

    battery_df = pd.read_csv(BATTERY_CSV, dtype={"timestamp": str})
    changes = []
    for row_index, row in battery_df.iterrows():
        true_power = corrections.get(row["timestamp"])
        if true_power is None:
            continue
        recorded_power = row["charging_power"]
        recorded_value = 0 if pd.isna(recorded_power) else round(recorded_power, 3)
        if recorded_value != true_power:
            changes.append((row["timestamp"], recorded_power, true_power))
            battery_df.at[row_index, "charging_power"] = true_power

    if not changes:
        print("No corrections needed")
        return 0

    print(f"\n{len(changes)} rows to correct:")
    for timestamp, old_power, new_power in changes:
        print(f"  {timestamp}: {old_power} -> {new_power} kW")

    if args.write:
        backup_path = BATTERY_CSV.with_suffix(
            f".csv.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        BATTERY_CSV.rename(backup_path)
        battery_df.to_csv(BATTERY_CSV, index=False)
        print(f"\nWrote corrections (backup at {backup_path})")
        print("Consider rebuilding sessions: python tools/rebuild_sessions_from_battery.py")
    else:
        print("\nDry run — re-run with --write to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
