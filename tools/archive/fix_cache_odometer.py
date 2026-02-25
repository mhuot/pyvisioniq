#!/usr/bin/env python3
"""
Fix odometer values in cache files (convert miles to km)
"""

import json
import shutil
from pathlib import Path


def fix_cache_odometer():
    cache_dir = Path("cache")

    if not cache_dir.exists():
        print("No cache directory found")
        return

    cache_files = list(cache_dir.glob("*.json"))
    cache_files = [f for f in cache_files if not f.name.startswith("error_")]

    for cache_file in cache_files:
        print(f"Processing {cache_file.name}")

        # Backup
        backup_path = cache_file.with_suffix(".json.backup_odometer")
        shutil.copy2(cache_file, backup_path)

        # Read and fix
        with open(cache_file, "r") as f:
            data = json.load(f)

        # Fix main odometer
        if "odometer" in data and isinstance(data["odometer"], (int, float)):
            if 10000 <= data["odometer"] <= 13000:  # Likely unconverted miles
                old_value = data["odometer"]
                data["odometer"] = round(old_value * 1.60934)
                print(f"  Fixed main odometer: {old_value} miles -> {data['odometer']} km")

        # Fix raw data odometers if they exist
        if "raw_data" in data:
            raw_data = data["raw_data"]

            # Check for odometer in various places in raw data
            def fix_odometer_recursive(obj, path=""):
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if key == "odometer" and isinstance(value, (int, float, str)):
                            try:
                                val = float(value)
                                if 10000 <= val <= 13000:
                                    obj[key] = round(val * 1.60934)
                                    print(f"  Fixed {path}.{key}: {val} miles -> {obj[key]} km")
                            except (ValueError, TypeError):
                                pass
                        elif isinstance(value, (dict, list)):
                            fix_odometer_recursive(value, f"{path}.{key}")
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        if isinstance(item, (dict, list)):
                            fix_odometer_recursive(item, f"{path}[{i}]")

            fix_odometer_recursive(raw_data, "raw_data")

        # Save fixed data
        with open(cache_file, "w") as f:
            json.dump(data, f, indent=2)

        print(f"  Updated {cache_file.name}")


if __name__ == "__main__":
    fix_cache_odometer()
