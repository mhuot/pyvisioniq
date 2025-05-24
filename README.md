# PyVisionic

A Python web application for visualizing Hyundai/Kia Connect API data with focus on battery performance and trip analysis.

## Features

- Vehicle data retrieval with API response caching
- CSV storage for historical data
- Modern web UI with real-time charts
- Battery level vs temperature analysis
- Trip history tracking
- WCAG AAA compliant interface

## Setup

1. Create and activate virtual environment:
```bash
python3.11 -m venv venv
source venv/bin/activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

4. Run the application:
```bash
python run.py
```

The web interface will be available at http://localhost:5000

## Usage

- Click "Refresh Data" to fetch latest vehicle data (limited to 30 API calls per day)
- View current battery status, temperature, and odometer readings
- Analyze battery level vs temperature correlation over time
- Review trip history with distance, duration, and speed metrics

## Data Storage

- API responses are cached in `cache/` directory
- Vehicle data is stored in CSV files in `data/` directory
- Cache duration is 1 hour by default