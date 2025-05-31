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
python data_collector.py &  # Runs in background, collects data every 48 minutes
python -m src.web.app       # Starts Flask web server on port 5000
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
```

### Testing
```bash
# Test charging session tracking
python test_charging_session.py
```

## High-Level Architecture

### Core Components Interaction
1. **Data Collector** (`data_collector.py`):
   - Runs continuously in background
   - Fetches vehicle data every 48 minutes
   - Respects 30 API calls/day limit
   - Stores data via CSVStorage

2. **CachedVehicleClient** (`src/api/client.py`):
   - Handles all Hyundai/Kia Connect API communication
   - Implements smart caching with separate validity (45.6 min) and retention (48 hours)
   - Manages authentication and rate limiting
   - Uses SSL patch for certificate issues

3. **CSVStorage** (`src/storage/csv_store.py`):
   - Manages 4 CSV files: trips, battery_status, locations, charging_sessions
   - Handles deduplication automatically
   - Tracks charging sessions with state management
   - Integrates weather data from Open-Meteo API

4. **Flask Web App** (`src/web/app.py`):
   - Serves dashboard at port 5000
   - Provides 12 API endpoints for data access
   - Cache management UI at `/cache`
   - Debug routes available when DEBUG_MODE=true

### Data Flow
```
Hyundai/Kia API → CachedVehicleClient → Cache Files → CSVStorage → Flask API → Web Dashboard
                       ↓                     ↓
                  Rate Limiting          Deduplication
                  SSL Patching          Weather Integration
```

### Key Patterns
- **Caching Strategy**: Two-tier system with validity period for API calls and longer retention for data availability
- **Error Handling**: Comprehensive error classification in API client with retry logic
- **Data Validation**: Debug utilities include DataValidator for type checking (handles numpy types)
- **Session Management**: Charging sessions tracked with incomplete/complete states
- **Docker Deployment**: Code is COPY'd into image, not mounted. Only data/logs/cache are volumes. Rebuild required for code changes.

## Environment Variables
Critical settings in `.env`:
- `BLUELINKVID`: Vehicle ID - obtain from first run if not set
- `API_DAILY_LIMIT`: Default 30, enforced by rate limiter
- `DEBUG_MODE`: Enables verbose logging and debug routes
- `TZ`: Timezone for data collection (default: America/Chicago)

## Known Issues
- Temperature data may not update correctly from API
- Data validation rejects numpy.int64 types in charging session tracking
- Virtual environment created on macOS may need recreation on Linux