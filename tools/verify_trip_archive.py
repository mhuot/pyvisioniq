#!/usr/bin/env python3
"""Check whether trips.csv has caught up with an archived trip record.

The Bluelink trip list arrives piecemeal. During the August 2026 Duluth return
the app already showed all four legs while trips.csv held only two of them, so
a chart built from the CSV would have costed a 154 mile drive as 48 miles. The
CSV usually fills in over the following polls, but nothing reports when it has,
which is what this does.

Archives under data/trip_archive/ record what the app showed at capture time.
This compares each one against the current CSV and reports the shortfall, so a
scheduled run can say "reconciled" and stop being interesting.

Exit code 0 when every archived leg is present, 1 when something is still
missing -- so cron can be silenced by success.

Usage:
    python tools/verify_trip_archive.py [--archive-dir data/trip_archive]
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

DEFAULT_ARCHIVE_DIR = Path("data/trip_archive")
TRIPS_CSV = Path("data/trips.csv")
# A logged leg counts as the archived one when it is this close on distance.
# Whole-mile rounding on both sides, so an exact match is not guaranteed.
DISTANCE_TOLERANCE_MI = 2.0


def load_trips():
    """Current trip rows, with dates parsed."""
    frame = pd.read_csv(TRIPS_CSV)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    return frame.dropna(subset=["date"])


def leg_is_present(trips, leg):
    """Whether a logged row plausibly matches this archived leg."""
    start = pd.Timestamp(leg["start"])
    window = trips[
        (trips["date"] >= start - pd.Timedelta(minutes=30))
        & (trips["date"] <= start + pd.Timedelta(minutes=30))
    ]
    if window.empty:
        return False
    return bool((window["distance"] - leg["distance_mi"]).abs().le(DISTANCE_TOLERANCE_MI).any())


def check(archive_path, trips):
    """Compare one archive against the CSV. Returns (missing, total, detail)."""
    archive = json.loads(archive_path.read_text())
    legs = archive.get("return_legs_from_app", {}).get("legs", [])
    if not legs:
        return 0, 0, ["no app-sourced legs recorded; nothing to check"]

    detail = []
    missing = 0
    for leg in legs:
        present = leg_is_present(trips, leg)
        missing += 0 if present else 1
        detail.append(
            f"  {leg['start']}  {leg['distance_mi']:>3} mi  "
            f"{leg['total_wh'] / 1000:>5.1f} kWh  {'present' if present else 'MISSING'}"
        )

    archived_mi = sum(leg["distance_mi"] for leg in legs)
    archived_kwh = sum(leg["total_wh"] for leg in legs) / 1000
    detail.append(f"  archived total: {archived_mi} mi, {archived_kwh:.1f} kWh")
    return missing, len(legs), detail


def main():
    """Report whether each archived trip is now in the CSV."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    args = parser.parse_args()

    archives = sorted(Path(args.archive_dir).glob("*.json"))
    if not archives:
        print("no archives to verify")
        return 0

    trips = load_trips()
    outstanding = 0
    for path in archives:
        missing, total, detail = check(path, trips)
        state = "RECONCILED" if missing == 0 else f"{missing}/{total} legs still missing"
        print(f"{path.name}: {state}")
        if missing:
            print("\n".join(detail))
        outstanding += missing

    return 0 if outstanding == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
