# PyVisionIQ - Hyundai/Kia Vehicle Data Monitor & Visualization
PyVisionIQ is a Python tool designed to fetch data from the Hyundai/Kia Connect API, log it, and visualize key metrics of your electric vehicle. It features secure credential storage, an advanced dashboard, API monitoring, and Prometheus metrics exposure.

## Features
* **Secure Credential Storage**: Encrypted configuration with master password protection
* **Advanced Dashboard**: Modern web interface with real-time updates
* **Data Collection** regularly fetches:
  * Charging level
  * Mileage
  * Battery health
  * Driving range
  * Location
  * Complete raw API data
* **Data Logging**: Stores fetched data in CSV format with automatic scheduling based on last update
* **Prometheus Metrics**: 
  * Vehicle data metrics
  * API monitoring metrics (calls, response times, rate limits)
* **Visualization**:
  * Interactive dashboard at `/dashboard`
  * Interactive map (Folium) at `/map`
  * Charging level plot at `/charge.png`
  * Mileage plot at `/mileage.png`
  * EV Driving Range plot at `/range.png`
* **API Monitoring**: Track API usage, success rates, and response times
* **Rate Limiting**: Intelligent scheduling that respects API limits
* **Systemd Service**: Easy deployment on Linux
## Prerequisites
* Python 3.x
* Libraries: Install from requirements.txt:

  ```Bash
  pip install -r requirements.txt
  ```
* Hyundai/Kia Connect Account  

## Setup

### Option 1: Secure Configuration (Recommended)
1. Initialize secure configuration:
   ```bash
   # Import from existing .env file
   python secure_config.py init --from-env
   
   # Or create new configuration
   python secure_config.py init
   ```
   You'll be prompted for:
   - Username
   - Password
   - PIN
   - Region code
   - Brand code (1=Kia, 2=Hyundai, 3=Genesis)
   - Vehicle ID
   - Optional settings

2. Run with secure config:
   ```bash
   python pyvisioniq.py
   # Enter master password when prompted
   ```

### Option 2: Environment Variables (Legacy)
1. Create .env file:
   ```
   BLUELINKUSER=your_username
   BLUELINKPASS=your_password
   BLUELINKPIN=your_pin
   BLUELINKREGION=your_region_code
   BLUELINKBRAND=your_brand_code
   BLUELINKVID=your_vehicle_id
   ```
   
2. (Optional) Other Variables:
   ```
   BLUELINKUPDATE=True         # Enable automatic updates
   BLUELINKLIMIT=30           # API requests/day
   BLUELINKPORT=8001          # Flask port
   BLUELINKHOST=0.0.0.0       # Flask host
   BLUELINKCSV=./vehicle_data.csv  # Data file path
   ```
## Installation & Running
1. Clone the repository:
   ```bash
   git clone https://github.com/mhuot/pyvisoniq.git
   cd pyvisioniq
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```
3. Dependencies: 
   ```bash
   pip install -r requirements.txt
   ```
4. Systemd (Linux):
    Copy pyvisioniq.service to /etc/systemd/system/
    ```bash
   sudo systemctl enable pyvisioniq.service
   sudo systemctl start pyvisioniq.service
   ```
5. Accessing Data
   - Dashboard: http://your_host:8001/dashboard
   - Prometheus: http://your_host:8001/metrics
   - Map: http://your_host:8001/map
   - API Endpoints:
     - GET `/api/stats` - API usage statistics
     - GET `/api/raw-data` - Complete vehicle data
     - GET `/api/config` - View configuration
     - POST `/api/refresh` - Manual data refresh
     - POST `/api/settings` - Update settings

## Linting with Pylint

Ensure you have activated the virtual environment before running `pylint`.  

If you encounter import errors with `pylint`, ensure that your environment is correctly set up and that the `.pylintrc` file is present in the project root. 
 
You can add the following to the `.pylintrc` file to include the virtual environment's site-packages:

## Dashboard Features

### Overview Tab
- Real-time vehicle status cards
- Interactive location map
- Quick action buttons

### API Stats Tab
- API usage metrics and history
- Success/failure rates
- Response time tracking
- Next update countdown

### Settings Tab
- Runtime configuration management
- Enable/disable automatic updates
- Adjust API rate limits

### Charts Tab
- Battery charge history
- Mileage progression
- EV range trends

### Raw API Data Tab
- Complete JSON response from Hyundai/Kia API
- Syntax-highlighted display
- Copy-to-clipboard functionality

## Secure Configuration Management

```bash
# View current config (passwords masked)
python secure_config.py show

# Update settings
python secure_config.py set --key api_limit --value 50

# Export to environment variables
python secure_config.py export
```

## Prometheus Metrics

### Vehicle Metrics
- `vehicle_data_charging_level`
- `vehicle_data_mileage` 
- `vehicle_data_battery_health`
- `vehicle_data_ev_driving_range`

### API Monitoring Metrics
- `pyvisioniq_api_calls_total{status="success|error"}`
- `pyvisioniq_api_response_seconds`
- `pyvisioniq_api_rate_limit_remaining`

## Additional Notes
* Updates scheduled based on last CSV entry (survives restarts)
* Real-time countdown to next update
* Automatic page refresh when update is imminent
* Secure credential storage with AES-256 encryption
* Consider using reverse proxy with authentication for production

