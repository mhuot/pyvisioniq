#!/usr/bin/env python3
"""
Fix odometer values that weren't converted from miles to km
"""
import pandas as pd
from pathlib import Path
import shutil

def fix_odometer_values():
    # Fix battery_status.csv
    battery_file = Path("data/battery_status.csv")
    if battery_file.exists():
        print("Fixing battery_status.csv...")
        # Backup
        shutil.copy2(battery_file, battery_file.with_suffix('.csv.backup_odometer'))
        
        # Read and fix
        df = pd.read_csv(battery_file)
        # Values around 11000-12000 are likely unconverted miles
        mask = (df['odometer'] >= 10000) & (df['odometer'] <= 13000)
        original_count = mask.sum()
        df.loc[mask, 'odometer'] = df.loc[mask, 'odometer'] * 1.60934
        df['odometer'] = df['odometer'].round()
        
        # Save
        df.to_csv(battery_file, index=False)
        print(f"Fixed {original_count} odometer values in battery_status.csv")
    
    # Fix trips.csv odometer_start column
    trips_file = Path("data/trips.csv")
    if trips_file.exists():
        print("\nFixing trips.csv...")
        # Backup
        shutil.copy2(trips_file, trips_file.with_suffix('.csv.backup_odometer'))
        
        # Read and fix
        df = pd.read_csv(trips_file)
        if 'odometer_start' in df.columns:
            # Values around 10989-11009 are likely unconverted miles
            mask = df['odometer_start'].notna() & (df['odometer_start'] >= 10000) & (df['odometer_start'] <= 13000)
            original_count = mask.sum()
            df.loc[mask, 'odometer_start'] = df.loc[mask, 'odometer_start'] * 1.60934
            df['odometer_start'] = df['odometer_start'].round()
            
            # Save
            df.to_csv(trips_file, index=False)
            print(f"Fixed {original_count} odometer_start values in trips.csv")

if __name__ == "__main__":
    fix_odometer_values()