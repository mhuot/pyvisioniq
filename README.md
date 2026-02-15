# PyVisionic

A comprehensive Python web application for monitoring and visualizing Hyundai/Kia Connect API data with focus on battery performance, trip analysis, and energy efficiency tracking.

## Features

### Core Functionality
- **Smart API Management**: Automatic rate limiting (30 calls/day) with dynamic cache validity
- **Real-time Vehicle Monitoring**: Battery level, range, temperature, and location tracking
- **Historical Data Storage**: CSV-based storage with automatic deduplication, optional Oracle ADB backend
- **Oracle ADB Integration**: Dual-write mode for Microsoft Fabric Data Gateway / Power BI
- **Docker Support**: Full containerization with docker-compose

### Web Dashboard
- **Interactive Charts**: 
  - Battery level with temperature correlation
  - Energy consumption breakdown by component (drivetrain, climate, accessories)
  - Trip efficiency trends over time
- **Efficiency Statistics**: 
  - Miles per kWh (mi/kWh) calculations
  - Time-based averages (day/week/month/year)
  - Best/worst/average efficiency tracking
- **Trip Management**:
  - Detailed trip history with clickable rows
  - Energy usage breakdowns per trip
  - Speed profiles and regeneration data
- **Map Integration**:
  - Interactive maps showing all trip endpoints
  - Individual trip route visualization
  - Current vehicle location display

### Cache Management
- **Web-based Cache UI**: View, download, and delete cached API responses
- **Force Update**: Manual cache refresh capability
- **Smart Caching**: Separate validity (45.6 min) and retention (48 hours) periods

### Data Tools
- **Migration Scripts**: Update data formats and fix historical data
- **Deduplication**: Remove duplicate trips automatically
- **Cache Reprocessing**: Rebuild CSV files from cached data

## Setup

### Using Docker (Recommended)

1. Clone the repository:
```bash
git clone https://github.com/yourusername/pyvisionic.git
cd pyvisionic
```

2. Create `.env` file with your credentials:
```bash
# Create .env file with your Hyundai/Kia Connect credentials
cat > .env << EOF
BLUELINKUSER=your_username
BLUELINKPASS=your_password
BLUELINKREGION=3  # 1=USA, 2=Canada, 3=Europe
BLUELINKBRAND=2   # 1=Hyundai, 2=Kia, 3=Genesis
EOF
```

3. Run with docker-compose:
```bash
docker compose up -d
```

The web interface will be available at http://localhost:5000

**Note**: After code changes, rebuild the Docker image:
```bash
docker compose down && docker compose build && docker compose up -d
```

### Manual Setup

1. Create and activate virtual environment:
```bash
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
# Create .env file with your credentials
cat > .env << EOF
BLUELINKUSER=your_username
BLUELINKPASS=your_password
BLUELINKREGION=3  # 1=USA, 2=Canada, 3=Europe
BLUELINKBRAND=2   # 1=Hyundai, 2=Kia, 3=Genesis
EOF
```

4. Run the application:
```bash
python data_collector.py &  # Start background data collector (runs every 48 minutes)
python -m src.web.app       # Start Flask web server on port 5000
```

## Oracle Autonomous Database (Optional)

PyVisionIQ supports Oracle Autonomous Database as a storage backend, enabling integration with Microsoft Fabric Data Gateway for Power BI dashboards. The default CSV backend continues to work without any Oracle configuration.

### Storage Modes

| Mode | `STORAGE_BACKEND` | Description |
|------|-------------------|-------------|
| CSV only | `csv` (default) | Original behaviour — flat files in `data/` |
| Oracle only | `oracle` | All reads/writes go to Oracle ADB |
| Dual-write | `dual` | Writes to **both** CSV and Oracle; reads from CSV by default |

### Oracle Setup

1. Provision an Always Free Autonomous Transaction Processing (ATP) database in OCI console
2. Download the wallet zip and extract to `oracle_wallet/` in the project root
3. Add Oracle environment variables to `.env`:
```bash
STORAGE_BACKEND=dual
ORACLE_USER=pyvisioniq
ORACLE_PASSWORD=your_oracle_password
ORACLE_DSN=pyvisioniq_low        # TNS name from wallet
ORACLE_WALLET_LOCATION=./oracle_wallet
ORACLE_WALLET_PASSWORD=your_wallet_password
```
4. Migrate existing CSV data to Oracle:
```bash
source venv/bin/activate
python tools/migrate_csv_to_oracle.py --dry-run   # Preview first
python tools/migrate_csv_to_oracle.py              # Execute migration
```

Tables are created automatically on first connection. Uses `python-oracledb` in thin mode (no Oracle Instant Client required).

### Fabric Data Gateway Integration

Once Oracle is populated, connect Microsoft Fabric via the On-Premises Data Gateway:
1. Install the gateway on a machine with Oracle Client
2. Add Oracle data source using the same wallet/TNS config
3. The 4 tables (`trips`, `battery_status`, `locations`, `charging_sessions`) are directly queryable from Fabric/Power BI

## Configuration

