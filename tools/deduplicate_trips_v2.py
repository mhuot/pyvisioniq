#!/usr/bin/env python3
"""
Remove duplicate trips from trips.csv
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))


def main():
    trips_file = Path("data/trips.csv")

    if not trips_file.exists():
        print("No trips.csv file found")
        return

    # Read trips
    df = pd.read_csv(trips_file)
    original_count = len(df)

    print(f"Original trips count: {original_count}")

    # Create unique identifier for each trip
    df["trip_id"] = df.apply(
        lambda row: f"{row['date']}_{row['distance']}_{row.get('odometer_start', '')}",
        axis=1,
    )

    # Sort by timestamp to keep the most recent version of each trip
    df = df.sort_values("timestamp", ascending=False)

    # Remove duplicates, keeping the first (most recent) occurrence
    df_unique = df.drop_duplicates(subset=["trip_id"], keep="first")

    # Remove the temporary trip_id column
    df_unique = df_unique.drop("trip_id", axis=1)

    # Sort by date descending for better viewing
    df_unique = df_unique.sort_values("date", ascending=False)

    unique_count = len(df_unique)
    removed_count = original_count - unique_count

    print(f"Unique trips count: {unique_count}")
    print(f"Removed {removed_count} duplicate trips")

    if removed_count > 0:
        # Backup original file
        backup_file = trips_file.with_suffix(".csv.backup_duplicates")
        df.to_csv(backup_file, index=False)
        print(f"Original file backed up to: {backup_file}")

        # Save deduplicated data
        df_unique.to_csv(trips_file, index=False)
        print(f"Saved deduplicated trips to: {trips_file}")
    else:
        print("No duplicates found")


if __name__ == "__main__":
    main()
