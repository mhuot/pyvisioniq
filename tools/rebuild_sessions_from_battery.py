#!/usr/bin/env python3
"""Rebuild charging_sessions.csv from battery_status.csv history.

Scans the full battery_status history to detect charging start/stop
transitions and reconstructs charging sessions with correct columns.

Run from repo root:
    python3 tools/rebuild_sessions_from_battery.py
    python3 tools/rebuild_sessions_from_battery.py --preview
"""

import argparse
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from src.storage.csv_store import CHARGING_SESSION_FIELDS


def rebuild_sessions(battery_df, gap_minutes=90.0, capacity_kwh=77.4):
    """Build charging sessions from battery status history."""
    df = battery_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Coerce columns
    df["battery_level"] = pd.to_numeric(df["battery_level"], errors="coerce")
    df["is_charging"] = df["is_charging"].apply(
        lambda v: str(v).strip().lower() == "true" if pd.notna(v) else False
    )
    df["charging_power"] = pd.to_numeric(df["charging_power"], errors="coerce").fillna(0)

    sessions = []
    active = None

    for _, row in df.iterrows():
        ts = row["timestamp"]
        level = row["battery_level"]
        charging = row["is_charging"]
        power = row["charging_power"]

        if pd.isna(level):
            continue

        # Try to get location from the row's associated data
        lat = row.get("latitude") if "latitude" in row.index else None
        lon = row.get("longitude") if "longitude" in row.index else None

        if charging:
            if active is None:
                # Start new session
                active = {
                    "start_time": ts,
                    "end_time": ts,
                    "start_battery": level,
                    "end_battery": level,
                    "max_power": power,
                    "lat": lat,
                    "lon": lon,
                }
            else:
                # Check gap
                gap = (ts - active["end_time"]).total_seconds() / 60
                if gap > gap_minutes:
                    # Finalize previous session and start new one
                    sessions.append(_finalize(active, capacity_kwh))
                    active = {
                        "start_time": ts,
                        "end_time": ts,
                        "start_battery": level,
                        "end_battery": level,
                        "max_power": power,
                        "lat": lat,
                        "lon": lon,
                    }
                else:
                    # Update ongoing session
                    active["end_time"] = ts
                    active["end_battery"] = level
                    if power > active["max_power"]:
                        active["max_power"] = power
        else:
            if active is not None:
                # Charging stopped — finalize session
                active["end_time"] = ts
                active["end_battery"] = level
                sessions.append(_finalize(active, capacity_kwh))
                active = None

    # Finalize any session still open at end of data
    if active is not None:
        sessions.append(_finalize(active, capacity_kwh, is_complete=False))

    return pd.DataFrame(sessions, columns=CHARGING_SESSION_FIELDS)


def _finalize(active, capacity_kwh, is_complete=True):
    """Convert an active session dict into a CSV row dict."""
    start = active["start_time"]
    end = active["end_time"]
    duration = round((end - start).total_seconds() / 60, 1)
    start_bat = active["start_battery"]
    end_bat = active["end_battery"]
    delta = max(end_bat - start_bat, 0)
    energy = round((delta / 100.0) * capacity_kwh, 2)
    avg_power = round(energy / (duration / 60), 2) if duration > 0 else 0

    session_id = f"charge_{start.strftime('%Y%m%d_%H%M%S')}"

    return {
        "session_id": session_id,
        "start_time": start,
        "end_time": end,
        "start_battery": start_bat,
        "end_battery": end_bat,
        "energy_added": energy,
        "avg_power": avg_power,
        "max_power": active["max_power"],
        "location_lat": active.get("lat"),
        "location_lon": active.get("lon"),
        "is_complete": is_complete,
        "duration_minutes": duration,
    }


def main():
    """Rebuild charging sessions CSV from battery status history."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="Show results without writing")
    args = parser.parse_args()

    data_dir = Path(os.getenv("DATA_DIR", ROOT / "data"))
    battery_file = data_dir / "battery_status.csv"
    sessions_file = data_dir / "charging_sessions.csv"
    location_file = data_dir / "locations.csv"

    if not battery_file.exists():
        print(f"Battery status file not found: {battery_file}")
        return

    battery_df = pd.read_csv(battery_file)
    print(f"Read {len(battery_df)} battery status records")

    # Merge location data if available
    if location_file.exists():
        loc_df = pd.read_csv(location_file)
        loc_df["timestamp"] = pd.to_datetime(loc_df["timestamp"], format="mixed")
        battery_df["timestamp"] = pd.to_datetime(battery_df["timestamp"], format="mixed")
        battery_df = battery_df.sort_values("timestamp")
        loc_df = loc_df.sort_values("timestamp")
        battery_df = pd.merge_asof(
            battery_df,
            loc_df[["timestamp", "latitude", "longitude"]],
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta("2h"),
        )
        print(f"Merged location data ({len(loc_df)} location records)")

    daily_limit = float(os.getenv("API_DAILY_LIMIT", "30"))
    poll_interval = (24 * 60) / daily_limit if daily_limit > 0 else 48.0
    gap_multiplier = float(os.getenv("CHARGING_SESSION_GAP_MULTIPLIER", "1.5"))
    gap_minutes = max(poll_interval * gap_multiplier, 5.0)
    capacity_kwh = float(os.getenv("BATTERY_CAPACITY_KWH", "77.4"))

    print(f"Gap threshold: {gap_minutes:.1f} min, capacity: {capacity_kwh} kWh")

    sessions_df = rebuild_sessions(battery_df, gap_minutes, capacity_kwh)
    print(f"Rebuilt {len(sessions_df)} charging sessions")

    if not sessions_df.empty:
        complete = sessions_df["is_complete"].sum()
        print(f"  Complete: {complete}, Incomplete: {len(sessions_df) - complete}")
        print("\nFirst 5 sessions:")
        print(
            sessions_df.head()[
                ["session_id", "start_battery", "end_battery", "duration_minutes"]
            ].to_string(index=False)
        )
        print("\nLast 5 sessions:")
        print(
            sessions_df.tail()[
                ["session_id", "start_battery", "end_battery", "duration_minutes"]
            ].to_string(index=False)
        )

    if args.preview:
        print("\nPreview mode — no changes written.")
        return

    # Back up existing file
    if sessions_file.exists():
        backup = sessions_file.with_suffix(".csv.bak")
        sessions_file.rename(backup)
        print(f"Backed up existing file to {backup}")

    sessions_df.to_csv(sessions_file, index=False, date_format="%Y-%m-%d %H:%M:%S.%f")
    print(f"Wrote {len(sessions_df)} sessions to {sessions_file}")


if __name__ == "__main__":
    main()
