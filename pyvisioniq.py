'''PyVisionIQ with secure credential storage, API tracking, and dashboard'''
import time
import os
import io
import sys
from datetime import datetime, timedelta
from threading import Thread, Lock
from collections import deque
from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.exceptions import (
    AuthenticationError,
    APIError,
    RateLimitingError,
    NoDataFound,
    ServiceTemporaryUnavailable,
    DuplicateRequestError,
    RequestTimeoutError,
    InvalidAPIResponseError,
)
from flask import Flask, render_template, Response, jsonify, request, send_file
from prometheus_client import Gauge, Counter, Histogram, generate_latest
import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import folium
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from dotenv import load_dotenv
from secure_config import SecureConfig

# Try to load from .env first for backward compatibility
load_dotenv()

# Initialize secure configuration
secure_config = SecureConfig()

# Load configuration
config_loaded = False
config_data = {}

# Try to load from secure storage first
if secure_config.exists():
    try:
        config_data = secure_config.load_config()
        if config_data:
            config_loaded = True
            print("Loaded configuration from secure storage", file=sys.stderr)
    except Exception as e:
        print(f"Failed to load secure config: {e}", file=sys.stderr)

# Fall back to environment variables if secure config not available
if not config_loaded:
    print("No secure configuration found, checking environment variables...", file=sys.stderr)
    config_data = secure_config.initialize_from_env()

    if all(key in config_data for key in ['username', 'password', 'pin', 'region', 'brand', 'vehicle_id']):
        print("Using configuration from environment variables", file=sys.stderr)
        # Optionally save to secure storage
        try:
            if os.isatty(sys.stdin.fileno()):  # Only prompt if running interactively
                if input("Save credentials to secure storage? (y/n): ").lower() == 'y':
                    secure_config.save_config(config_data)
        except:
            pass  # Skip if not running interactively
    else:
        print("Missing required configuration. Please run:", file=sys.stderr)
        print("  python secure_config.py init --from-env", file=sys.stderr)
        print("or", file=sys.stderr)
        print("  python secure_config.py init", file=sys.stderr)
        sys.exit(1)

# Extract configuration values
USERNAME = config_data.get('username')
PASSWORD = config_data.get('password')
PIN = config_data.get('pin')
REGION = str(config_data.get('region', ''))
BRAND = str(config_data.get('brand', ''))
VEHICLE_ID = config_data.get('vehicle_id')

# Optional configuration with defaults
UPDATE = config_data.get('auto_update', False)
APILIMIT = config_data.get('api_limit', 30)
PORT = config_data.get('port', 8001)
HOST = config_data.get('host', '0.0.0.0')
CSV_FILE = config_data.get('csv_file', './vehicle_data.csv')

# Validate required configuration
required_config = {
    'username': USERNAME,
    'password': PASSWORD,
    'pin': PIN,
    'region': REGION,
    'brand': BRAND,
    'vehicle_id': VEHICLE_ID
}

missing_config = [k for k, v in required_config.items() if not v]
if missing_config:
    print(f"Missing required configuration: {', '.join(missing_config)}", file=sys.stderr)
    print("Please run: python secure_config.py init", file=sys.stderr)
    sys.exit(1)

# API tracking variables
api_stats = {
    'total_calls': 0,
    'success_count': 0,
    'error_count': 0,
    'daily_calls': 0,
    'last_reset': datetime.now().date(),
    'response_times': deque(maxlen=100),
    'recent_calls': deque(maxlen=20)
}
api_stats_lock = Lock()

# Settings that can be changed at runtime
runtime_settings = {
    'update_enabled': UPDATE,
    'api_limit': APILIMIT
}
settings_lock = Lock()

# Latest vehicle data cache
latest_vehicle_data = {
    'battery_level': 0,
    'ev_range': 0,
    'mileage': 0,
    'last_update': 'Never',
    'last_update_time': None,  # Store actual datetime
    'battery_health': 0,
    'location': {'lat': 0, 'lon': 0},
    'raw_data': None  # Store raw API response
}
vehicle_data_lock = Lock()

