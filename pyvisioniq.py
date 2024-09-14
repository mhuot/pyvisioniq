'''This script can be used to collect, export to prom and view the data here'''
import time
import os
import io
import sys
from datetime import datetime, timedelta, timezone
from threading import Thread
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
from flask import Flask, render_template, Response
from prometheus_client import Gauge, generate_latest
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import folium
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV_VARS = ["BLUELINKUSER", "BLUELINKPASS", "BLUELINKPIN",
                    "BLUELINKREGION", "BLUELINKBRAND", "BLUELINKVID"]

env_vars = {name: os.getenv(name) for name in REQUIRED_ENV_VARS}

missing_vars = [name for name, value in env_vars.items() if value is None]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

USERNAME = env_vars["BLUELINKUSER"]  # Username used to access the API
PASSWORD = env_vars["BLUELINKPASS"]  # Password used to access the API
PIN = env_vars["BLUELINKPIN"]  # Pin used to access the API
REGION = env_vars["BLUELINKREGION"]  # Region used to access the API
BRAND = env_vars["BLUELINKBRAND"]  # Brand used to access the API
VEHICLE_ID = env_vars["BLUELINKVID"]  # Vehicle ID used to access the API

# Optional environment variables
UPDATE = os.getenv("BLUELINKUPDATE", "False") == "True" # For dry runs
APILIMIT = int(os.getenv("BLUELINKLIMIT", "30")) # Max API requests per 24 hours
PORT = int(os.getenv("BLUELINKPORT", "8001"))
HOST = os.getenv("BLUELINKHOST", '0.0.0.0')
CSV_FILE = os.getenv("BLUELINKCSV", './vehicle_data.csv')

vm = VehicleManager(region=int(REGION),
                    brand=int(BRAND),
                    username=USERNAME,
                    password=PASSWORD,
                    pin=PIN)

# Initialize Flask app
app = Flask(__name__)

# Prometheus metrics
charging_level_gauge = Gauge('vehicle_data_charging_level', 'Charging level')
mileage_gauge = Gauge('vehicle_data_mileage', 'Mileage')
battery_health_gauge = Gauge('vehicle_data_battery_health', 'Battery health percentage')
ev_driving_range_gauge = Gauge('vehicle_data_ev_driving_range', 'Estimated driving range')

