#!/usr/bin/env python3
"""
Test charging session tracking by simulating charging events
"""

import sys
from pathlib import Path
import pandas as pd
from datetime import datetime

# Add the project root to the Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.storage.csv_store import CSVStorage

def simulate_charging_session():
    """Simulate a charging session to test the tracking logic"""
    storage = CSVStorage()
    
    print("Current charging sessions:")
    sessions_df = storage.get_charging_sessions_df()
    print(f"Found {len(sessions_df)} sessions")
    
    # Simulate starting a charging session
    print("\n--- Simulating charging start ---")
    timestamp1 = datetime.now().isoformat()
    data1 = {
        'battery': {
            'level': 65,
            'is_charging': True,
            'charging_power': 7.2,
            'range': 200
        },
        'location': {
            'latitude': 44.88585,
            'longitude': -93.13727
        },
        'temperature': 20,
        'odometer': 5000
    }
    storage.store_vehicle_data(data1)
    print(f"Stored charging start at {timestamp1}")
    
    # Simulate updates during charging
    print("\n--- Simulating charging update ---")
    import time
    time.sleep(2)  # Small delay
    
    timestamp2 = datetime.now().isoformat()
    data2 = {
        'battery': {
            'level': 68,
            'is_charging': True,
            'charging_power': 7.2,
            'range': 210
        },
        'location': {
            'latitude': 44.88585,
            'longitude': -93.13727
        },
        'temperature': 20,
        'odometer': 5000
    }
    storage.store_vehicle_data(data2)
    print(f"Stored charging update at {timestamp2}")
    
    # Simulate charging end
    print("\n--- Simulating charging end ---")
    time.sleep(2)  # Small delay
    
    timestamp3 = datetime.now().isoformat()
    data3 = {
        'battery': {
            'level': 70,
            'is_charging': False,
            'charging_power': 0,
            'range': 220
        },
        'location': {
            'latitude': 44.88585,
            'longitude': -93.13727
        },
        'temperature': 20,
        'odometer': 5000
    }
    storage.store_vehicle_data(data3)
    print(f"Stored charging end at {timestamp3}")
    
    # Check results
    print("\n--- Checking results ---")
    sessions_df = storage.get_charging_sessions_df()
    print(f"Now have {len(sessions_df)} sessions")
    
    if not sessions_df.empty:
        latest_session = sessions_df.iloc[-1]
        print(f"\nLatest session:")
        print(f"  ID: {latest_session['session_id']}")
        print(f"  Start: {latest_session['start_time']}")
        print(f"  End: {latest_session['end_time']}")
        print(f"  Duration: {latest_session['duration_minutes']} minutes")
        print(f"  Battery: {latest_session['start_battery']}% -> {latest_session['end_battery']}%")
        print(f"  Energy: {latest_session['energy_added']} kWh")
        print(f"  Complete: {latest_session['is_complete']}")

if __name__ == '__main__':
    simulate_charging_session()