### Environment Variables
- `BLUELINKUSER`: Your Hyundai/Kia Connect username
- `BLUELINKPASS`: Your Hyundai/Kia Connect password
- `BLUELINKPIN`: Your Hyundai/Kia Connect PIN (optional, only needed for remote commands)
- `BLUELINKVID`: Your vehicle ID (obtained from first run if not set)
- `BLUELINKREGION`: Region code (1=USA, 2=Canada, 3=Europe)
- `BLUELINKBRAND`: Brand (1=Hyundai, 2=Kia, 3=Genesis)
- `API_DAILY_LIMIT`: API call limit (default: 30)
- `DEBUG_MODE`: Enable verbose logging and debug routes (true/false)
- `TZ`: Timezone for data collection (default: America/Chicago)
- `PORT`: Web server port (default: 5000)
- `STORAGE_BACKEND`: Storage backend — `csv` (default), `oracle`, or `dual`
- `ORACLE_USER`: Oracle ADB username (required for oracle/dual)
- `ORACLE_PASSWORD`: Oracle ADB password (required for oracle/dual)
- `ORACLE_DSN`: Oracle TNS name from wallet (required for oracle/dual)
- `ORACLE_WALLET_LOCATION`: Path to extracted wallet directory
- `ORACLE_WALLET_PASSWORD`: Optional wallet password

## Usage

### Web Interface
- **Dashboard**: View current vehicle status and charts
- **Refresh**: Click "Refresh Data" to fetch latest data (respects API limits)
- **Units**: Toggle between metric and imperial units
- **Trip Details**: Click any trip row to see detailed energy breakdown
- **Cache Management**: Access via `/cache` endpoint

### API Endpoints
- `/api/trips`: Trip history with energy consumption breakdown
- `/api/battery_status`: Battery level, charging status, and temperature
- `/api/locations`: GPS coordinates history
- `/api/charging_sessions`: Charging session data
- `/api/last_update`: Last data collection timestamp
- `/api/weather/<lat>/<lon>`: Weather data for coordinates
- `/api/summary/trips`: Trip summaries and statistics
- `/api/summary/battery`: Battery usage statistics
- `/api/trips/<trip_id>`: Individual trip details
- `/api/force-update`: Force cache refresh
- `/cache/api/files`: List cached files
- `/cache/api/download/<filename>`: Download cached file

### Data Management Tools
Located in `tools/` directory:
- `reprocess_cache_complete.py`: Rebuild CSV files from cache
- `deduplicate_trips_v2.py`: Remove duplicate trip entries
- `migrate_trips_location.py`: Add location data to trips
- `fix_cache_odometer.py`: Fix odometer readings in cache
- `fix_charging_sessions.py`: Fix charging session data issues
- `add_temperature_columns.py`: Add temperature data to CSVs
- `add_charging_power_column.py`: Add charging power data
- `migrate_csv_to_oracle.py`: Migrate CSV data to Oracle ADB (supports `--dry-run`)

## Data Storage

- **Cache Files**: `cache/` directory
  - API responses cached with timestamps
  - Automatic cleanup of old files
- **CSV Files**: `data/` directory
  - `battery_status.csv`: Battery level, charging status, temperature
  - `trips.csv`: Trip records with energy consumption breakdown
  - `locations.csv`: GPS location history
  - `charging_sessions.csv`: Charging session tracking
  - `api_call_history.json`: API call tracking for rate limiting
- **Logs**: `logs/` directory
  - `collector.log`: Data collection logs

## Development

### Project Structure
```
pyvisionic/
├── src/
│   ├── api/          # API client and caching
│   ├── storage/      # Storage backends (CSV, Oracle, dual-write)
│   │   ├── base.py           # Abstract StorageBackend interface
│   │   ├── csv_store.py      # CSV file storage (default)
│   │   ├── oracle_store.py   # Oracle ADB storage
│   │   ├── oracle_schema.py  # Oracle DDL definitions
│   │   ├── dual_store.py     # Dual-write (CSV + Oracle)
│   │   └── factory.py        # Storage factory (reads STORAGE_BACKEND)
│   └── web/          # Flask web application
├── tools/            # Data management scripts
├── docs/             # Architecture documentation
├── data/             # CSV data files
├── cache/            # Cached API responses
├── oracle_wallet/    # Oracle ADB wallet (not in git)
└── logs/             # Application logs
```

### Architecture Documentation
For detailed architectural diagrams and technical documentation, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), which includes:
- System architecture overview
- Data flow diagrams
- Web UI structure
- API endpoint mappings
- Data storage schema

### Adding Features
1. API changes: Modify `src/api/client.py`
2. Storage changes: Update `src/storage/csv_store.py`
3. Web UI changes: Edit `src/web/app.py` and templates
4. New tools: Add scripts to `tools/` directory

## Known Issues
- Temperature data may not update correctly from API
- Data validation rejects numpy.int64 types in charging session tracking
- Virtual environment created on macOS may need recreation on Linux

## Troubleshooting

### Common Issues
- **No data displayed**: Check if data collector is running
- **API errors**: Verify credentials in `.env` file
- **Missing vehicle ID**: Run once without BLUELINKVID to see available vehicles
- **Cache issues**: Use cache management UI to clear old files

### Debug Mode
Set `DEBUG_MODE=true` in `.env` for:
- Verbose logging to console and files
- Debug endpoints at `/debug/*`
- Error tracking in `debug/` directory
- Detailed error messages in web UI

## License

MIT License - See LICENSE file for details

## Acknowledgments

Built with:
- [hyundai-kia-connect-api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api) for vehicle connectivity
- Flask for web framework
- Chart.js for interactive charts
- Leaflet.js for mapping
- Docker for containerization