# Rate limit variables
interval_between_requests = timedelta(seconds=86400 // APILIMIT)

def fetch_and_update_metrics():
    '''Fetch data from the vehicle API and update Prometheus metrics.'''
    # Refresh the token and update vehicle data
    vehicle = None
    # pylint: disable=broad-exception-caught
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
        print(f"Hyundai/Kia API error: {error}", file=sys.stderr)
    except Exception as unexpected_error:
        print(f"Unexpected error: {unexpected_error}. Investigate further.", file=sys.stderr)

    # except KeyError as key_error:  # Specific exception
    #     print(f"KeyError: {key_error}. API response might be missing data.", file=sys.stderr)
    # except ConnectionError as conn_error:  # Specific exception
    #     print(f"ConnectionError: {conn_error}. Unable to connect to API.", file=sys.stderr)
    # except Exception as general_error:  # General exception (last resort)
    #     print(f"Unexpected error: {general_error}. Check library or API documentation.", file=sys.stderr)

    # Get a TZ aware datetime object for the threashold of updates
    last_updated_threshold = (datetime.now() - interval_between_requests).replace(tzinfo=timezone.utc)

    if vehicle is None or vehicle.last_updated_at < last_updated_threshold:
        print("Cached data is stale, force refreshing...", file=sys.stderr)
        vm.force_refresh_vehicle_state(VEHICLE_ID)
        vehicle = vm.get_vehicle(VEHICLE_ID)  # Get updated vehicle data

    if vehicle is None:
        print("Vehicle data not available after refresh. Skipping update.", file=sys.stderr)
        return  # Exit the function early

    print('Updating...', file=sys.stderr)
    # Fetch the data
    charging_level = vehicle.ev_battery_percentage
    mileage = vehicle.odometer
    battery_health = vehicle.ev_battery_soh_percentage if vehicle.ev_battery_soh_percentage else 0
    ev_driving_range = vehicle.ev_driving_range
    longitude = vehicle.location_longitude
    latitude = vehicle.location_latitude

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
    '''Schedule periodic updates to fetch and
        update vehicle data while adhering to the API rate limits.'''
    while True:
        now = datetime.now()
        next_update = (now + interval_between_requests).replace(second=0, microsecond=0)
        fetch_and_update_metrics()
        sleep_duration = (next_update - datetime.now()).total_seconds()
        time.sleep(max(0, sleep_duration))

def rangeplot():
    '''Generate a plot of the charging level over time.'''
    data = pd.read_csv(CSV_FILE)  # Load data directly from CSV using Pandas

    # Ensure the 'Timestamp' column is correctly parsed as datetime
    data['Timestamp'] = pd.to_datetime(data['Timestamp'], errors='coerce')

    # Set 'Timestamp' as the index
    data.set_index('Timestamp', inplace=True)

    plt.figure(figsize=(10, 6))
    plt.plot(data.index, data['EV Driving Range'], label='EV Driving Range', marker='o', linestyle='-')
    plt.xlabel('Timestamp')
    plt.ylabel('Miles')
    plt.title('EV Driving Range Over Time')
    plt.legend()
    plt.xticks(rotation=45, ha="right")

    # Set the major locator to a reasonable interval
    plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d/%y %H:%M'))
    plt.tight_layout()  # Adjust the layout to prevent clipping

    plt.grid(axis='y')
    fig = plt.gcf()
    plt.close()
    return fig

def chargeplot():
    '''Generate a plot of the charging level over time.'''
    data = pd.read_csv(CSV_FILE)  # Load data directly from CSV using Pandas

    # Ensure the 'Timestamp' column is correctly parsed as datetime
    data['Timestamp'] = pd.to_datetime(data['Timestamp'], errors='coerce')

    # Set 'Timestamp' as the index
    data.set_index('Timestamp', inplace=True)
    plt.figure(figsize=(10, 6))
    plt.plot(data.index, data['Charging Level'], label='Charging Level', marker='o', linestyle='-')
    plt.xlabel('Timestamp')
    plt.ylabel('%')
    plt.title('Charging Level Over Time')
    plt.legend()
    plt.xticks(rotation=45, ha="right")

    # Set the major locator to a reasonable interval
    plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d/%y %H:%M'))
    plt.tight_layout()  # Adjust the layout to prevent clipping

    plt.grid(axis='y')
    fig = plt.gcf()
    plt.close()
    return fig

def mileageplot():
    '''Generate a plot of the mileage over time.'''
    data = pd.read_csv(CSV_FILE)  # Load data directly from CSV using Pandas

    # Ensure the 'Timestamp' column is correctly parsed as datetime
    data['Timestamp'] = pd.to_datetime(data['Timestamp'], errors='coerce')

    # Set 'Timestamp' as the index
    data.set_index('Timestamp', inplace=True)
    plt.figure(figsize=(10,6))
    plt.plot(data.index, data['Mileage'], label='Mileage', marker='x', linestyle='-')
    plt.xlabel('Timestamp')
    plt.ylabel('Miles')
    plt.title('Total Miles')
    plt.legend()
    plt.xticks(rotation=45, ha="right")

    # Set the major locator to a reasonable interval
    plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d/%y %H:%M'))
    plt.tight_layout()  # Adjust the layout to prevent clipping

    plt.grid(axis='y')
    fig = plt.gcf()
    plt.close()
    return fig

def mapit():
    '''Create and save a map visualization of the vehicle's location data.'''
    data = pd.read_csv(CSV_FILE)  # Load data directly from CSV using Pandas
    # data['Timestamp'] = pd.to_datetime(data['Timestamp'])
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

    # my_map.save("ev_map.html")
    return my_map.render()

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

# Update Flask routes
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

if __name__ == "__main__":
    if UPDATE:
    # Start the scheduled update in a separate thread
        update_thread = Thread(target=scheduled_update)
        update_thread.daemon = True
        update_thread.start()
    else:
        print("Not updating.")

    # Start the Flask app
    app.run(host=HOST, port=PORT)
