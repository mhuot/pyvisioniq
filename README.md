# PyVisionic

A comprehensive Python web application for monitoring and visualizing Hyundai/Kia Connect API data with focus on battery performance, trip analysis, and energy efficiency tracking.

## Features

### Core Functionality
- **Smart API Management**: Automatic rate limiting (30 calls/day) with dynamic cache validity
- **Real-time Vehicle Monitoring**: Battery level, range, temperature, and location tracking
- **Historical Data Storage**: CSV-based storage with automatic deduplication
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
cp .env.example .env
# Edit .env with your Hyundai/Kia Connect credentials
```

3. Run with docker-compose:
```bash
docker compose up -d
```

The web interface will be available at http://localhost:5000

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
cp .env.example .env
# Edit .env with your credentials
```

4. Run the application:
```bash
python data_collector.py &  # Start background data collector
python -m src.web.app       # Start web server
```

## Configuration

### Environment Variables
- `BLUELINKUSER`: Your Hyundai/Kia Connect username
- `BLUELINKPASS`: Your Hyundai/Kia Connect password
- `BLUELINKPIN`: Your Hyundai/Kia Connect PIN
- `BLUELINKVID`: Your vehicle ID (obtained from first run)
- `BLUELINKREGION`: Region code (1=USA, 2=Canada, 3=Europe)
- `BLUELINKBRAND`: Brand (1=Hyundai, 2=Kia, 3=Genesis)
- `API_DAILY_LIMIT`: API call limit (default: 30)
- `CACHE_DURATION_HOURS`: Cache retention time (default: 48)
- `PORT`: Web server port (default: 5000)

## Usage

### Web Interface
- **Dashboard**: View current vehicle status and charts
- **Refresh**: Click "Refresh Data" to fetch latest data (respects API limits)
- **Units**: Toggle between metric and imperial units
- **Trip Details**: Click any trip row to see detailed energy breakdown
- **Cache Management**: Access via `/cache` endpoint

### API Endpoints
- `/api/current-status`: Current vehicle data
- `/api/battery-history`: Historical battery data
- `/api/trips`: Trip history
- `/api/efficiency-stats`: Efficiency statistics
- `/api/locations`: All trip locations for mapping
- `/cache/api/files`: List cached files
- `/cache/api/force-update`: Force cache refresh

### Data Management Tools
Located in `tools/` directory:
- `reprocess_cache_complete.py`: Rebuild CSV files from cache
- `deduplicate_trips_v2.py`: Remove duplicate trip entries
- `migrate_trips_location.py`: Add location data to trips
- `fix_cache_odometer.py`: Fix odometer readings in cache

## Data Storage

- **Cache Files**: `cache/` directory
  - API responses cached with timestamps
  - Automatic cleanup of old files
- **CSV Files**: `data/` directory
  - `battery_status.csv`: Battery history
  - `trips.csv`: Trip records
  - `locations.csv`: Location history
- **Logs**: `logs/` directory
  - `collector.log`: Data collection logs

## Development

### Project Structure
```
pyvisionic/
├── src/
│   ├── api/          # API client and caching
│   ├── storage/      # CSV data storage
│   └── web/          # Flask web application
├── tools/            # Data management scripts
├── data/             # CSV data files
├── cache/            # Cached API responses
└── logs/             # Application logs
```

### Adding Features
1. API changes: Modify `src/api/client.py`
2. Storage changes: Update `src/storage/csv_store.py`
3. Web UI changes: Edit `src/web/app.py` and templates
4. New tools: Add scripts to `tools/` directory

## Known issues
- The temperature doesn't seem to be updated correctly

## Troubleshooting

### Common Issues
- **No data displayed**: Check if data collector is running
- **API errors**: Verify credentials in `.env` file
- **Missing vehicle ID**: Run once without BLUELINKVID to see available vehicles
- **Cache issues**: Use cache management UI to clear old files

### Debug Mode
Set `FLASK_ENV=development` in `.env` for detailed error messages

## License

MIT License - See LICENSE file for details

## Acknowledgments

Built with:
- [hyundai-kia-connect-api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api) for vehicle connectivity
- Flask for web framework
- Plotly.js for interactive charts
- Leaflet.js for mapping
- Docker for containerization