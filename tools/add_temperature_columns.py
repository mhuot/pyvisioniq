#!/usr/bin/env python3
"""
Add meteo_temp and vehicle_temp columns to existing battery_status.csv file
"""
import csv
import sys
from pathlib import Path

def add_temperature_columns():
    battery_file = Path("data/battery_status.csv")
    
    if not battery_file.exists():
        print("battery_status.csv not found!")
        return
    
    # Read existing data
    with open(battery_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        existing_fieldnames = reader.fieldnames
    
    # Check if columns already exist
    if 'meteo_temp' in existing_fieldnames and 'vehicle_temp' in existing_fieldnames:
        print("Temperature columns already exist!")
        return
    
    # Create new fieldnames with temperature columns at the end
    fieldnames = list(existing_fieldnames) + ['meteo_temp', 'vehicle_temp']
    
    # Backup original file
    backup_file = battery_file.with_suffix('.csv.bak2')
    battery_file.rename(backup_file)
    print(f"Backed up original file to {backup_file}")
    
    # Write updated CSV with new columns
    with open(battery_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for row in rows:
            # Add empty temperature fields
            row['meteo_temp'] = ''
            row['vehicle_temp'] = ''
            
            # For existing data, copy temperature to vehicle_temp since it came from vehicle
            if row.get('temperature'):
                row['vehicle_temp'] = row['temperature']
            
            writer.writerow(row)
    
    print(f"Successfully added temperature columns to {battery_file}")
    print(f"Total rows updated: {len(rows)}")

if __name__ == "__main__":
    add_temperature_columns()