#!/usr/bin/env python3
"""Merge fragmented charging sessions using the current gap configuration.

This script loads charging history via CSVStorage, applies the dynamic gap
threshold that derives from API_DAILY_LIMIT and CHARGING_SESSION_GAP_MULTIPLIER,
and merges consecutive sessions that should have been treated as one. It then
recomputes duration, energy_added, and avg_power for the merged sessions and
stores the result back in data/charging_sessions.csv.

Run from repo root:
    python3 tools/rebuild_charging_sessions.py
"""

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in __import__('sys').path:
    __import__('sys').path.insert(0, str(ROOT))

from src.storage.csv_store import CSVStorage


@dataclass
class Session:
    session_id: str
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    start_battery: float
    end_battery: float
    energy_added: float
    avg_power: float
    max_power: float
    location_lat: float
    location_lon: float
    is_complete: bool
    duration_minutes: float

    @classmethod
    def from_row(cls, row: pd.Series) -> "Session":
        return cls(
            session_id=str(row['session_id']),
            start_time=row['start_time'],
            end_time=row['end_time'],
            start_battery=float(row['start_battery']) if pd.notna(row['start_battery']) else 0.0,
            end_battery=float(row['end_battery']) if pd.notna(row['end_battery']) else 0.0,
            energy_added=float(row['energy_added']) if pd.notna(row['energy_added']) else 0.0,
            avg_power=float(row['avg_power']) if pd.notna(row['avg_power']) else 0.0,
            max_power=float(row['max_power']) if pd.notna(row['max_power']) else 0.0,
            location_lat=float(row['location_lat']) if pd.notna(row['location_lat']) else None,
            location_lon=float(row['location_lon']) if pd.notna(row['location_lon']) else None,
            is_complete=bool(row['is_complete']),
            duration_minutes=float(row['duration_minutes']) if pd.notna(row['duration_minutes']) else 0.0,
        )

    def recalc_metrics(self, battery_capacity_kwh: float) -> None:
        if pd.isna(self.start_time) or pd.isna(self.end_time):
            self.duration_minutes = 0.0
            self.avg_power = 0.0
            return
        self.duration_minutes = round((self.end_time - self.start_time).total_seconds() / 60.0, 1)
        battery_delta = max(self.end_battery - self.start_battery, 0.0)
        self.energy_added = round((battery_delta / 100.0) * battery_capacity_kwh, 2)
        duration_hours = self.duration_minutes / 60.0
        if duration_hours > 0:
            self.avg_power = round(self.energy_added / duration_hours, 2)
        else:
            self.avg_power = 0.0


def merge_sessions(df: pd.DataFrame, gap_minutes: float, capacity_kwh: float) -> List[Session]:
    sessions: List[Session] = []
    for _, row in df.iterrows():
        session = Session.from_row(row)
        if not sessions:
            sessions.append(session)
            continue

        prev = sessions[-1]
        if (
            prev.is_complete
            and session.is_complete
            and pd.notna(prev.end_time)
            and pd.notna(session.start_time)
        ):
            gap = (session.start_time - prev.end_time).total_seconds() / 60.0
            if gap <= gap_minutes:
                # Merge into previous session
                prev.end_time = max(prev.end_time, session.end_time)
                prev.end_battery = max(prev.end_battery, session.end_battery)
                prev.max_power = max(prev.max_power, session.max_power)
                # Keep existing location
                prev.recalc_metrics(capacity_kwh)
                continue
        sessions.append(session)
    return sessions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview merges without writing out the CSV",
    )
    args = parser.parse_args()

    storage = CSVStorage()
    df = storage.get_charging_sessions_df()
    if df.empty:
        print("No charging sessions found")
        return

    df = df.sort_values('start_time')
    gap_minutes = getattr(storage, 'charging_gap_threshold_minutes', 45.0)
    capacity = getattr(storage, 'battery_capacity_kwh', 77.4)
    print(f"Using gap threshold {gap_minutes:.1f} minutes and capacity {capacity:.2f} kWh")

    merged = merge_sessions(df, gap_minutes, capacity)
    print(f"Merged {len(df)} records down to {len(merged)}")

    merged_df = pd.DataFrame([asdict(s) for s in merged])
    merged_df = merged_df.sort_values('start_time')

    if args.preview:
        print(merged_df.tail())
        return

    merged_df.to_csv(
        storage.charging_sessions_file,
        index=False,
        date_format='%Y-%m-%d %H:%M:%S.%f'
    )
    print(f"Wrote updated sessions to {storage.charging_sessions_file}")


if __name__ == "__main__":
    main()
