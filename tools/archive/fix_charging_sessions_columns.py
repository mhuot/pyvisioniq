#!/usr/bin/env python3
"""
Fix charging sessions CSV column order mismatch.
The header and data rows have different field orders, causing data corruption.
"""

import sys
from pathlib import Path

import pandas as pd

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))


def fix_charging_sessions():
    """Fix the charging sessions CSV by reordering columns to match actual data."""
    csv_file = Path("data/charging_sessions.csv")

    if not csv_file.exists():
        print("charging_sessions.csv not found")
        return

    # Read with the ACTUAL field order (what the data is)
    actual_fieldnames = [
        "session_id",
        "start_time",
        "end_time",
        "start_battery",
        "end_battery",
        "energy_added",
        "avg_power",
        "max_power",
        "location_lat",
        "location_lon",
        "is_complete",
        "duration_minutes",
    ]

    print(f"Reading {csv_file}...")
    df = pd.read_csv(csv_file, names=actual_fieldnames, skiprows=1)

    print(f"Found {len(df)} sessions")
    print(f"\nSample of data BEFORE fix:")
    print(
        df.tail(3)[
            [
                "session_id",
                "start_battery",
                "location_lat",
                "location_lon",
                "duration_minutes",
            ]
        ]
    )

    # The correct field order (what we want)
    correct_fieldnames = [
        "session_id",
        "start_time",
        "end_time",
        "duration_minutes",
        "start_battery",
        "end_battery",
        "energy_added",
        "avg_power",
        "max_power",
        "location_lat",
        "location_lon",
        "is_complete",
    ]

    # Reorder columns
    df = df[correct_fieldnames]

    print(f"\nSample of data AFTER fix:")
    print(
        df.tail(3)[
            [
                "session_id",
                "start_battery",
                "location_lat",
                "location_lon",
                "duration_minutes",
            ]
        ]
    )

    # Backup original
    backup_file = csv_file.with_suffix(".csv.backup")
    print(f"\nBacking up original to {backup_file}")
    csv_file.rename(backup_file)

    # Write corrected file
    print(f"Writing corrected file to {csv_file}")
    df.to_csv(csv_file, index=False)

    print("\n✓ Charging sessions CSV fixed successfully!")
    print("  - Columns reordered to match header")
    print(f"  - Backup saved to {backup_file}")


if __name__ == "__main__":
    fix_charging_sessions()
