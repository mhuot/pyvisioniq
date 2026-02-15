#!/usr/bin/env python3
"""
Add is_cached column to existing battery_status.csv file.
All existing data will be marked as is_cached=False since we don't know the original source.
"""

import pandas as pd
from pathlib import Path
import sys
import shutil
from datetime import datetime


def add_is_cached_column():
    """Add is_cached column to battery_status.csv"""
    battery_file = Path("data/battery_status.csv")

    if not battery_file.exists():
        print("No battery_status.csv file found.")
        return

    # Create backup
    backup_file = battery_file.with_suffix(
        ".csv.backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    shutil.copy(battery_file, backup_file)
    print(f"Created backup: {backup_file}")

    # Read the CSV
    try:
        df = pd.read_csv(battery_file, parse_dates=["timestamp"])
        print(f"Loaded {len(df)} battery status records")

        # Check if column already exists
        if "is_cached" in df.columns:
            print("is_cached column already exists!")
            return

        # Add the column - mark all existing data as False (we don't know the original source)
        df["is_cached"] = False

        # Save back to CSV
        df.to_csv(battery_file, index=False)
        print(f"Successfully added is_cached column to {len(df)} records")
        print("All existing data marked as is_cached=False")

    except Exception as e:
        print(f"Error processing battery_status.csv: {e}")
        print(f"Restoring from backup...")
        shutil.copy(backup_file, battery_file)
        sys.exit(1)


if __name__ == "__main__":
    print("Adding is_cached column to battery_status.csv...")
    add_is_cached_column()
    print("Done!")
