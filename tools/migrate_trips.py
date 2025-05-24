#!/usr/bin/env python3
"""
Migrate existing trips data to include new energy breakdown fields
"""
import csv
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

def migrate_trips_csv():
    """Add new columns to existing trips.csv file"""
    trips_file = Path("data/trips.csv")
    trips_backup = Path("data/trips_backup.csv")
    
    if not trips_file.exists():
        print("No trips.csv file found")
        return False
    
    # Create backup
    if trips_backup.exists():
        trips_backup.unlink()
    trips_file.rename(trips_backup)
    print(f"Backed up existing trips to {trips_backup}")
    
    # Read existing data
    existing_data = []
    with open(trips_backup, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        existing_fieldnames = reader.fieldnames
        for row in reader:
            existing_data.append(row)
    
    # Define new fieldnames
    new_fieldnames = [
        'timestamp', 'date', 'distance', 'duration', 
        'average_speed', 'max_speed', 'idle_time', 'trips_count',
        'total_consumed', 'regenerated_energy',
        'accessories_consumed', 'climate_consumed', 'drivetrain_consumed',
        'battery_care_consumed', 'odometer_start'
    ]
    
    # Write updated data with new columns
    with open(trips_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=new_fieldnames)
        writer.writeheader()
        
        for row in existing_data:
            # Add new fields with empty values
            for field in new_fieldnames:
                if field not in row:
                    row[field] = ''
            writer.writerow(row)
    
    print(f"Migrated {len(existing_data)} trips to new format")
    print(f"Added columns: {', '.join([f for f in new_fieldnames if f not in existing_fieldnames])}")
    return True

def reprocess_cached_trips():
    """Check if we can extract detailed trip data from cached API responses"""
    cache_dir = Path("cache")
    found_details = False
    
    for cache_file in cache_dir.glob("*.json"):
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
            
            # Check for detailed trip data
            ev_trip_details = data.get('raw_data', {}).get('evTripDetails', {})
            if ev_trip_details and 'tripdetails' in ev_trip_details:
                print(f"\nFound detailed trip data in {cache_file.name}")
                trip_details = ev_trip_details['tripdetails']
                print(f"  Contains {len(trip_details)} detailed trips")
                
                # Show sample of available data
                if trip_details:
                    sample = trip_details[0]
                    print(f"  Sample trip data:")
                    print(f"    Date: {sample.get('startdate')}")
                    print(f"    Drivetrain: {sample.get('drivetrain')} Wh")
                    print(f"    Climate: {sample.get('climate')} Wh")
                    print(f"    Accessories: {sample.get('accessories')} Wh")
                    print(f"    Battery care: {sample.get('batterycare')} Wh")
                    found_details = True
                    
        except Exception as e:
            continue
    
    if found_details:
        print("\nDetailed trip data is available in cache!")
        print("Run 'python tools/reprocess_cache.py' to reprocess with new trip details")
    else:
        print("\nNo detailed trip data found in cache")
        print("New data will be collected with next API call")

if __name__ == "__main__":
    print("Migrating trips data to new format...")
    if migrate_trips_csv():
        print("\nChecking for cached detailed trip data...")
        reprocess_cached_trips()
    else:
        print("Migration failed")