vm = VehicleManager(region=int(REGION),
                    brand=int(BRAND),
                    username=USERNAME,
                    password=PASSWORD,
                    pin=PIN)

# Initialize Flask app
app = Flask(__name__)

# Prometheus metrics - Vehicle data
charging_level_gauge = Gauge('vehicle_data_charging_level', 'Charging level')
mileage_gauge = Gauge('vehicle_data_mileage', 'Mileage')
battery_health_gauge = Gauge('vehicle_data_battery_health', 'Battery health percentage')
ev_driving_range_gauge = Gauge('vehicle_data_ev_driving_range', 'Estimated driving range')

# Prometheus metrics - API monitoring
api_calls_total = Counter('pyvisioniq_api_calls_total', 'Total API calls', ['status'])
api_response_time = Histogram('pyvisioniq_api_response_seconds', 'API response time')
api_rate_limit_remaining = Gauge('pyvisioniq_api_rate_limit_remaining', 'Remaining API calls')

# Rate limit variables
def get_interval_between_requests():
    with settings_lock:
        return timedelta(seconds=86400 // runtime_settings['api_limit'])

def track_api_call(endpoint, success, response_time=None, error=None):
    '''Track API call statistics'''
    with api_stats_lock:
        # Reset daily counter if it's a new day
        if api_stats['last_reset'] != datetime.now().date():
            api_stats['daily_calls'] = 0
            api_stats['last_reset'] = datetime.now().date()

        api_stats['total_calls'] += 1
        api_stats['daily_calls'] += 1

        if success:
            api_stats['success_count'] += 1
            api_calls_total.labels(status='success').inc()
        else:
            api_stats['error_count'] += 1
            api_calls_total.labels(status='error').inc()

        if response_time:
            api_stats['response_times'].append(response_time)
            # Convert milliseconds to seconds for Prometheus
            api_response_time.observe(response_time / 1000.0)

        # Update rate limit remaining metric
        with settings_lock:
            remaining = runtime_settings['api_limit'] - api_stats['daily_calls']
            api_rate_limit_remaining.set(max(0, remaining))

        # Add to recent calls
        api_stats['recent_calls'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'endpoint': endpoint,
            'success': success,
            'response_time': response_time or 0,
            'error': str(error) if error else None
        })

