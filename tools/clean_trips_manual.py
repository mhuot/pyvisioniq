#!/usr/bin/env python3
"""
Manually clean trips to keep only detailed entries
"""
import pandas as pd
from pathlib import Path
import shutil

def clean_trips_manual():
    trips_file = Path("data/trips.csv")
    
    if not trips_file.exists():
        print("No trips.csv file found")
        return
    
    # Backup
    shutil.copy2(trips_file, trips_file.with_suffix('.csv.backup_manual'))
    
    # Read trips
    df = pd.read_csv(trips_file)
    print(f"Original count: {len(df)}")
    
    # Keep only entries with detailed data (those that have duration filled)
    detailed_df = df[df['duration'].notna()].copy()
    print(f"Detailed entries: {len(detailed_df)}")
    
    # Save cleaned data
    detailed_df.to_csv(trips_file, index=False)
    print(f"Saved {len(detailed_df)} detailed trips")
    
    print("\nCleaned trips:")
    print(detailed_df[['date', 'distance', 'duration', 'odometer_start']])

if __name__ == "__main__":
    clean_trips_manual()