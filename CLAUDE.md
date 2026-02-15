# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Virtual Environment
- Must use venv for all Python operations
- Activate with: `source venv/bin/activate`
- If venv has issues, recreate with: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`

## Common Commands

### Running the Application
```bash
# Start both data collector and web server (development)
source venv/bin/activate
python data_collector.py &  # Runs in background, collects data based on API_DAILY_LIMIT (default: every 48 min for 30 calls/day)
python -m src.web.app       # Starts Flask dev server on port 5000 (or PORT env var)

# Production with gunicorn
gunicorn --bind 0.0.0.0:5000 --workers 4 --threads 2 --timeout 120 src.web.app:app

# Test single data collection
python data_collector.py --once
```

### Docker Operations
```bash
docker compose up -d        # Start all services
docker compose down         # Stop all services
docker compose logs -f      # View logs
docker compose logs -f --tail=100  # View last 100 lines

# IMPORTANT: Code changes require rebuild since Dockerfile COPYs code (not mounted)
docker compose down && docker compose build && docker compose up -d

# Access container shell for debugging
docker exec -it pyvisionic sh
```

### Code Quality
```bash
# Format with black (required before commits)
black src/ tools/ data_collector.py

# Lint with pylint (minimum score 8.0 required)
pylint src/ tools/ data_collector.py

# Run security checks
bandit -r src/ tools/ data_collector.py
```

### Data Management Tools
```bash
# All tools require venv activation first
python tools/reprocess_cache_complete.py       # Rebuild CSV files from cache
python tools/deduplicate_trips_v2.py           # Remove duplicate trips
python tools/fix_charging_sessions.py          # Fix charging session data issues
python tools/fix_charging_sessions_columns.py  # Fix charging session column structure
python tools/rebuild_charging_sessions.py      # Rebuild charging sessions from scratch
python tools/migrate_trips_location.py         # Add location data to trips
python tools/add_temperature_columns.py        # Add temperature data to CSVs
python tools/add_charging_power_column.py      # Add charging power column
python tools/fix_cache_odometer.py             # Fix odometer readings in cache
python tools/add_is_cached_column.py           # Add is_cached column to battery_status.csv
python tools/recompute_is_cached.py            # Recompute is_cached flags
python tools/migrate_csv_to_oracle.py          # Migrate CSV data to Oracle ADB
python tools/migrate_csv_to_oracle.py --dry-run  # Preview migration without writing
```

### Testing
```bash
# Test charging session tracking
python test_charging_session.py
```

### Development and Debugging
```bash
# Run with debug mode enabled for verbose logging
DEBUG_MODE=true python -m src.web.app

# Analyze error data (when debug files exist)
python analyze_errors.py    # Processes debug/*.json error files
```

## High-Level Architecture

### Core Components Interaction
1. **Data Collector** (`data_collector.py`):
   - Runs continuously in background
   - Fetches vehicle data based on API_DAILY_LIMIT setting (default 30/day = every 48 min)
   - Manages API rate limiting with call history tracking
   - Stores data via storage backend (CSVStorage, OracleStorage, or DualWriteStorage)

2. **CachedVehicleClient** (`src/api/client.py`):
   - Handles all Hyundai/Kia Connect API communication  
   - Implements smart caching with separate validity and retention periods:
     - Cache validity: Calculated from API_DAILY_LIMIT (e.g., 30 calls/day = 45.6 min validity)
     - Cache retention: Configured via CACHE_DURATION_HOURS (default 48 hours)
   - Manages authentication and error handling with custom APIError class
   - Uses SSL patch for certificate issues when DISABLE_SSL_VERIFY=true

3. **Storage Layer** (`src/storage/`):
   - **StorageBackend** (`base.py`): Abstract base class defining the storage interface
   - **CSVStorage** (`csv_store.py`): CSV file backend (default) — manages 4 CSV files
   - **OracleStorage** (`oracle_store.py`): Oracle Autonomous DB backend with connection pooling
   - **DualWriteStorage** (`dual_store.py`): Writes to both CSV and Oracle simultaneously
   - **Factory** (`factory.py`): `create_storage()` returns the backend per `STORAGE_BACKEND` env var
   - Handles automatic deduplication (CSV via pandas, Oracle via MERGE/UNIQUE constraints)
   - Tracks charging sessions with state management (incomplete/complete)
   - Integrates weather data from Open-Meteo API or vehicle sensor based on WEATHER_SOURCE

4. **Flask Web App** (`src/web/app.py`):
   - Serves dashboard at configured PORT (default 5000)
   - Provides 12+ API endpoints for data access
   - Cache management UI via cache_bp blueprint at `/cache`
   - Debug routes via debug_bp blueprint when DEBUG_MODE=true
   - Handles NaN values for JSON serialization

### Data Flow
```
Hyundai/Kia API → CachedVehicleClient → Cache Files → Storage Factory → Flask API → Web Dashboard
                       ↓                     ↓              ↓
                  Rate Limiting          Deduplication   CSVStorage (default)
                  SSL Patching          Weather         OracleStorage
                  Error Classification  Session Track   DualWriteStorage (CSV+Oracle)