def fetch_and_update_metrics():
    '''Fetch data from the vehicle API and update Prometheus metrics.'''
    start_time = time.time()
    vehicle = None
    error_occurred = None

    try:
        vm.check_and_refresh_token()
        vm.update_vehicle_with_cached_state(VEHICLE_ID)
        vehicle = vm.get_vehicle(VEHICLE_ID)

    except (
        KeyError,
        ConnectionError,
        AuthenticationError,
        APIError,
        RateLimitingError,
        NoDataFound,
        ServiceTemporaryUnavailable,
        DuplicateRequestError,
        RequestTimeoutError,
        InvalidAPIResponseError,
    ) as error:
        error_occurred = error
        print(f"Hyundai/Kia API error: {error}", file=sys.stderr)
    except Exception as unexpected_error:
        error_occurred = unexpected_error
        print(f"Unexpected error: {unexpected_error}. Investigate further.", file=sys.stderr)

    # Track the initial API call
    response_time = int((time.time() - start_time) * 1000)
    track_api_call('update_vehicle', vehicle is not None, response_time, error_occurred)

    # Check if data is stale - handle both timezone-aware and naive datetimes
    if vehicle is None:
        needs_refresh = True
    else:
        try:
            # Get current time in the same timezone as vehicle.last_updated_at
            if vehicle.last_updated_at.tzinfo is not None:
                # Vehicle timestamp is timezone-aware
                from datetime import timezone
                current_time = datetime.now(timezone.utc)
            else:
                # Vehicle timestamp is timezone-naive
                current_time = datetime.now()

            needs_refresh = vehicle.last_updated_at < current_time - get_interval_between_requests()
        except Exception as e:
            print(f"Error comparing timestamps: {e}, forcing refresh", file=sys.stderr)
            needs_refresh = True

    if needs_refresh:
        print("Cached data is stale, force refreshing...", file=sys.stderr)
        refresh_start = time.time()
        try:
            vm.force_refresh_vehicle_state(VEHICLE_ID)
            vehicle = vm.get_vehicle(VEHICLE_ID)
            refresh_time = int((time.time() - refresh_start) * 1000)
            track_api_call('force_refresh', True, refresh_time)
        except Exception as e:
            refresh_time = int((time.time() - refresh_start) * 1000)
            track_api_call('force_refresh', False, refresh_time, e)
            print(f"Force refresh failed: {e}", file=sys.stderr)

    if vehicle is None:
        print("Vehicle data not available after refresh. Skipping update.", file=sys.stderr)
        return

    print('Updating...', file=sys.stderr)
    # Fetch the data
    charging_level = vehicle.ev_battery_percentage
    mileage = vehicle.odometer
    battery_health = vehicle.ev_battery_soh_percentage if vehicle.ev_battery_soh_percentage else 0
    ev_driving_range = vehicle.ev_driving_range
    longitude = vehicle.location_longitude
    latitude = vehicle.location_latitude

    # Update cached vehicle data
    with vehicle_data_lock:
        latest_vehicle_data['battery_level'] = charging_level
        latest_vehicle_data['ev_range'] = ev_driving_range
        latest_vehicle_data['mileage'] = mileage
        latest_vehicle_data['battery_health'] = battery_health
        latest_vehicle_data['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        latest_vehicle_data['last_update_time'] = datetime.now()  # Store actual datetime
        latest_vehicle_data['location'] = {'lat': latitude, 'lon': longitude}

        # Store raw vehicle data (convert to dict to make it JSON serializable)
        try:
            # Get all available attributes from the vehicle object
            raw_data = {}
            for attr in dir(vehicle):
                if not attr.startswith('_'):
                    try:
                        value = getattr(vehicle, attr)
                        # Skip methods and non-serializable objects
                        if not callable(value) and isinstance(value, (str, int, float, bool, list, dict, type(None))):
                            raw_data[attr] = value
                        elif hasattr(value, 'isoformat'):  # Handle datetime objects
                            raw_data[attr] = value.isoformat()
                    except:
                        pass
            latest_vehicle_data['raw_data'] = raw_data
        except Exception as e:
            print(f"Error extracting raw data: {e}", file=sys.stderr)
            latest_vehicle_data['raw_data'] = {"error": "Failed to extract raw data"}

    # Update Prometheus metrics
    charging_level_gauge.set(charging_level)
    mileage_gauge.set(mileage)
    battery_health_gauge.set(battery_health)
    ev_driving_range_gauge.set(ev_driving_range)

    data_to_log = pd.DataFrame({
        'Timestamp': [datetime.now().isoformat()],
        'Charging Level': [charging_level],
        'Mileage': [mileage],
        'Battery Health': [battery_health],
        'EV Driving Range': [ev_driving_range],
        'Longitude': [longitude],
        'Latitude': [latitude]
    })

    # Write to CSV with header only if it's the first time
    if not os.path.exists(CSV_FILE):
        data_to_log.to_csv(CSV_FILE, index=False)
    else:
        data_to_log.to_csv(CSV_FILE, mode='a', header=False, index=False)

    print(f"{datetime.now().isoformat()}," +
          f"Charging Level: {charging_level}%, " +
          f"Mileage: {mileage} miles, " +
          f"Battery Health: {battery_health}%," +
          f"EV Driving Range: {ev_driving_range} miles," +
          f"long: {longitude}, lat: {latitude}", file=sys.stderr)

def scheduled_update():
    '''Schedule periodic updates to fetch and update vehicle data while adhering to the API rate limits.'''
    # Check if we need to wait before first update based on last CSV update
    with vehicle_data_lock:
        last_update_time = latest_vehicle_data.get('last_update_time')

    if last_update_time:
        interval = get_interval_between_requests()
        next_update = last_update_time + interval
        wait_time = (next_update - datetime.now()).total_seconds()

        if wait_time > 0:
            print(f"Waiting {wait_time:.0f} seconds until next scheduled update "
                  f"based on last CSV entry", file=sys.stderr)
            time.sleep(wait_time)

    while True:
        with settings_lock:
            if not runtime_settings['update_enabled']:
                time.sleep(60)  # Check every minute if updates are re-enabled
                continue

        now = datetime.now()
        interval = get_interval_between_requests()
        next_update = (now + interval).replace(second=0, microsecond=0)
        fetch_and_update_metrics()
        sleep_duration = (next_update - datetime.now()).total_seconds()
        time.sleep(max(0, sleep_duration))

def rangeplot():
    '''Generate a plot of the charging level over time.'''
    # Check if CSV file exists
    if not os.path.exists(CSV_FILE):
        plt.figure(figsize=(10, 6))
        plt.text(0.5, 0.5, 'No data available yet\nTrigger a data refresh first',
                ha='center', va='center', fontsize=16, color='gray')
        plt.xlim(0, 1)
        plt.ylim(0, 1)
        plt.axis('off')
        fig = plt.gcf()
        plt.close()
        return fig

    data = pd.read_csv(CSV_FILE)
    data['Timestamp'] = pd.to_datetime(data['Timestamp'], errors='coerce')
    data.set_index('Timestamp', inplace=True)

    plt.figure(figsize=(10, 6))
    plt.plot(data.index, data['EV Driving Range'], label='EV Driving Range', marker='o', linestyle='-')
    plt.xlabel('Timestamp')
    plt.ylabel('Miles')
    plt.title('EV Driving Range Over Time')
    plt.legend()
    plt.xticks(rotation=45, ha="right")
    plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d/%y %H:%M'))
    plt.tight_layout()
    plt.grid(axis='y')
    fig = plt.gcf()
    plt.close()
    return fig

def chargeplot():
    '''Generate a plot of the charging level over time.'''
    # Check if CSV file exists
    if not os.path.exists(CSV_FILE):
        plt.figure(figsize=(10, 6))
        plt.text(0.5, 0.5, 'No data available yet\nTrigger a data refresh first',
                ha='center', va='center', fontsize=16, color='gray')
        plt.xlim(0, 1)
        plt.ylim(0, 1)
        plt.axis('off')
        fig = plt.gcf()
        plt.close()
        return fig

    data = pd.read_csv(CSV_FILE)
    data['Timestamp'] = pd.to_datetime(data['Timestamp'], errors='coerce')
    data.set_index('Timestamp', inplace=True)

    plt.figure(figsize=(10, 6))
    plt.plot(data.index, data['Charging Level'], label='Charging Level', marker='o', linestyle='-')
    plt.xlabel('Timestamp')
    plt.ylabel('%')
    plt.title('Charging Level Over Time')
    plt.legend()
    plt.xticks(rotation=45, ha="right")
    plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d/%y %H:%M'))
    plt.tight_layout()
    plt.grid(axis='y')
    fig = plt.gcf()
    plt.close()
    return fig

def mileageplot():
    '''Generate a plot of the mileage over time.'''
    # Check if CSV file exists
    if not os.path.exists(CSV_FILE):
        plt.figure(figsize=(10, 6))
        plt.text(0.5, 0.5, 'No data available yet\nTrigger a data refresh first',
                ha='center', va='center', fontsize=16, color='gray')
        plt.xlim(0, 1)
        plt.ylim(0, 1)
        plt.axis('off')
        fig = plt.gcf()
        plt.close()
        return fig

    data = pd.read_csv(CSV_FILE)
    data['Timestamp'] = pd.to_datetime(data['Timestamp'], errors='coerce')
    data.set_index('Timestamp', inplace=True)

    plt.figure(figsize=(10,6))
    plt.plot(data.index, data['Mileage'], label='Mileage', marker='x', linestyle='-')
    plt.xlabel('Timestamp')
    plt.ylabel('Miles')
    plt.title('Total Miles')
    plt.legend()
    plt.xticks(rotation=45, ha="right")
    plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d/%y %H:%M'))
    plt.tight_layout()
    plt.grid(axis='y')
    fig = plt.gcf()
    plt.close()
    return fig

def mapit():
    '''Create and save a map visualization of the vehicle's location data.'''
    # Check if CSV file exists
    if not os.path.exists(CSV_FILE):
        # Return a simple message if no data
        return ("<p style='text-align: center; padding: 2rem; color: #666;'>"
                "No location data available yet. Trigger a data refresh first.</p>")

    try:
        data = pd.read_csv(CSV_FILE)

        # Check if we have location data
        if data.empty or 'Latitude' not in data.columns or 'Longitude' not in data.columns:
            return "<p style='text-align: center; padding: 2rem; color: #666;'>No location data available.</p>"

        map_center = [data['Latitude'].mean(), data['Longitude'].mean()]
        my_map = folium.Map(location=map_center, zoom_start=12)

        for _, row in data.iterrows():
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=5,
                color="blue",
                fill=True,
                fill_color="blue",
                fill_opacity=0.7,
                popup=f"Charging Level: {row['Charging Level']}%, Mileage: {row['Mileage']} miles"
            ).add_to(my_map)

        return my_map._repr_html_()
    except Exception as e:
        return f"<p style='text-align: center; padding: 2rem; color: #666;'>Error loading map: {str(e)}</p>"

