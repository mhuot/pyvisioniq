#!/usr/bin/env python3
"""
Fix charging sessions data issues:
1. Remove sessions with missing start_time
2. Recalculate energy_added correctly
3. Recalculate average power based on corrected energy values
"""

import sys
from pathlib import Path

import pandas as pd

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def fix_charging_sessions():
    """Fix issues in charging sessions data"""
    charging_file = project_root / "data" / "charging_sessions.csv"

    if not charging_file.exists():
        print("No charging sessions file found")
        return

    # Read the data
    df = pd.read_csv(charging_file)
    print(f"Original data: {len(df)} sessions")

    # Remove sessions with missing start_time
    before_count = len(df)
    df = df.dropna(subset=["start_time"])
    removed = before_count - len(df)
    if removed > 0:
        print(f"Removed {removed} sessions with missing start_time")

    # Parse dates
    df["start_time"] = pd.to_datetime(df["start_time"], errors="coerce")
    df["end_time"] = pd.to_datetime(df["end_time"], errors="coerce")

    # Recalculate duration
    df["duration_minutes"] = ((df["end_time"] - df["start_time"]).dt.total_seconds() / 60).round(1)

    # Recalculate energy_added correctly
    battery_capacity = 77.4  # kWh for Ioniq 5
    df["battery_diff"] = df["end_battery"] - df["start_battery"]
    df["energy_added"] = (df["battery_diff"] / 100 * battery_capacity).round(2)

    # Remove negative or zero energy sessions (these are incomplete or errors)
    df = df[df["energy_added"] > 0]

    # Recalculate average power
    df["avg_power"] = (df["energy_added"] / (df["duration_minutes"] / 60)).round(2)

    # Clean up
    df = df.drop(columns=["battery_diff"])

    # Sort by start time
    df = df.sort_values("start_time")

    # Save the fixed data
    backup_file = charging_file.with_suffix(".csv.backup")
    print(f"Backing up original to {backup_file}")
    pd.read_csv(charging_file).to_csv(backup_file, index=False)

    df.to_csv(charging_file, index=False)
    print(f"Fixed data: {len(df)} sessions saved")

    # Show summary
    print("\nSummary of fixed sessions:")
    print(f"Average charging duration: {df['duration_minutes'].mean():.1f} minutes")
    print(f"Average energy added: {df['energy_added'].mean():.2f} kWh")
    print(f"Average charging power: {df['avg_power'].mean():.2f} kW")


if __name__ == "__main__":
    fix_charging_sessions()
