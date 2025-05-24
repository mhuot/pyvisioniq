from flask import Flask, render_template, jsonify, Response, request
import plotly.graph_objs as go
import plotly.utils
import json
import os
from datetime import datetime, timedelta
import sys
from pathlib import Path
import numpy as np

sys.path.append(str(Path(__file__).parent.parent.parent))

from src.api.client import CachedVehicleClient
from src.storage.csv_store import CSVStorage

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key'

# Initialize with error handling
try:
    client = CachedVehicleClient()
except Exception as e:
    print(f"Warning: Failed to initialize API client: {e}")
    client = None

storage = CSVStorage()

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
        data = client.get_vehicle_data()
        if data:
            storage.store_vehicle_data(data)
            return jsonify({"status": "success", "message": "Data refreshed successfully"})
        else:
            return jsonify({"status": "error", "message": "Failed to fetch data"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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
                line=dict(color='#2ecc71', width=3)
            )
            
            # Create temperature chart
            temp_trace = go.Scatter(
                x=battery_df['timestamp'],
                y=battery_df['temperature'],
                mode='lines+markers',
                name='Temperature',
                line=dict(color='#e74c3c', width=2),
                yaxis='y2'
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
            "cache_duration_hours": client.cache_duration.total_seconds() / 3600,
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