```

### Key Patterns
- **Caching Strategy**: Two-tier system with validity period for API calls and longer retention for data availability
- **Error Handling**: APIError class with error types and user-friendly messages
- **Data Validation**: Debug utilities with DataValidator for type checking (handles numpy types)
- **Session Management**: Charging sessions tracked with start/end times, energy added, location
- **Storage Factory**: `create_storage()` reads `STORAGE_BACKEND` env var (csv/oracle/dual)
- **Dual-Write**: CSV as primary (reads), Oracle as secondary (for Fabric Gateway); Oracle errors don't block
- **Docker Deployment**: Code is COPY'd into image, not mounted. Only data/logs/cache/oracle_wallet are volumes
- **Blueprint Architecture**: Modular routes using Flask blueprints (cache_bp, debug_bp)

### API Endpoints
Flask app provides endpoints at `http://localhost:{PORT}/api/`:
- `/trips` - Trip history with pagination, filtering (page, per_page, start_date, end_date, hours)
- `/trip/<trip_id>` - Individual trip details with energy breakdown
- `/battery-history` - Battery level history with temperature (hours, start_date, end_date params)
- `/current-status` - Current battery, charging, range, odometer, temperature
- `/locations` - GPS coordinates for all trips (supports hours/date filtering)
- `/charging-sessions` - Charging session history (supports hours/date filtering)
- `/efficiency-stats` - Efficiency statistics by time period (day/week/month/year)
- `/temperature-efficiency` - Trip efficiency correlated with temperature
- `/charging-temperature-impact` - Charging power/speed by temperature
- `/collection-status` - Data collection status (calls_today, next_collection)
- `/force-update` - Force cache refresh (respects rate limits)
- `/clear-cache` - Clear non-history cache files
- `/refresh` - Refresh data with timeout handling
- `/debug` - Debug info (config, cache status)

Web UI routes:
- `/` - Main dashboard
- `/cache` - Cache management UI (view/download/delete cached files)
- `/debug/*` - Debug routes (only when DEBUG_MODE=true)

## Environment Variables (.env)
Critical settings that must be configured:
- `BLUELINKVID`: Vehicle ID - obtain from first run if not set
- `BLUELINKUSER`: Hyundai/Kia Connect account username  
- `BLUELINKPASS`: Hyundai/Kia Connect account password
- `BLUELINKPIN`: Account PIN for remote commands
- `BLUELINKREGION`: 1=Europe, 2=Canada, 3=USA, 4=China, 5=Australia
- `BLUELINKBRAND`: 1=Kia, 2=Hyundai, 3=Genesis
- `API_DAILY_LIMIT`: Default 30, used to calculate cache validity
- `CACHE_DURATION_HOURS`: How long to retain cache files (default 48)
- `DEBUG_MODE`: Enables verbose logging and debug routes
- `TZ`: Timezone for data collection (default: America/Chicago)
- `PORT`: Web server port (default: 5000)
- `WEATHER_SOURCE`: "meteo" for Open-Meteo API or "vehicle" for car sensor
- `STORAGE_BACKEND`: Storage backend to use — `csv` (default), `oracle`, or `dual` (CSV + Oracle)
- `DUAL_READ_FROM`: When using dual mode, which backend to read from — `csv` (default) or `oracle`
- `ORACLE_USER`: Oracle ADB username
- `ORACLE_PASSWORD`: Oracle ADB password
- `ORACLE_DSN`: Oracle TNS name from wallet (e.g. `pyvisioniq_low`)
- `ORACLE_WALLET_LOCATION`: Path to extracted wallet directory
- `ORACLE_WALLET_PASSWORD`: Optional wallet password

