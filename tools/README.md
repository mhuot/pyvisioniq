# PyVisionic Data Management Tools

Utility scripts for managing and maintaining PyVisionic data.

## Active Tools

### reprocess_cache_complete.py
Rebuilds all CSV data files from cached API responses. **Recovery tool.**

```bash
python tools/reprocess_cache_complete.py
```

**When to use:** After updating parsing logic, recovering from corrupted CSVs, or applying new transformations to historical data.

### deduplicate_trips_v2.py
Removes duplicate trip entries from trips.csv.

```bash
python tools/deduplicate_trips_v2.py
```

**When to use:** Duplicate trips in dashboard, after manual CSV edits, or as regular maintenance. Identifies duplicates by date + distance + odometer.

### rebuild_sessions_from_battery.py
Reconstructs charging sessions from battery_status.csv history.

```bash
python tools/rebuild_sessions_from_battery.py             # Full rebuild
python tools/rebuild_sessions_from_battery.py --preview    # Dry run
```

**When to use:** When charging_sessions.csv is corrupted or incomplete. Replays battery history to detect charge start/stop transitions, merges location data, and calculates energy/power.

### rebuild_charging_sessions.py
Merges fragmented charging sessions using smart gap detection.

```bash
python tools/rebuild_charging_sessions.py
```

**When to use:** When sessions are split into too many fragments. Uses `CHARGING_SESSION_GAP_MULTIPLIER` to merge consecutive short-gap sessions.

### recompute_is_cached.py
Replays cache history to recompute freshness flags.

```bash
python tools/recompute_is_cached.py                # Dry run
python tools/recompute_is_cached.py --write-cache  # Persist changes
```

**When to use:** After major cache changes or to audit historical freshness. Compares timestamps and payload hashes to determine if data was truly fresh from Hyundai.

### migrate_csv_to_oracle.py
Bulk migrates CSV data to Oracle Autonomous Database.

```bash
python tools/migrate_csv_to_oracle.py              # Full migration
python tools/migrate_csv_to_oracle.py --dry-run    # Preview only
python tools/migrate_csv_to_oracle.py --batch-size 500
```

**When to use:** Setting up Oracle backend or re-syncing Oracle from CSV source of truth.

### analyze_errors.py
Analyzes error patterns from cached API error files.

```bash
python tools/analyze_errors.py
```

**When to use:** Debugging API rate limiting, understanding error patterns by time of day, or investigating connectivity issues.

## Archived Tools

One-time migration and fix scripts that have already been applied are in `tools/archive/`. These are kept for reference but should not need to be run again:

- `add_is_cached_column.py` — Added is_cached column to battery_status.csv
- `add_temperature_columns.py` — Split temperature into meteo_temp/vehicle_temp
- `add_charging_power_column.py` — Added charging_power column
- `migrate_trips_location.py` — Added location columns to trips.csv
- `fix_charging_sessions.py` — Superseded by rebuild_sessions_from_battery.py
- `fix_charging_sessions_columns.py` — One-time CSV column order fix
- `fix_cache_odometer.py` — One-time miles-to-km conversion in cache
- `csv_to_postgres.py` — PostgreSQL migration (project uses Oracle)

## Usage Guidelines

### Before Running Any Tool

1. **Stop the data collector** — Ensure data_collector.py is not running
2. **Backup your data** — Tools create automatic backups, but manual backups are recommended
3. **Activate venv** — `source venv/bin/activate`

### Running Tools

From the project root:

```bash
python tools/[tool_name].py
```

In Docker:

```bash
docker compose exec pyvisionic python tools/[tool_name].py
```

### After Running Tools

1. Verify results in the web interface
2. Check tool output for errors
3. Restart data collector and web server if needed
