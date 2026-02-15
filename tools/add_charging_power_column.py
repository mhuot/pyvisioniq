#!/usr/bin/env python3
"""
Add charging_power column to existing battery_status.csv file
"""

import csv
import sys
from pathlib import Path


def add_charging_power_column():
    battery_file = Path("data/battery_status.csv")

    if not battery_file.exists():
        print("battery_status.csv not found!")
        return

    # Read existing data
    with open(battery_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        existing_fieldnames = reader.fieldnames

    # Check if charging_power already exists
    if "charging_power" in existing_fieldnames:
        print("charging_power column already exists!")
        return

    # Create new fieldnames with charging_power after is_charging
    fieldnames = []
    for field in existing_fieldnames:
        fieldnames.append(field)
        if field == "is_charging":
            fieldnames.append("charging_power")

    # Backup original file
    backup_file = battery_file.with_suffix(".csv.bak")
    battery_file.rename(backup_file)
    print(f"Backed up original file to {backup_file}")

    # Write updated CSV with new column
    with open(battery_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            # Add empty charging_power field
            row["charging_power"] = ""
            writer.writerow(row)

    print(f"Successfully added charging_power column to {battery_file}")
    print(f"Total rows updated: {len(rows)}")


if __name__ == "__main__":
    add_charging_power_column()
