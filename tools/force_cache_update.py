#!/usr/bin/env python3
"""
Force a cache update by making a single API call and saving the result.
This helps minimize API calls during debugging.
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from src.api.client import CachedVehicleClient
import json
from datetime import datetime

def main():
    print("=== Force Cache Update Tool ===")
    print("This will make ONE API call and save the result to cache.")
    print()
    
    # Create client with cache disabled to force API call
    client = CachedVehicleClient()
    
    # Force cache update
    print("Making API call...")
    data = client.force_cache_update()
    
    if data:
        print("\n✓ Cache update successful!")
        print(f"  - Vehicle ID: {data.get('vehicle_id')}")
        print(f"  - Battery Level: {data.get('battery', {}).get('level')}%")
        print(f"  - Range: {data.get('battery', {}).get('range')} km")
        print(f"  - Odometer: {data.get('odometer')} km")
        
        # Save a copy for inspection
        debug_file = Path("cache/debug_last_api_response.json")
        with open(debug_file, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"\n  Debug copy saved to: {debug_file}")
        
    else:
        print("\n✗ Cache update failed!")
        print("  Check the logs for error details.")
        
        # Check for recent error files
        cache_dir = Path("cache")
        error_files = sorted(cache_dir.glob("error_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if error_files:
            print(f"\n  Recent error file: {error_files[0]}")
            with open(error_files[0], 'r') as f:
                error_data = json.load(f)
                print(f"  Error: {error_data.get('error_message')}")

if __name__ == "__main__":
    main()