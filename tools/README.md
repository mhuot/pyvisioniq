# PyVisionic Data Management Tools

This directory contains utility scripts for managing and maintaining PyVisionic data.

## Available Tools

### reprocess_cache_complete.py
Rebuilds all CSV data files from cached API responses.

**Usage:**
```bash
python tools/reprocess_cache_complete.py
```

**When to use:**
- After updating data parsing logic
- To recover from corrupted CSV files
- To apply new data transformations to historical data

**What it does:**
1. Backs up existing CSV files
2. Reads all cache files in chronological order
3. Rebuilds battery_status.csv, trips.csv, and locations.csv
4. Removes duplicate entries automatically

### deduplicate_trips.py
Removes duplicate trip entries from the trips CSV file.

**Usage:**
```bash
python tools/deduplicate_trips.py
```

**When to use:**
- If you notice duplicate trips in the dashboard
- After manual CSV edits
- As part of regular maintenance

**What it does:**
1. Creates a backup of trips.csv
2. Identifies duplicates by date, distance, and odometer
3. Keeps the first occurrence of each unique trip
4. Reports number of duplicates removed

### migrate_trips_location.py
Adds location data (latitude/longitude) to existing trips.

**Usage:**
```bash
python tools/migrate_trips_location.py
```

**When to use:**
- After enabling location tracking
- To backfill location data for historical trips
- When location data is missing from trips

**What it does:**
1. Reads location data from cache files
2. Matches locations to trips by timestamp
3. Updates trips.csv with end_latitude and end_longitude
4. Preserves all existing trip data

### fix_cache_odometer.py
Fixes odometer readings in cached API responses.

**Usage:**
```bash
python tools/fix_cache_odometer.py
```

**When to use:**
- If odometer values are incorrect in cache
- After identifying odometer parsing issues
- Before reprocessing cache files

**What it does:**
1. Backs up existing cache files
2. Reads each cache file and extracts correct odometer value
3. Updates the odometer field in the cache
4. Maintains all other data unchanged

## General Guidelines

### Before Running Any Tool

1. **Stop the data collector**: Ensure data_collector.py is not running
2. **Backup your data**: Tools create automatic backups, but manual backups are recommended
3. **Check disk space**: Ensure sufficient space for backups

### Running Tools

All tools should be run from the project root directory:

```bash
cd /path/to/pyvisionic
python tools/[tool_name].py
```

If using the Docker setup, run inside the container:

```bash
docker compose exec pyvisionic python tools/[tool_name].py
```

### After Running Tools

1. **Verify results**: Check that data looks correct in the web interface
2. **Check logs**: Look for any error messages in the tool output
3. **Restart services**: Restart data collector and web server if needed

## Creating New Tools

When creating new data management tools:

1. **Always create backups**: Before modifying any data files
2. **Use existing modules**: Import from src.storage and src.api
3. **Add logging**: Include progress messages and error handling
4. **Test thoroughly**: Verify on a copy of data first
5. **Document usage**: Update this README with the new tool

## Data File Formats

### battery_status.csv
```
timestamp,battery_level,is_charging,remaining_time,range,temperature,odometer
```

### trips.csv
```
timestamp,date,distance,duration,average_speed,max_speed,idle_time,trips_count,
total_consumed,regenerated_energy,accessories_consumed,climate_consumed,
drivetrain_consumed,battery_care_consumed,odometer_start,end_latitude,
end_longitude,end_temperature
```

### locations.csv
```
timestamp,latitude,longitude,last_updated
```

## Troubleshooting

### Common Issues

**"No such file or directory"**
- Ensure you're running from the project root
- Check that data/ and cache/ directories exist

**"Permission denied"**
- Check file ownership if using Docker
- Run with appropriate permissions

**"Module not found"**
- Activate virtual environment first
- Or run inside Docker container

### Getting Help

If you encounter issues:
1. Check the tool's error output
2. Verify data file formats match expected structure
3. Review recent changes to data collection
4. Check PyVisionic logs for related errors