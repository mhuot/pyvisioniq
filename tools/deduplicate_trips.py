#!/usr/bin/env python3
"""
Deduplicate trips in the trips.csv file
"""
import pandas as pd
from pathlib import Path
import shutil
from datetime import datetime

def deduplicate_trips():
    trips_file = Path("data/trips.csv")
    
    if not trips_file.exists():
        print("No trips.csv file found")
        return
    
    # Backup original file
    backup_path = trips_file.with_suffix('.csv.backup_dedup')
    shutil.copy2(trips_file, backup_path)
    print(f"Created backup at {backup_path}")
    
    # Read trips
    df = pd.read_csv(trips_file)
    original_count = len(df)
    print(f"Original trip count: {original_count}")
    
    # Create unique identifier for each trip
    # Using date, distance, and odometer_start (if available)
    df['trip_id'] = df['date'].astype(str) + '_' + df['distance'].astype(str)
    
    # Add odometer_start to trip_id if it exists and is not null
    mask = df['odometer_start'].notna()
    df.loc[mask, 'trip_id'] = df.loc[mask, 'trip_id'] + '_' + df.loc[mask, 'odometer_start'].astype(str)
    
    # Sort by timestamp to keep the most recent version of each trip
    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
    df = df.sort_values('timestamp', ascending=False)
    
    # Drop duplicates, keeping the first (most recent) occurrence
    df_dedup = df.drop_duplicates(subset=['trip_id'], keep='first')
    
    # Remove the temporary trip_id column
    df_dedup = df_dedup.drop('trip_id', axis=1)
    
    # Sort by date descending for better display
    df_dedup = df_dedup.sort_values(['date', 'timestamp'], ascending=[False, False])
    
    deduplicated_count = len(df_dedup)
    removed_count = original_count - deduplicated_count
    
    print(f"Deduplicated trip count: {deduplicated_count}")
    print(f"Removed {removed_count} duplicate trips")
    
    # Write back to file
    df_dedup.to_csv(trips_file, index=False)
    print(f"Updated {trips_file}")
    
    # Show sample of remaining trips
    print("\nSample of deduplicated trips:")
    print(df_dedup[['timestamp', 'date', 'distance', 'odometer_start']].head(10))

if __name__ == "__main__":
    deduplicate_trips()