#!/usr/bin/env python3
"""
Migrate trips.csv to add location and temperature columns
"""
import pandas as pd
from pathlib import Path
import shutil

def migrate_trips_location():
    trips_file = Path("data/trips.csv")
    
    if not trips_file.exists():
        print("No trips.csv file found")
        return
    
    # Read current trips
    df = pd.read_csv(trips_file)
    
    # Check if columns already exist
    if 'end_latitude' in df.columns:
        print("Location columns already exist in trips.csv")
        return
    
    # Backup original file
    backup_path = trips_file.with_suffix('.csv.backup_location')
    shutil.copy2(trips_file, backup_path)
    print(f"Created backup at {backup_path}")
    
    # Add new columns with null values
    df['end_latitude'] = None
    df['end_longitude'] = None
    df['end_temperature'] = None
    
    # Save updated file
    df.to_csv(trips_file, index=False)
    print(f"Added location and temperature columns to {trips_file}")
    print(f"Total trips: {len(df)}")

if __name__ == "__main__":
    migrate_trips_location()