def mileage_png():
    '''Generate and return a PNG image of the mileage plot.'''
    fig = mileageplot()
    output = io.BytesIO()
    FigureCanvas(fig).print_png(output)
    return Response(output.getvalue(), mimetype='image/png')

def range_png():
    '''Generate and return a PNG image of the range level plot.'''
    fig = rangeplot()
    output = io.BytesIO()
    FigureCanvas(fig).print_png(output)
    return Response(output.getvalue(), mimetype='image/png')

def charge_png():
    '''Generate and return a PNG image of the charging level plot.'''
    fig = chargeplot()
    output = io.BytesIO()
    FigureCanvas(fig).print_png(output)
    return Response(output.getvalue(), mimetype='image/png')

# Flask routes
@app.route('/metrics')
def metrics():
    '''Endpoint to expose Prometheus metrics.'''
    return Response(generate_latest(), mimetype='text/plain')

@app.route('/map')
def endpointmap():
    '''Endpoint to render the map visualization.'''
    return render_template('index.html', map=mapit())

@app.route('/mileage.png')
def endpointmileage():
    '''Endpoint to serve the mileage plot as a PNG image.'''
    return mileage_png()

@app.route('/range.png')
def endpointrange():
    '''Endpoint to serve the range level plot as a PNG image.'''
    return range_png()

