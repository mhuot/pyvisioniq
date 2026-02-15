# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Virtual Environment
- Must use venv for all Python operations
- Activate with: `source venv/bin/activate`
- If venv has issues, recreate with: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`

## Common Commands

### Running the Application
```bash
# Start both data collector and web server
source venv/bin/activate
python data_collector.py &  # Runs in background, collects data every 48 minutes based on API_DAILY_LIMIT
python -m src.web.app       # Starts Flask web server on port 5000 (or PORT env var)
```

### Docker Operations
```bash
docker compose up -d        # Start all services
docker compose down         # Stop all services
docker compose logs -f      # View logs

# IMPORTANT: Code changes require rebuild since Dockerfile COPYs code
docker compose down && docker compose build && docker compose up -d
```

### Data Management Tools
```bash
# All tools require venv activation first
python tools/reprocess_cache_complete.py    # Rebuild CSV files from cache
python tools/deduplicate_trips_v2.py        # Remove duplicate trips
python tools/fix_charging_sessions.py       # Fix charging session data issues
python tools/migrate_trips_location.py      # Add location data to trips
python tools/add_temperature_columns.py     # Add temperature data to CSVs
python tools/fix_cache_odometer.py          # Fix odometer readings in cache
python tools/add_is_cached_column.py        # Add is_cached column to battery_status.csv
python tools/migrate_csv_to_oracle.py      # Migrate CSV data to Oracle ADB
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
- `/trips` - Trip history with energy consumption breakdown
- `/battery_status` - Battery level, charging status, temperature  
- `/locations` - GPS coordinates history
- `/charging_sessions` - Charging session data
- `/last_update` - Last data collection timestamp
- `/weather/<lat>/<lon>` - Weather data for coordinates
- `/summary/trips` - Trip statistics and summaries
- `/summary/battery` - Battery usage statistics
- `/trips/<trip_id>` - Individual trip details
- `/force-update` - Force cache refresh (respects rate limits)
- `/clear-cache` - Clear non-history cache files
- `/refresh` - Refresh data with timeout handling

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

## Known Issues
- Temperature data may not update correctly from API
- Data validation rejects numpy.int64 types in charging session tracking  
- Virtual environment created on macOS may need recreation on Linux
- SSL certificate issues may require DISABLE_SSL_VERIFY=true