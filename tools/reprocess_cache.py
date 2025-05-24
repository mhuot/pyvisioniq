#!/usr/bin/env python3
"""
Reprocess the most recent cached API response
"""
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).parent.parent))

from src.storage.csv_store import CSVStorage
from dotenv import load_dotenv

load_dotenv()

def reprocess_latest_cache():
    cache_dir = Path("cache")
    storage = CSVStorage()
    
    # Find the most recent cache file
    cache_files = list(cache_dir.glob("*.json"))
    if not cache_files:
        print("No cache files found")
        return False
    
    # Sort by modification time and get the most recent
    latest_cache = max(cache_files, key=lambda p: p.stat().st_mtime)
    print(f"Processing cache file: {latest_cache.name}")
    
    try:
        with open(latest_cache, 'r') as f:
            data = json.load(f)
        
        # Fix range if it's missing but available in raw data
        if data.get('battery', {}).get('range') is None:
            ev_status = data.get('raw_data', {}).get('vehicleStatus', {}).get('evStatus', {})
            drv_distance = ev_status.get('drvDistance', [])
            if drv_distance and len(drv_distance) > 0:
                range_data = drv_distance[0].get('rangeByFuel', {}).get('totalAvailableRange', {})
                ev_range = range_data.get('value')
                # Unit 3 appears to be miles, convert to km
                if ev_range and range_data.get('unit') == 3:
                    ev_range = round(ev_range * 1.60934)  # Convert miles to km
                    if 'battery' not in data:
                        data['battery'] = {}
                    data['battery']['range'] = ev_range
                    print(f"Fixed range: {range_data.get('value')} miles -> {ev_range} km")
        
        # Fix odometer from miles to km (US region)
        odometer_miles = data.get('odometer')
        if odometer_miles:
            # Check if it's likely in miles (US region typically uses miles)
            vehicle_details = data.get('raw_data', {}).get('vehicleDetails', {})
            if vehicle_details or odometer_miles < 50000:  # Likely miles if under 50k
                odometer_km = round(odometer_miles * 1.60934)
                data['odometer'] = odometer_km
                print(f"Fixed odometer: {odometer_miles} miles -> {odometer_km} km")
        
        # Process detailed trip data if available
        ev_trip_details = data.get('raw_data', {}).get('evTripDetails', {})
        if ev_trip_details and 'tripdetails' in ev_trip_details:
            print("\nProcessing detailed trip data...")
            trip_details = ev_trip_details.get('tripdetails', [])
            
            # Replace basic trips with detailed ones
            detailed_trips = []
            for trip in trip_details:
                # Convert units
                avg_speed_mph = trip.get('avgspeed', {}).get('value', 0)
                max_speed_mph = trip.get('maxspeed', {}).get('value', 0)
                duration_sec = trip.get('duration', {}).get('value', 0)
                driving_time_sec = trip.get('mileagetime', {}).get('value', 0)
                
                detailed_trips.append({
                    "date": trip.get('startdate'),
                    "distance": trip.get('distance'),
                    "duration": round(duration_sec / 60) if duration_sec else None,
                    "average_speed": round(avg_speed_mph * 1.60934) if avg_speed_mph else None,
                    "max_speed": round(max_speed_mph * 1.60934) if max_speed_mph else None,
                    "idle_time": round((duration_sec - driving_time_sec) / 60) if duration_sec and driving_time_sec else None,
                    "trips_count": 1,
                    "total_consumed": trip.get('totalused'),
                    "regenerated_energy": trip.get('regen'),
                    "accessories_consumed": trip.get('accessories'),
                    "climate_consumed": trip.get('climate'),
                    "drivetrain_consumed": trip.get('drivetrain'),
                    "battery_care_consumed": trip.get('batterycare'),
                    "odometer_start": trip.get('odometer', {}).get('value')
                })
            
            data['trips'] = detailed_trips
            print(f"Processed {len(detailed_trips)} trips with detailed energy data")
        
        # Store in CSV files
        storage.store_vehicle_data(data)
        
        # Print summary
        battery_info = data.get('battery', {})
        temp_f = data.get('raw_data', {}).get('airTemp', {}).get('value')
        
        print(f"Data reprocessed successfully:")
        print(f"  Battery: {battery_info.get('level', 'N/A')}%")
        print(f"  Range: {battery_info.get('range', 'N/A')}km")
        print(f"  Temperature: {temp_f}°F" if temp_f else "  Temperature: N/A")
        print(f"  Timestamp: {data.get('timestamp', 'Unknown')}")
        
        return True
        
    except Exception as e:
        print(f"Error reprocessing cache: {e}")
        return False

if __name__ == "__main__":
    success = reprocess_latest_cache()
    sys.exit(0 if success else 1)