@app.route('/charge.png')
def endpointcharge():
    '''Endpoint to serve the charge level plot as a PNG image.'''
    return charge_png()

# New dashboard route
@app.route('/')
@app.route('/dashboard')
def dashboard():
    '''Enhanced dashboard with API stats and controls'''
    with vehicle_data_lock:
        vehicle_data = latest_vehicle_data.copy()

    with api_stats_lock:
        stats = api_stats.copy()
        avg_response_time = (sum(stats['response_times']) / len(stats['response_times'])
                             if stats['response_times'] else 0)
        success_rate = (stats['success_count'] / stats['total_calls'] * 100) if stats['total_calls'] > 0 else 100

        # Determine API status
        if stats['daily_calls'] >= runtime_settings['api_limit']:
            api_status = 'warning'
        elif stats['error_count'] > stats['success_count']:
            api_status = 'error'
        else:
            api_status = 'healthy'

    with settings_lock:
        settings = runtime_settings.copy()

    # Calculate next update time
    if settings['update_enabled']:
        interval = get_interval_between_requests()
        last_update_time = vehicle_data.get('last_update_time')

        if last_update_time:
            # Calculate actual next update based on last update
            next_update = last_update_time + interval
            time_until_next = next_update - datetime.now()

            if time_until_next.total_seconds() > 0:
                # Format time remaining
                total_seconds = int(time_until_next.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60

                if hours > 0:
                    next_update_in = f"{hours}h {minutes}m"
                elif minutes > 0:
                    next_update_in = f"{minutes}m {seconds}s"
                else:
                    next_update_in = f"{seconds}s"
            else:
                next_update_in = "Any moment"
        else:
            # No update yet, show interval
            next_update_in = f"{int(interval.total_seconds() / 60)} min"
    else:
        next_update_in = "Disabled"

    # Generate map HTML
    try:
        map_html = mapit()
    except:
        map_html = "<p>No location data available</p>"

    return render_template('dashboard.html',
        # Vehicle data
        battery_level=vehicle_data['battery_level'],
        ev_range=vehicle_data['ev_range'],
        mileage=vehicle_data['mileage'],
        last_update=vehicle_data['last_update'],

        # API stats
        api_status=api_status,
        api_calls_today=stats['daily_calls'],
        api_limit=settings['api_limit'],
        api_success_count=stats['success_count'],
        api_error_count=stats['error_count'],
        api_success_rate=f"{success_rate:.1f}",
        avg_response_time=f"{avg_response_time:.0f}",
        next_update_in=next_update_in,
        recent_api_calls=list(stats['recent_calls'])[-10:],

        # Settings
        update_enabled=settings['update_enabled'],
        csv_path=CSV_FILE,
        region=REGION,
        brand=BRAND,

        # Map
        map_html=map_html
    )

# API endpoints
@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    '''Manually trigger a vehicle data refresh'''
    try:
        # Check rate limit
        with api_stats_lock:
            if api_stats['daily_calls'] >= runtime_settings['api_limit']:
                return jsonify({'success': False, 'error': 'Daily API limit reached'})

        # Perform refresh in a thread to avoid blocking
        thread = Thread(target=fetch_and_update_metrics)
        thread.start()

        return jsonify({'success': True, 'message': 'Refresh initiated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/settings', methods=['POST'])
def api_settings():
    '''Update runtime settings'''
    try:
        data = request.json

        with settings_lock:
            if 'update_enabled' in data:
                runtime_settings['update_enabled'] = data['update_enabled'] == 'true'

            if 'api_limit' in data:
                new_limit = int(data['api_limit'])
                if 1 <= new_limit <= 100:
                    runtime_settings['api_limit'] = new_limit
                else:
                    return jsonify({'success': False, 'error': 'API limit must be between 1 and 100'})

        return jsonify({'success': True, 'message': 'Settings updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/export')
def api_export():
    '''Export vehicle data as CSV'''
    try:
        if not os.path.exists(CSV_FILE):
            return jsonify({'success': False, 'error': 'No data available yet. Please trigger a data refresh first.'})
        return send_file(CSV_FILE, as_attachment=True, download_name='vehicle_data.csv')
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/stats')
def api_stats_endpoint():
    '''Get current API statistics'''
    with api_stats_lock:
        stats = api_stats.copy()

    return jsonify({
        'total_calls': stats['total_calls'],
        'daily_calls': stats['daily_calls'],
        'success_count': stats['success_count'],
        'error_count': stats['error_count'],
        'recent_calls': list(stats['recent_calls'])
    })

@app.route('/api/raw-data')
def api_raw_data():
    '''Get raw vehicle data from the API'''
    with vehicle_data_lock:
        raw_data = latest_vehicle_data.get('raw_data')

    if raw_data is None:
        return jsonify({'success': False, 'error': 'No data available yet. Please trigger a data refresh first.'})

    return jsonify({
        'success': True,
        'last_update': latest_vehicle_data.get('last_update'),
        'data': raw_data
    })

@app.route('/api/update-status')
def api_update_status():
    '''Get current update status and next update time'''
    with vehicle_data_lock:
        last_update_time = latest_vehicle_data.get('last_update_time')

    with settings_lock:
        update_enabled = runtime_settings['update_enabled']

    if update_enabled:
        interval = get_interval_between_requests()

        if last_update_time:
            # Calculate actual next update based on last update
            next_update = last_update_time + interval
            time_until_next = next_update - datetime.now()

            if time_until_next.total_seconds() > 0:
                # Format time remaining
                total_seconds = int(time_until_next.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60

                if hours > 0:
                    next_update_in = f"{hours}h {minutes}m"
                elif minutes > 0:
                    next_update_in = f"{minutes}m {seconds}s"
                else:
                    next_update_in = f"{seconds}s"
            else:
                next_update_in = "Any moment"
        else:
            # No update yet, show interval
            next_update_in = f"{int(interval.total_seconds() / 60)} min"
    else:
        next_update_in = "Disabled"

    return jsonify({
        'success': True,
        'next_update_in': next_update_in,
        'update_enabled': update_enabled,
        'last_update': latest_vehicle_data.get('last_update', 'Never')
    })

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    '''Manage secure configuration'''
    if request.method == 'GET':
        # Return non-sensitive configuration
        safe_config = {}
        for key, value in config_data.items():
            if key not in ('password', 'pin'):
                safe_config[key] = value
            else:
                safe_config[key] = '***'  # Mask sensitive values
        return jsonify({'success': True, 'config': safe_config})

    elif request.method == 'POST':
        try:
            updates = request.json

            # Don't allow changing sensitive data through API without authentication
            sensitive_keys = {'password', 'pin', 'username'}
            if any(key in updates for key in sensitive_keys):
                return jsonify({'success': False, 'error': 'Cannot change sensitive data through API'})

            # Update configuration in memory
            for key, value in updates.items():
                if key in config_data:
                    config_data[key] = value
                    secure_config.set(key, value)

            # Save to secure storage
            secure_config.save_config(secure_config.get_all())

            # Update runtime settings if applicable
            with settings_lock:
                if 'api_limit' in updates:
                    runtime_settings['api_limit'] = int(updates['api_limit'])
                if 'auto_update' in updates:
                    runtime_settings['update_enabled'] = bool(updates['auto_update'])

            # Update global variables if needed
            global APILIMIT, UPDATE, PORT, HOST, CSV_FILE
            if 'api_limit' in updates:
                APILIMIT = int(updates['api_limit'])
            if 'auto_update' in updates:
                UPDATE = bool(updates['auto_update'])
            if 'port' in updates:
                PORT = int(updates['port'])
            if 'host' in updates:
                HOST = updates['host']
            if 'csv_file' in updates:
                CSV_FILE = updates['csv_file']

            return jsonify({'success': True, 'message': 'Configuration updated and saved securely'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

if __name__ == "__main__":
    # Initialize rate limit metric
    with api_stats_lock:
        with settings_lock:
            remaining = runtime_settings['api_limit'] - api_stats['daily_calls']
            api_rate_limit_remaining.set(max(0, remaining))

    # Load last update time from CSV if it exists
    if os.path.exists(CSV_FILE):
        try:
            data = pd.read_csv(CSV_FILE)
            if not data.empty and 'Timestamp' in data.columns:
                # Get the most recent timestamp
                data['Timestamp'] = pd.to_datetime(data['Timestamp'], errors='coerce')
                last_timestamp = data['Timestamp'].max()

                if pd.notna(last_timestamp):
                    with vehicle_data_lock:
                        latest_vehicle_data['last_update_time'] = last_timestamp.to_pydatetime()
                        latest_vehicle_data['last_update'] = last_timestamp.strftime('%Y-%m-%d %H:%M:%S')

                    print(f"Loaded last update time from CSV: {last_timestamp}", file=sys.stderr)

                    # Also load the latest vehicle data
                    last_row = data.loc[data['Timestamp'].idxmax()]
                    with vehicle_data_lock:
                        latest_vehicle_data['battery_level'] = last_row.get('Charging Level', 0)
                        latest_vehicle_data['ev_range'] = last_row.get('EV Driving Range', 0)
                        latest_vehicle_data['mileage'] = last_row.get('Mileage', 0)
                        latest_vehicle_data['battery_health'] = last_row.get('Battery Health', 0)
                        latest_vehicle_data['location'] = {
                            'lat': last_row.get('Latitude', 0),
                            'lon': last_row.get('Longitude', 0)
                        }
        except Exception as e:
            print(f"Error loading last update from CSV: {e}", file=sys.stderr)

    if UPDATE:
        # Start the scheduled update in a separate thread
        update_thread = Thread(target=scheduled_update)
        update_thread.daemon = True
        update_thread.start()
    else:
        print("Not updating. Enable updates from the dashboard.")

    # Start the Flask app
    app.run(host=HOST, port=PORT, debug=False)
