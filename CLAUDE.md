# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PyVisionIQ is a Python application that monitors and visualizes Hyundai/Kia electric vehicle data using the Hyundai/Kia Connect API (BlueLink). It collects vehicle metrics, stores them in CSV format, exposes Prometheus metrics, and provides web-based visualizations.

## Common Development Tasks

### Running the Application

```bash
# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the application (requires .env file with credentials)
python pyvisioniq.py
```

### Linting

```bash
# Run pylint with project configuration
pylint pyvisioniq.py

# Run the enhanced version with API monitoring
python pyvisioniq_enhanced.py
```

The project uses a custom `.pylintrc` configuration with:
- Max line length: 120 characters
- Python 3.11 target
- Virtual environment path included in init-hook

### Prometheus Metrics

The enhanced version includes additional Prometheus metrics:
- `pyvisioniq_api_calls_total` - Counter for API calls with status labels
- `pyvisioniq_api_response_seconds` - Histogram for API response times
- `pyvisioniq_api_rate_limit_remaining` - Gauge for remaining daily API calls

### Testing

Currently, there are no automated tests. When implementing tests, check for testing framework in requirements.txt or ask the user for the preferred testing approach.

## Architecture Overview

### Core Components

1. **VehicleManager Class**: Handles authentication and data retrieval from the Hyundai/Kia Connect API
   - Manages API rate limiting (default 30 requests/day)
   - Implements retry logic for API failures
   - Caches authentication tokens

2. **Data Collection**: Background thread (`scheduled_update()`) that:
   - Calculates optimal update intervals based on API limits
   - Fetches vehicle data periodically
   - Stores data in CSV format with timestamps
   - Updates Prometheus metrics

3. **Web Interface**: Flask application serving:
   - `/metrics` - Prometheus metrics endpoint
   - `/map` - Interactive Folium map showing vehicle location
   - `/charge.png` - Battery charge level graph
   - `/mileage.png` - Odometer progression graph
   - `/range.png` - EV range over time graph

### Key Environment Variables

Required in `.env` file:
- `BLUELINKUSER` - API username
- `BLUELINKPASS` - API password  
- `BLUELINKPIN` - API PIN
- `BLUELINKREGION` - Region code
- `BLUELINKBRAND` - Brand code (1=Kia, 2=Hyundai, 3=Genesis)
- `BLUELINKVID` - Vehicle ID

Optional:
- `BLUELINKUPDATE` - Enable data fetching (default: False)
- `BLUELINKLIMIT` - API requests per day (default: 30)
- `BLUELINKPORT` - Flask port (default: 8001)
- `BLUELINKCSV` - CSV file path (default: ./vehicle_data.csv)

### Data Flow

1. API credentials loaded from environment
2. VehicleManager authenticates with API
3. Background thread schedules updates based on rate limits
4. Vehicle data fetched and stored in CSV
5. Prometheus metrics updated
6. Web interface displays visualizations from CSV data

## Development Guidelines

### Important Notes

- **Matplotlib Backend**: Always use `matplotlib.use('Agg')` before importing pyplot to avoid GUI threading issues
- This must be set before any matplotlib imports to prevent macOS threading errors

### API Rate Limiting

The application intelligently manages API rate limits:
- Default: 30 requests/day
- Updates scheduled evenly throughout the day
- Force refresh available via `/api/fetch` endpoint (use sparingly)

### Error Handling

- All API calls wrapped in try/except blocks
- Detailed logging for debugging
- Graceful degradation when API unavailable

### Data Storage

- CSV format: timestamp, charging_level, mileage, battery_health, ev_range, latitude, longitude
- New data appended to existing file
- No automatic cleanup - manual maintenance required for large datasets

### Deployment

For production deployment:
1. Use the provided systemd service file
2. Run as dedicated `pyvisioniq` user
3. Install to `/opt/pyvisioniq`
4. Consider reverse proxy for Flask application