## Project Structure
```
pyvisionic/
├── src/
│   ├── api/
│   │   ├── client.py          # CachedVehicleClient - API communication, caching, rate limiting
│   │   └── ssl_patch.py       # SSL certificate workarounds
│   ├── storage/
│   │   └── csv_store.py       # CSVStorage - manages 4 CSV files, deduplication, session tracking
│   ├── utils/
│   │   ├── weather.py         # WeatherService - Open-Meteo API integration
│   │   └── debug.py           # DebugLogger, DataValidator - debugging utilities
│   └── web/
│       ├── app.py             # Flask app - main routes, API endpoints
│       ├── cache_routes.py    # Blueprint for /cache routes
│       ├── debug_routes.py    # Blueprint for /debug routes (DEBUG_MODE only)
│       ├── templates/         # HTML templates
│       └── static/            # CSS, JS, images
├── tools/                      # Data migration/repair scripts
├── data/                       # CSV files (trips, battery_status, locations, charging_sessions)
├── cache/                      # Cached API responses (timestamped history files)
├── logs/                       # Application logs
└── data_collector.py           # Background data collector (runs every ~48 min)
```

## Key Implementation Details

### Data Flow for API Calls
1. `data_collector.py` calls `client.get_vehicle_data()` at scheduled intervals
2. `CachedVehicleClient` checks cache validity (based on API_DAILY_LIMIT)
3. If cache invalid: calls Hyundai API, compares timestamps to detect freshness, saves to cache
4. Returns data with `is_cached` and `hyundai_data_fresh` flags
5. `CSVStorage.store_vehicle_data()` processes and saves to 4 CSV files with deduplication

### Charging Session State Machine
- **Start**: is_charging=True + no active session → create new session with is_complete=False
- **Update**: is_charging=True + active session → update end_battery, end_time, energy_added
- **Gap Detection**: time gap > charging_gap_threshold → complete old session, start new one
- **Complete**: is_charging=False + active session → set is_complete=True
- Energy calculation: `(battery_delta / 100) * BATTERY_CAPACITY_KWH`
- Average power: `energy_added / (duration_minutes / 60)`

### Cache Freshness Detection
Two-tier cache system prevents unnecessary API calls:
- **Validity Period** (45.6 min for 30 calls/day): When to make new API call
- **Retention Period** (48 hours): How long to keep historical cache files
- **Freshness Detection**: Compares `api_last_updated` timestamps and raw data hash
- **Stale Detection**: If timestamp unchanged and payload unchanged → mark as not fresh

### Temperature Data Sources
Two sources configured via WEATHER_SOURCE:
- `meteo`: Open-Meteo API (weather service) - more reliable
- `vehicle`: Vehicle sensor from airTemp field - may be stale
Both sources stored in separate columns (meteo_temp, vehicle_temp) for comparison

## Known Issues & Workarounds
- **Temperature stale**: Vehicle sensor may not update; use WEATHER_SOURCE=meteo
- **numpy types**: DataValidator handles numpy.int64 validation to prevent JSON errors
- **vehicleStatus KeyError**: Hyundai API maintenance windows → gracefully use last cache
- **SSL certificate issues**: Set DISABLE_SSL_VERIFY=true (uses ssl_patch.py)
- **Rate limiting**: Automatic backoff extends next collection interval by 1.5x (max 4x)
- **Cross-platform venv**: Recreate venv when moving between macOS and Linux