#!/usr/bin/env python3
"""
Reprocess all cache files to update CSV data with latest changes.
This will read all cached data and rebuild the CSV files without duplicates.
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from src.storage.csv_store import CSVStorage


def process_cache_files():
    """Process all cache files and rebuild CSV data."""
    cache_dir = Path("cache")
    storage = CSVStorage()

    print("Starting cache reprocessing...")

    # Backup existing files
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for file in ["battery_status.csv", "trips.csv", "locations.csv"]:
        src = Path(f"data/{file}")
        if src.exists():
            dst = Path(f"data/{file}.backup_{timestamp}")
            src.rename(dst)
            print(f"Backed up {file} to {file}.backup_{timestamp}")

    # Create fresh storage
    storage = CSVStorage()

    # Get all cache files sorted by timestamp
    cache_files = []
    for file in cache_dir.glob("*.json"):
        if (
            file.stem.startswith("history_")
            or file.stem == "72a2f11d47c418e96847e34b5bf75766"
        ):
            # Extract timestamp from filename
            if file.stem.startswith("history_"):
                parts = file.stem.split("_")
                if len(parts) >= 3:
                    timestamp_str = parts[1] + parts[2]  # Combine date and time parts
                    try:
                        timestamp = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                        cache_files.append((timestamp, file))
                    except ValueError:
                        continue
            else:
                # For the main cache file, use its modification time
                timestamp = datetime.fromtimestamp(file.stat().st_mtime)
                cache_files.append((timestamp, file))

    # Sort by timestamp
    cache_files.sort(key=lambda x: x[0])

    print(f"Found {len(cache_files)} cache files to process")

    # Process each cache file in chronological order
    processed_files = 0

    for timestamp, cache_file in cache_files:
        try:
            with open(cache_file, "r") as f:
                data = json.load(f)

            # Use the store_vehicle_data method which handles all data types
            storage.store_vehicle_data(data)
            processed_files += 1

        except Exception as e:
            print(f"Error processing {cache_file}: {e}")
            continue

    print(f"\nProcessing complete!")
    print(f"Processed {processed_files} cache files")

    # Remove duplicates
    print("\nRemoving duplicates...")

    # Remove duplicate battery entries
    battery_df = storage.get_battery_df()
    if not battery_df.empty:
        before = len(battery_df)
        # Keep the first occurrence of each timestamp
        battery_df = battery_df.drop_duplicates(subset=["timestamp"], keep="first")
        after = len(battery_df)
        if before > after:
            battery_df.to_csv(storage.battery_file, index=False)
            print(f"Removed {before - after} duplicate battery entries")

    # Remove duplicate trips
    trips_df = storage.get_trips_df()
    if not trips_df.empty:
        before = len(trips_df)
        # For trips, use date as the unique identifier
        trips_df = trips_df.drop_duplicates(subset=["date"], keep="first")
        after = len(trips_df)
        if before > after:
            trips_df.to_csv(storage.trips_file, index=False)
            print(f"Removed {before - after} duplicate trips")

    # Remove duplicate locations
    locations_df = storage.get_locations_df()
    if not locations_df.empty:
        before = len(locations_df)
        # For locations, use timestamp as unique identifier
        locations_df = locations_df.drop_duplicates(subset=["timestamp"], keep="first")
        after = len(locations_df)
        if before > after:
            locations_df.to_csv(storage.location_file, index=False)
            print(f"Removed {before - after} duplicate location entries")

    print("\nCache reprocessing complete!")


if __name__ == "__main__":
    process_cache_files()
