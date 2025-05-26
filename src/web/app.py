from flask import Flask, render_template, jsonify, Response, send_file, request
import plotly.graph_objs as go
import plotly.utils
import json
import os
from datetime import datetime, timedelta
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent))

from src.api.client import CachedVehicleClient, APIError
from src.storage.csv_store import CSVStorage
from src.web.cache_routes import cache_bp

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key'

# Initialize with error handling
try:
    client = CachedVehicleClient()
    app.config['cache_client'] = client  # Store client in app config for blueprints
except Exception as e:
    print(f"Warning: Failed to initialize API client: {e}")
    client = None
    app.config['cache_client'] = None

storage = CSVStorage()

# Register the cache blueprint
app.register_blueprint(cache_bp)

def clean_nan_values(data):
    """Replace NaN and None values with None for JSON serialization"""
    if isinstance(data, dict):
        return {k: clean_nan_values(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_nan_values(v) for v in data]
    elif isinstance(data, float):
        if np.isnan(data):
            return None
        return data
    elif data is np.nan:
        return None
    else:
        return data

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    # Check if we have a favicon.png to serve
    favicon_path = Path('src/web/static/favicon.png')
    if favicon_path.exists():
        return send_file(favicon_path, mimetype='image/png')
    return '', 204  # No content

@app.route('/api/clear-cache')
def clear_cache():
    """Clear the cache to force fresh API call"""
    try:
        if client:
            cache_files = list(client.cache_dir.glob("*.json"))
            # Only clear non-history files
            cleared = []
            for f in cache_files:
                if not f.name.startswith("history_"):
                    f.unlink()
                    cleared.append(f.name)
            return jsonify({
                "status": "success", 
                "message": f"Cleared {len(cleared)} cache files",
                "files": cleared
            })
        return jsonify({"status": "error", "message": "Client not initialized"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/refresh')
def refresh_data():
    if not client:
        return jsonify({"status": "error", "message": "API client not initialized. Please check your .env configuration."}), 500
    
    try:
        # Add timeout protection
        import signal
        
        def timeout_handler(signum, frame):
            raise TimeoutError("API request timed out")
        
        # Set 30 second timeout
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(30)
        
        try:
            # Force a cache update to get fresh data
            data = client.force_cache_update()
            signal.alarm(0)  # Cancel timeout
            
            if data:
                storage.store_vehicle_data(data)
                
                # Include data freshness info in response
                api_updated = data.get('api_last_updated', 'Unknown')
                if api_updated and api_updated != 'Unknown':
                    try:
                        from datetime import datetime
                        api_time = datetime.fromisoformat(str(api_updated).replace('Z', '+00:00'))
                        age_minutes = int((datetime.now(api_time.tzinfo) - api_time).total_seconds() / 60)
                        freshness_msg = f" (vehicle data from {age_minutes} minutes ago)"
                    except:
                        freshness_msg = ""
                else:
                    freshness_msg = ""
                
                return jsonify({
                    "status": "success", 
                    "message": f"Data refreshed successfully{freshness_msg}"
                })
            else:
                return jsonify({
                    "status": "error", 
                    "message": "Failed to fetch data. The vehicle may be offline or in an area without coverage."
                }), 500
        except TimeoutError:
            signal.alarm(0)  # Cancel timeout
            return jsonify({
                "status": "error", 
                "message": "Request timed out after 30 seconds. The vehicle may be offline or the Hyundai/Kia servers are slow to respond."
            }), 504
            
    except APIError as e:
        signal.alarm(0)  # Cancel timeout if set
        # Use our custom error classification
        status_code = 429 if e.error_type == "rate_limit" else 500
        return jsonify({
            "status": "error", 
            "message": e.message,
            "error_type": e.error_type
        }), status_code
        
    except Exception as e:
        signal.alarm(0)  # Cancel timeout if set
        # Fallback for unexpected errors
        app.logger.error(f"Unexpected error in refresh_data: {type(e).__name__}: {str(e)}")
        return jsonify({
            "status": "error", 
            "message": f"An unexpected error occurred. Please try again later. ({type(e).__name__})"
        }), 500

@app.route('/api/trip/<trip_id>')
def get_trip_detail(trip_id):
    """Get detailed information about a specific trip"""
    try:
        import base64
        import pandas as pd
        
        app.logger.info(f"=== Trip Detail Request ===")
        app.logger.info(f"Raw trip_id: {trip_id}")
        
        trips_df = storage.get_trips_df()
        
        if trips_df.empty:
            app.logger.error("No trips found in database")
            return jsonify({"error": "No trips found"}), 404
        
        app.logger.info(f"Total trips in database: {len(trips_df)}")
        
        # Trip ID is composed of date_distance_odometer
        parts = trip_id.split('_')
        app.logger.info(f"Trip ID parts: {parts}")
        
        if len(parts) < 2:
            app.logger.error(f"Invalid trip ID format: {parts}")
            return jsonify({"error": "Invalid trip ID"}), 400
        
        # Decode the base64 encoded date
        try:
            # Add padding if needed
            encoded_date = parts[0]
            padding = 4 - (len(encoded_date) % 4)
            if padding != 4:
                encoded_date += '=' * padding
            date_str = base64.b64decode(encoded_date).decode('utf-8')
            app.logger.info(f"Decoded date from base64: '{date_str}'")
        except Exception as e:
            # Fallback to old format if decoding fails
            date_str = parts[0]
            app.logger.warning(f"Base64 decode failed: {e}, using raw date: '{date_str}'")
        
        distance = float(parts[1])
        odometer = float(parts[2]) if len(parts) > 2 and parts[2] else None
        
        app.logger.info(f"Parsed values - date: '{date_str}', distance: {distance}, odometer: {odometer}")
        
        # Find the trip - handle various date formats
        # The date might come as "2025-05-25T10:05:49" (from JSON) but CSV has "2025-05-25 10:05:49.0"
        # Convert T to space for matching
        clean_date_str = date_str.replace('T', ' ').replace('.0', '').strip()
        trips_df['clean_date'] = trips_df['date'].astype(str).str.replace('.0', '').str.strip()
        
        # Debug logging - show all available trips
        app.logger.info(f"=== Searching for trip ===")
        app.logger.info(f"Looking for: date='{clean_date_str}', distance={distance}, odometer={odometer}")
        app.logger.info(f"Available trips (first 10):")
        for idx, row in trips_df.head(10).iterrows():
            app.logger.info(f"  Trip {idx}: date='{row['clean_date']}', distance={row['distance']}, odometer={row.get('odometer_start', 'N/A')}")
        
        mask = (trips_df['clean_date'] == clean_date_str) & \
               (trips_df['distance'] == distance)
        if odometer is not None:
            mask = mask & (trips_df['odometer_start'] == odometer)
        
        trip_data = trips_df[mask]
        
        app.logger.info(f"Matching trips found: {len(trip_data)}")
        
        if trip_data.empty:
            app.logger.error(f"Trip not found! No match for date='{clean_date_str}', distance={distance}, odometer={odometer}")
            return jsonify({"error": "Trip not found"}), 404
        
        # Get the first matching trip (should be unique after deduplication)
        trip = trip_data.iloc[0].to_dict()
        
        # Clean NaN values
        trip = {k: (None if pd.isna(v) else v) for k, v in trip.items()}
        
        # Calculate energy efficiency if possible
        if trip['distance'] and trip['total_consumed']:
            trip['efficiency_wh_per_km'] = round(trip['total_consumed'] / trip['distance'], 1)
        else:
            trip['efficiency_wh_per_km'] = None
        
        # Calculate net energy (consumed - regenerated)
        if trip['total_consumed'] and trip['regenerated_energy']:
            trip['net_energy'] = trip['total_consumed'] - trip['regenerated_energy']
        else:
            trip['net_energy'] = trip['total_consumed']
        
        return jsonify(trip)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trips')
def get_trips():
    try:
        # Get query parameters for pagination and filtering
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        min_distance = request.args.get('min_distance', type=float)
        max_distance = request.args.get('max_distance', type=float)
        
        # Get all trips
        trips_df = storage.get_trips_df()
        
        if trips_df.empty:
            return jsonify({
                'trips': [],
                'total': 0,
                'page': page,
                'per_page': per_page,
                'total_pages': 0
            })
        
        # Apply filters
        if start_date:
            trips_df = trips_df[trips_df['date'] >= start_date]
        if end_date:
            trips_df = trips_df[trips_df['date'] <= end_date]
        if min_distance is not None:
            trips_df = trips_df[trips_df['distance'] >= min_distance]
        if max_distance is not None:
            trips_df = trips_df[trips_df['distance'] <= max_distance]
        
        # Sort by date descending
        trips_df = trips_df.sort_values('date', ascending=False)
        
        # Calculate pagination
        total = len(trips_df)
        total_pages = (total + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        # Get page of trips
        page_trips = trips_df.iloc[start_idx:end_idx]
        
        # Convert to JSON string with proper NaN handling
        json_str = page_trips.to_json(orient='records', date_format='iso')
        json_str = json_str.replace('NaN', 'null')
        trips_data = json.loads(json_str)
        
        return jsonify({
            'trips': trips_data,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/battery-history')
def get_battery_history():
    try:
        battery_df = storage.get_battery_history(days=7)
        if not battery_df.empty:
            # Fill NaN values with None before any processing
            battery_df = battery_df.fillna(value=np.nan).replace([np.nan], [None])
            # Create battery level chart
            battery_trace = go.Scatter(
                x=battery_df['timestamp'],
                y=battery_df['battery_level'],
                mode='lines+markers',
                name='Battery Level',
                line=dict(color='#2ecc71', width=3),
                connectgaps=False  # Show gaps for missing data
            )
            
            # Create temperature chart
            temp_trace = go.Scatter(
                x=battery_df['timestamp'],
                y=battery_df['temperature'],
                mode='lines+markers',
                name='Temperature',
                line=dict(color='#e74c3c', width=2),
                yaxis='y2',
                connectgaps=False  # Show gaps for missing data
            )
            
            layout = go.Layout(
                title='Battery Level vs Temperature',
                xaxis=dict(title='Time'),
                yaxis=dict(title='Battery Level (%)', side='left'),
                yaxis2=dict(title='Temperature (°C)', side='right', overlaying='y'),
                hovermode='x unified',
                template='plotly_white'
            )
            
            fig = go.Figure(data=[battery_trace, temp_trace], layout=layout)
            # Convert NaN to null for JSON compatibility
            graphJSON = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
            graphJSON = graphJSON.replace('NaN', 'null')
            
            # Convert DataFrame to JSON string with proper NaN handling
            data_json = battery_df.to_json(orient='records', date_format='iso')
            data_json = data_json.replace('NaN', 'null')
            
            # Parse and clean the data
            data_list = json.loads(data_json)
            chart_data = json.loads(graphJSON.replace('NaN', 'null'))
            
            # Clean any remaining NaN values
            response_data = clean_nan_values({
                "chart": chart_data,
                "data": data_list
            })
            
            return jsonify(response_data)
        return jsonify({"chart": None, "data": []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/debug')
def debug_api():
    """Debug endpoint to check API configuration and connectivity"""
    if not client:
        return jsonify({
            "status": "error",
            "message": "API client not initialized",
            "config": {
                "username": os.getenv("BLUELINKUSER", "NOT_SET"),
                "region": os.getenv("BLUELINKREGION", "NOT_SET"),
                "brand": os.getenv("BLUELINKBRAND", "NOT_SET"),
                "vehicle_id": os.getenv("BLUELINKVID", "NOT_SET"),
                "cache_enabled": os.getenv("API_CACHE_ENABLED", "true")
            }
        })
    
    try:
        # Check cache status
        cache_info = {
            "cache_enabled": client.cache_enabled,
            "cache_validity_minutes": client.cache_validity.total_seconds() / 60,
            "cache_retention_hours": client.cache_retention.total_seconds() / 3600,
            "cache_directory": str(client.cache_dir)
        }
        
        # List cached files
        cache_files = list(client.cache_dir.glob("*.json"))
        cache_info["cached_files"] = [f.name for f in cache_files]
        
        return jsonify({
            "status": "ok",
            "api_initialized": client.manager is not None,
            "config": {
                "region": client.region,
                "brand": client.brand,
                "vehicle_id": client.vehicle_id[:10] + "..." if client.vehicle_id else None
            },
            "cache": cache_info
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/efficiency-stats')
def get_efficiency_stats():
    """Get efficiency statistics for different time periods"""
    try:
        from datetime import datetime, timedelta
        import pandas as pd
        
        trips_df = storage.get_trips_df()
        
        if trips_df.empty:
            return jsonify({"error": "No trips found"}), 404
        
        # Convert date column to datetime, handling .0 suffix
        trips_df['date'] = trips_df['date'].astype(str).str.replace(r'\.0+$', '', regex=True)
        trips_df['date'] = pd.to_datetime(trips_df['date'])
        
        # Calculate efficiency in Wh/km for each trip
        trips_df['efficiency_wh_per_km'] = trips_df.apply(
            lambda row: row['total_consumed'] / row['distance'] if row['distance'] > 0 else None, axis=1
        )
        
        # Convert to mi/kWh (miles per kilowatt-hour)
        # 1 Wh/km = 1.60934 Wh/mi
        # mi/kWh = 1000 / (Wh/mi) = 1000 / (Wh/km * 1.60934)
        trips_df['efficiency_mi_per_kwh'] = trips_df['efficiency_wh_per_km'].apply(
            lambda x: 1000 / (x * 1.60934) if x and x > 0 else None
        )
        
        now = datetime.now()
        today = now.date()
        
        # Define time periods
        periods = {
            'last_day': now - timedelta(days=1),
            'last_week': now - timedelta(weeks=1),
            'last_month': now - timedelta(days=30),
            'last_year': now - timedelta(days=365)
        }
        
        stats = {}
        
        # Calculate stats for each period
        for period_name, start_date in periods.items():
            period_trips = trips_df[trips_df['date'] >= start_date]
            
            if not period_trips.empty:
                # Filter out None values
                valid_efficiencies = period_trips['efficiency_mi_per_kwh'].dropna()
                
                if not valid_efficiencies.empty:
                    stats[period_name] = {
                        'average': round(valid_efficiencies.mean(), 2),
                        'best': round(valid_efficiencies.max(), 2),
                        'worst': round(valid_efficiencies.min(), 2),
                        'trip_count': len(valid_efficiencies)
                    }
                else:
                    stats[period_name] = None
            else:
                stats[period_name] = None
        
        # Overall stats
        valid_efficiencies_all = trips_df['efficiency_mi_per_kwh'].dropna()
        if not valid_efficiencies_all.empty:
            stats['all_time'] = {
                'average': round(valid_efficiencies_all.mean(), 2),
                'best': round(valid_efficiencies_all.max(), 2),
                'worst': round(valid_efficiencies_all.min(), 2),
                'trip_count': len(valid_efficiencies_all)
            }
        else:
            stats['all_time'] = None
        
        # Also calculate total energy and distance for context
        stats['totals'] = {
            'last_day': {
                'distance_km': float(trips_df[trips_df['date'] >= periods['last_day']]['distance'].sum()),
                'energy_kwh': float(trips_df[trips_df['date'] >= periods['last_day']]['total_consumed'].sum() / 1000)
            },
            'last_week': {
                'distance_km': float(trips_df[trips_df['date'] >= periods['last_week']]['distance'].sum()),
                'energy_kwh': float(trips_df[trips_df['date'] >= periods['last_week']]['total_consumed'].sum() / 1000)
            },
            'last_month': {
                'distance_km': float(trips_df[trips_df['date'] >= periods['last_month']]['distance'].sum()),
                'energy_kwh': float(trips_df[trips_df['date'] >= periods['last_month']]['total_consumed'].sum() / 1000)
            },
            'all_time': {
                'distance_km': float(trips_df['distance'].sum()),
                'energy_kwh': float(trips_df['total_consumed'].sum() / 1000)
            }
        }
        
        return jsonify(stats)
        
    except Exception as e:
        app.logger.error(f"Error calculating efficiency stats: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/locations')
def get_all_locations():
    """Get all trip locations for mapping"""
    try:
        trips_df = storage.get_trips_df()
        
        if trips_df.empty:
            app.logger.warning("No trips found in DataFrame")
            return jsonify([])
        
        app.logger.info(f"Found {len(trips_df)} trips total")
        
        # Get locations with valid coordinates
        locations = []
        coords_count = 0
        for _, trip in trips_df.iterrows():
            if pd.notna(trip.get('end_latitude')) and pd.notna(trip.get('end_longitude')):
                coords_count += 1
                locations.append({
                    'lat': float(trip['end_latitude']),
                    'lng': float(trip['end_longitude']),
                    'date': str(trip['date']),
                    'distance': float(trip['distance']) if pd.notna(trip['distance']) else 0,
                    'duration': int(trip['duration']) if pd.notna(trip['duration']) else 0,
                    'efficiency': round(trip['total_consumed'] / trip['distance'], 1) if trip['distance'] and trip['distance'] > 0 else None,
                    'temperature': float(trip['end_temperature']) if pd.notna(trip.get('end_temperature')) else None
                })
        
        # Also add current location if available
        battery_df = storage.get_battery_df()
        if not battery_df.empty:
            latest = battery_df.iloc[-1]
            # Get location from API client
            if client:
                try:
                    data = client.get_vehicle_data()
                    if data and data.get('location'):
                        loc = data['location']
                        if loc.get('latitude') and loc.get('longitude'):
                            locations.append({
                                'lat': float(loc['latitude']),
                                'lng': float(loc['longitude']),
                                'date': 'Current Location',
                                'distance': 0,
                                'duration': 0,
                                'efficiency': None,
                                'temperature': None,
                                'is_current': True
                            })
                except:
                    pass
        
        app.logger.info(f"Found {coords_count} trips with coordinates, returning {len(locations)} locations")
        return jsonify(locations)
        
    except Exception as e:
        app.logger.error(f"Error getting locations: {e}")
        return jsonify([])

@app.route('/api/collection-status')
def get_collection_status():
    """Get data collection status"""
    try:
        history_file = Path('data/api_call_history.json')
        if history_file.exists():
            with open(history_file, 'r') as f:
                history = json.load(f)
                
            # Calculate next collection time
            calls_today = history.get('calls_today', 0)
            daily_limit = int(os.getenv('API_DAILY_LIMIT', 30))
            
            if calls_today < daily_limit:
                # Calculate based on evenly distributed collections
                last_call = datetime.fromisoformat(history.get('last_call', datetime.now().isoformat()))
                interval_minutes = (24 * 60) // daily_limit
                next_collection = last_call + timedelta(minutes=interval_minutes)
            else:
                # Next collection tomorrow at midnight
                tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                next_collection = tomorrow
            
            return jsonify({
                'calls_today': calls_today,
                'daily_limit': daily_limit,
                'next_collection': next_collection.isoformat(),
                'last_call': history.get('last_call')
            })
        else:
            daily_limit = int(os.getenv('API_DAILY_LIMIT', 30))
            return jsonify({
                'calls_today': 0,
                'daily_limit': daily_limit,
                'next_collection': None,
                'last_call': None
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/current-status')
def get_current_status():
    try:
        battery_df = storage.get_battery_df()
        if not battery_df.empty:
            # Use pandas to_json to handle NaN properly
            latest_json = battery_df.iloc[[-1]].to_json(orient='records', date_format='iso')
            latest_data = json.loads(latest_json)[0]
            
            return jsonify({
                "battery_level": latest_data.get('battery_level'),
                "is_charging": latest_data.get('is_charging'),
                "charging_power": latest_data.get('charging_power'),
                "range": latest_data.get('range'),
                "temperature": latest_data.get('temperature'),
                "odometer": latest_data.get('odometer'),
                "last_updated": latest_data.get('timestamp')
            })
        return jsonify({
            "battery_level": None,
            "is_charging": None,
            "range": None,
            "temperature": None,
            "odometer": None,
            "last_updated": None
        })
    except Exception as e:
        print(f"Error in current-status: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)