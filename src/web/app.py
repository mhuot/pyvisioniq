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
from src.storage.factory import create_storage
from src.web.cache_routes import cache_bp
from src.web.debug_routes import debug_bp

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key"

# Initialize with error handling
try:
    client = CachedVehicleClient()
    app.config["cache_client"] = client  # Store client in app config for blueprints
except Exception as e:
    print(f"Warning: Failed to initialize API client: {e}")
    client = None
    app.config["cache_client"] = None

storage = create_storage()

# Register blueprints
app.register_blueprint(cache_bp)
app.register_blueprint(debug_bp)


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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    # Check if we have a favicon.png to serve
    # Use absolute path relative to the app file location
    favicon_path = Path(__file__).parent / "static" / "favicon.png"
    if favicon_path.exists():
        return send_file(favicon_path, mimetype="image/png")
    return "", 204  # No content


@app.route("/api/clear-cache")
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
            return jsonify(
                {
                    "status": "success",
                    "message": f"Cleared {len(cleared)} cache files",
                    "files": cleared,
                }
            )
        return jsonify({"status": "error", "message": "Client not initialized"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/refresh")
def refresh_data():
    if not client:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "API client not initialized. Please check your .env configuration.",
                }
            ),
            500,
        )

    try:
        # Force a cache update to get fresh data
        # Note: timeout is handled within the client
        data = client.force_cache_update()

        if data:
            storage.store_vehicle_data(data)

            # Include data freshness info in response
            api_updated = data.get("api_last_updated", "Unknown")
            if api_updated and api_updated != "Unknown":
                try:
                    from datetime import datetime

                    api_time = datetime.fromisoformat(
                        str(api_updated).replace("Z", "+00:00")
                    )
                    age_minutes = int(
                        (datetime.now(api_time.tzinfo) - api_time).total_seconds() / 60
                    )
                    freshness_msg = f" (vehicle data from {age_minutes} minutes ago)"
                except:
                    freshness_msg = ""
            else:
                freshness_msg = ""

            return jsonify(
                {
                    "status": "success",
                    "message": f"Data refreshed successfully{freshness_msg}",
                }
            )
        else:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to fetch data. The vehicle may be offline or in an area without coverage.",
                    }
                ),
                500,
            )

    except APIError as e:
        # Use our custom error classification
        status_code = 429 if e.error_type == "rate_limit" else 500
        return (
            jsonify(
                {"status": "error", "message": e.message, "error_type": e.error_type}
            ),
            status_code,
        )

    except Exception as e:
        # Fallback for unexpected errors
        app.logger.error(
            f"Unexpected error in refresh_data: {type(e).__name__}: {str(e)}"
        )
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"An unexpected error occurred. Please try again later. ({type(e).__name__})",
                }
            ),
            500,
        )


@app.route("/api/trip/<trip_id>")
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
        parts = trip_id.split("_")
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
                encoded_date += "=" * padding
            date_str = base64.b64decode(encoded_date).decode("utf-8")
            app.logger.info(f"Decoded date from base64: '{date_str}'")
        except Exception as e:
            # Fallback to old format if decoding fails
            date_str = parts[0]
            app.logger.warning(
                f"Base64 decode failed: {e}, using raw date: '{date_str}'"
            )

        distance = float(parts[1])
        odometer = float(parts[2]) if len(parts) > 2 and parts[2] else None

        app.logger.info(
            f"Parsed values - date: '{date_str}', distance: {distance}, odometer: {odometer}"
        )

        # Find the trip - handle various date formats
        # The date might come as "2025-05-25T10:05:49" (from JSON) but CSV has "2025-05-25 10:05:49.0"
        # Convert T to space for matching
        clean_date_str = date_str.replace("T", " ").replace(".0", "").strip()
        trips_df["clean_date"] = (
            trips_df["date"].astype(str).str.replace(".0", "").str.strip()
        )

        # Debug logging - show all available trips
        app.logger.info(f"=== Searching for trip ===")
        app.logger.info(
            f"Looking for: date='{clean_date_str}', distance={distance}, odometer={odometer}"
        )
        app.logger.info(f"Available trips (first 10):")
        for idx, row in trips_df.head(10).iterrows():
            app.logger.info(
                f"  Trip {idx}: date='{row['clean_date']}', distance={row['distance']}, odometer={row.get('odometer_start', 'N/A')}"
            )

        mask = (trips_df["clean_date"] == clean_date_str) & (
            trips_df["distance"] == distance
        )
        if odometer is not None:
            mask = mask & (trips_df["odometer_start"] == odometer)

        trip_data = trips_df[mask]

        app.logger.info(f"Matching trips found: {len(trip_data)}")

        if trip_data.empty:
            app.logger.error(
                f"Trip not found! No match for date='{clean_date_str}', distance={distance}, odometer={odometer}"
            )
            return jsonify({"error": "Trip not found"}), 404

        # Get the first matching trip (should be unique after deduplication)
        trip = trip_data.iloc[0].to_dict()

        # Clean NaN values
        trip = {k: (None if pd.isna(v) else v) for k, v in trip.items()}

        # Calculate energy efficiency if possible
        if trip["distance"] and trip["total_consumed"]:
            trip["efficiency_wh_per_km"] = round(
                trip["total_consumed"] / trip["distance"], 1
            )
        else:
            trip["efficiency_wh_per_km"] = None

        # Calculate net energy (consumed - regenerated)
        if trip["total_consumed"] and trip["regenerated_energy"]:
            trip["net_energy"] = trip["total_consumed"] - trip["regenerated_energy"]
        else:
            trip["net_energy"] = trip["total_consumed"]

        return jsonify(trip)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trips")
def get_trips():
    try:
        # Get query parameters for pagination and filtering
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        min_distance = request.args.get("min_distance", type=float)
        max_distance = request.args.get("max_distance", type=float)
        hours = request.args.get("hours")  # Time range filter

        # Get all trips
        trips_df = storage.get_trips_df()

        if trips_df.empty:
            return jsonify(
                {
                    "trips": [],
                    "total": 0,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": 0,
                }
            )

        # Apply time range filter first if specified
        if hours and hours != "all":
            try:
                trips_df["date"] = pd.to_datetime(trips_df["date"])
                hours_int = int(hours)
                cutoff = pd.Timestamp.now() - pd.Timedelta(hours=hours_int)
                trips_df = trips_df[trips_df["date"] >= cutoff]
            except (ValueError, TypeError):
                pass

        # Apply other filters
        if start_date:
            trips_df = trips_df[trips_df["date"] >= start_date]
        if end_date:
            trips_df = trips_df[trips_df["date"] <= end_date]
        if min_distance is not None:
            trips_df = trips_df[trips_df["distance"] >= min_distance]
        if max_distance is not None:
            trips_df = trips_df[trips_df["distance"] <= max_distance]

        # Sort by date descending
        trips_df = trips_df.sort_values("date", ascending=False)

        # Calculate pagination
        total = len(trips_df)
        total_pages = (total + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page

        # Get page of trips
        page_trips = trips_df.iloc[start_idx:end_idx]

        # Add efficiency calculations
        page_trips = page_trips.copy()  # Create a copy to avoid SettingWithCopyWarning
        for idx, row in page_trips.iterrows():
            if pd.notna(row.get("distance")) and row["distance"] > 0:
                # Use total consumed for efficiency calculation
                total_consumed = (
                    row.get("total_consumed", 0)
                    if pd.notna(row.get("total_consumed"))
                    else 0
                )

                # Efficiency in Wh/km
                efficiency_wh_per_km = (
                    round(total_consumed / row["distance"], 1)
                    if total_consumed > 0
                    else 0
                )
                page_trips.loc[idx, "efficiency_wh_per_km"] = efficiency_wh_per_km
            else:
                page_trips.loc[idx, "efficiency_wh_per_km"] = 0

        # Convert energy values from Wh to kWh
        energy_columns = [
            "total_consumed",
            "regenerated_energy",
            "accessories_consumed",
            "climate_consumed",
            "drivetrain_consumed",
            "battery_care_consumed",
        ]
        for col in energy_columns:
            if col in page_trips.columns:
                page_trips[col] = page_trips[col] / 1000.0  # Convert Wh to kWh

        # Convert to JSON string with proper NaN handling
        json_str = page_trips.to_json(orient="records", date_format="iso")
        json_str = json_str.replace("NaN", "null")
        trips_data = json.loads(json_str)

        return jsonify(
            {
                "trips": trips_data,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/battery-history")
def get_battery_history():
    try:
        # Get parameters from query string
        hours = request.args.get("hours", "24")
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        # If custom date range is provided
        if hours == "custom" and start_date and end_date:
            battery_df = storage.get_battery_df()
            if not battery_df.empty:
                battery_df["timestamp"] = pd.to_datetime(battery_df["timestamp"])
                # Filter by date range
                start = pd.to_datetime(start_date)
                end = pd.to_datetime(end_date) + pd.Timedelta(
                    days=1
                )  # Include end date
                battery_df = battery_df[
                    (battery_df["timestamp"] >= start) & (battery_df["timestamp"] < end)
                ]
        else:
            # Convert hours to days for the storage function
            if hours == "all":
                days = None  # Get all data
            else:
                try:
                    hours_int = int(hours)
                    days = hours_int / 24.0
                except ValueError:
                    days = 1  # Default to 1 day if invalid

            battery_df = storage.get_battery_history(days=days)
        if not battery_df.empty:
            # Fill NaN values with None before any processing
            battery_df = battery_df.fillna(value=np.nan).replace([np.nan], [None])
            # Create battery level chart
            battery_trace = go.Scatter(
                x=battery_df["timestamp"],
                y=battery_df["battery_level"],
                mode="lines+markers",
                name="Battery Level",
                line=dict(color="#2ecc71", width=3),
                connectgaps=False,  # Show gaps for missing data
            )

            # Create temperature chart
            temp_trace = go.Scatter(
                x=battery_df["timestamp"],
                y=battery_df["temperature"],
                mode="lines+markers",
                name="Temperature",
                line=dict(color="#e74c3c", width=2),
                yaxis="y2",
                connectgaps=False,  # Show gaps for missing data
            )

            layout = go.Layout(
                title="Battery Level vs Temperature",
                xaxis=dict(title="Time"),
                yaxis=dict(title="Battery Level (%)", side="left"),
                yaxis2=dict(title="Temperature (°C)", side="right", overlaying="y"),
                hovermode="x unified",
                template="plotly_white",
            )

            fig = go.Figure(data=[battery_trace, temp_trace], layout=layout)
            # Convert NaN to null for JSON compatibility
            graphJSON = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
            graphJSON = graphJSON.replace("NaN", "null")

            # Convert DataFrame to JSON string with proper NaN handling
            data_json = battery_df.to_json(orient="records", date_format="iso")
            data_json = data_json.replace("NaN", "null")

            # Parse and clean the data
            data_list = json.loads(data_json)
            chart_data = json.loads(graphJSON.replace("NaN", "null"))

            # Clean any remaining NaN values
            response_data = clean_nan_values({"chart": chart_data, "data": data_list})

            return jsonify(response_data)
        return jsonify({"chart": None, "data": []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug")
def debug_api():
    """Debug endpoint to check API configuration and connectivity"""
    debug_info = {
        "api_client": {
            "initialized": client is not None,
            "config": {
                "username": os.getenv("BLUELINKUSER", "NOT_SET"),
                "region": os.getenv("BLUELINKREGION", "NOT_SET"),
                "brand": os.getenv("BLUELINKBRAND", "NOT_SET"),
                "vehicle_id": os.getenv("BLUELINKVID", "NOT_SET"),
                "cache_enabled": os.getenv("API_CACHE_ENABLED", "true"),
            },
        }
    }

    try:
        # Check cache status
        cache_info = {
            "cache_enabled": client.cache_enabled,
            "cache_validity_minutes": client.cache_validity.total_seconds() / 60,
            "cache_retention_hours": client.cache_retention.total_seconds() / 3600,
            "cache_directory": str(client.cache_dir),
        }

        # List cached files
        cache_files = list(client.cache_dir.glob("*.json"))
        cache_info["cached_files"] = [f.name for f in cache_files]

        return jsonify(
            {
                "status": "ok",
                "api_initialized": client.manager is not None,
                "config": {
                    "region": client.region,
                    "brand": client.brand,
                    "vehicle_id": (
                        client.vehicle_id[:10] + "..." if client.vehicle_id else None
                    ),
                },
                "cache": cache_info,
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/temperature-efficiency")
def get_temperature_efficiency():
    """Get efficiency data correlated with temperature"""
    try:
        trips_df = storage.get_trips_df()
        battery_df = storage.get_battery_df()

        if trips_df.empty or battery_df.empty:
            return jsonify({"error": "No data available"}), 404

        # Merge trips with battery data to get temperature
        # First, get the closest battery reading for each trip
        efficiency_data = []

        for _, trip in trips_df.iterrows():
            if (
                trip["distance"] > 0
                and trip["total_consumed"]
                and trip["total_consumed"] > 0
            ):
                # Calculate efficiency
                efficiency_wh_per_km = trip["total_consumed"] / trip["distance"]
                efficiency_mi_per_kwh = 1000 / (efficiency_wh_per_km * 1.60934)

                # Find closest battery reading to get temperature
                trip_time = pd.to_datetime(trip["date"])
                battery_df["timestamp"] = pd.to_datetime(
                    battery_df["timestamp"], format="ISO8601"
                )
                time_diffs = abs(battery_df["timestamp"] - trip_time)
                closest_idx = time_diffs.idxmin()

                if time_diffs[closest_idx] < pd.Timedelta(hours=1):  # Within 1 hour
                    temp = battery_df.loc[closest_idx, "temperature"]
                    if pd.notna(temp):
                        efficiency_data.append(
                            {
                                "temperature": float(temp),
                                "efficiency": float(efficiency_mi_per_kwh),
                                "distance": float(trip["distance"]),
                                "date": (
                                    trip["date"].isoformat()
                                    if hasattr(trip["date"], "isoformat")
                                    else str(trip["date"])
                                ),
                            }
                        )

        if not efficiency_data:
            return (
                jsonify({"error": "No efficiency data with temperature available"}),
                404,
            )

        # Create temperature bins (5°C ranges)
        temp_bins = {}
        for data_point in efficiency_data:
            temp = data_point["temperature"]
            # Create 5°C bins: -20 to -15, -15 to -10, etc.
            bin_start = int(temp // 5) * 5
            bin_label = f"{bin_start} to {bin_start + 5}°C"

            if bin_label not in temp_bins:
                temp_bins[bin_label] = {
                    "temperatures": [],
                    "efficiencies": [],
                    "count": 0,
                    "total_distance": 0,
                }

            temp_bins[bin_label]["temperatures"].append(temp)
            temp_bins[bin_label]["efficiencies"].append(data_point["efficiency"])
            temp_bins[bin_label]["count"] += 1
            temp_bins[bin_label]["total_distance"] += data_point["distance"]

        # Calculate averages for each bin
        bin_stats = []
        for bin_label, data in temp_bins.items():
            if data["efficiencies"]:
                avg_efficiency = sum(data["efficiencies"]) / len(data["efficiencies"])
                avg_temp = sum(data["temperatures"]) / len(data["temperatures"])

                bin_stats.append(
                    {
                        "temperature_range": bin_label,
                        "avg_temperature": round(avg_temp, 1),
                        "avg_efficiency": round(avg_efficiency, 2),
                        "trip_count": data["count"],
                        "total_distance": round(data["total_distance"], 1),
                        "best_efficiency": round(max(data["efficiencies"]), 2),
                        "worst_efficiency": round(min(data["efficiencies"]), 2),
                    }
                )

        # Sort by average temperature
        bin_stats.sort(key=lambda x: x["avg_temperature"])

        return jsonify(
            {
                "raw_data": efficiency_data,
                "temperature_bins": bin_stats,
                "summary": {
                    "total_trips": len(efficiency_data),
                    "temperature_range": {
                        "min": round(min(d["temperature"] for d in efficiency_data), 1),
                        "max": round(max(d["temperature"] for d in efficiency_data), 1),
                    },
                    "efficiency_range": {
                        "min": round(min(d["efficiency"] for d in efficiency_data), 2),
                        "max": round(max(d["efficiency"] for d in efficiency_data), 2),
                    },
                },
            }
        )

    except Exception as e:
        app.logger.error(f"Error in temperature-efficiency analysis: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/efficiency-stats")
def get_efficiency_stats():
    """Get efficiency statistics for different time periods"""
    try:
        from datetime import datetime, timedelta
        import pandas as pd

        trips_df = storage.get_trips_df()

        if trips_df.empty:
            return jsonify({"error": "No trips found"}), 404

        # Convert date column to datetime, handling .0 suffix
        trips_df["date"] = (
            trips_df["date"].astype(str).str.replace(r"\.0+$", "", regex=True)
        )
        trips_df["date"] = pd.to_datetime(trips_df["date"])

        # Calculate efficiency in Wh/km for each trip
        trips_df["efficiency_wh_per_km"] = trips_df.apply(
            lambda row: (
                row["total_consumed"] / row["distance"] if row["distance"] > 0 else None
            ),
            axis=1,
        )

        # Convert to mi/kWh (miles per kilowatt-hour)
        # 1 Wh/km = 1.60934 Wh/mi
        # mi/kWh = 1000 / (Wh/mi) = 1000 / (Wh/km * 1.60934)
        trips_df["efficiency_mi_per_kwh"] = trips_df["efficiency_wh_per_km"].apply(
            lambda x: 1000 / (x * 1.60934) if x and x > 0 else None
        )

        now = datetime.now()
        today = now.date()

        # Define time periods
        periods = {
            "last_day": now - timedelta(days=1),
            "last_week": now - timedelta(weeks=1),
            "last_month": now - timedelta(days=30),
            "last_year": now - timedelta(days=365),
        }

        stats = {}

        # Calculate stats for each period
        for period_name, start_date in periods.items():
            period_trips = trips_df[trips_df["date"] >= start_date]

            if not period_trips.empty:
                # Filter out None values
                valid_efficiencies = period_trips["efficiency_mi_per_kwh"].dropna()

                if not valid_efficiencies.empty:
                    stats[period_name] = {
                        "average": round(valid_efficiencies.mean(), 2),
                        "best": round(valid_efficiencies.max(), 2),
                        "worst": round(valid_efficiencies.min(), 2),
                        "trip_count": len(valid_efficiencies),
                    }
                else:
                    stats[period_name] = None
            else:
                stats[period_name] = None

        # Overall stats
        valid_efficiencies_all = trips_df["efficiency_mi_per_kwh"].dropna()
        if not valid_efficiencies_all.empty:
            stats["all_time"] = {
                "average": round(valid_efficiencies_all.mean(), 2),
                "best": round(valid_efficiencies_all.max(), 2),
                "worst": round(valid_efficiencies_all.min(), 2),
                "trip_count": len(valid_efficiencies_all),
            }
        else:
            stats["all_time"] = None

        # Also calculate total energy and distance for context
        stats["totals"] = {
            "last_day": {
                "distance_km": float(
                    trips_df[trips_df["date"] >= periods["last_day"]]["distance"].sum()
                ),
                "energy_kwh": float(
                    trips_df[trips_df["date"] >= periods["last_day"]][
                        "total_consumed"
                    ].sum()
                    / 1000
                ),
            },
            "last_week": {
                "distance_km": float(
                    trips_df[trips_df["date"] >= periods["last_week"]]["distance"].sum()
                ),
                "energy_kwh": float(
                    trips_df[trips_df["date"] >= periods["last_week"]][
                        "total_consumed"
                    ].sum()
                    / 1000
                ),
            },
            "last_month": {
                "distance_km": float(
                    trips_df[trips_df["date"] >= periods["last_month"]][
                        "distance"
                    ].sum()
                ),
                "energy_kwh": float(
                    trips_df[trips_df["date"] >= periods["last_month"]][
                        "total_consumed"
                    ].sum()
                    / 1000
                ),
            },
            "all_time": {
                "distance_km": float(trips_df["distance"].sum()),
                "energy_kwh": float(trips_df["total_consumed"].sum() / 1000),
            },
        }

        return jsonify(stats)

    except Exception as e:
        app.logger.error(f"Error calculating efficiency stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/locations")
def get_all_locations():
    """Get all trip locations for mapping"""
    try:
        # Get parameters from query string
        hours = request.args.get("hours", "all")
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        trips_df = storage.get_trips_df()

        if not trips_df.empty:
            # Convert dates first
            trips_df["date"] = pd.to_datetime(trips_df["date"], errors="coerce")
            # Remove any trips with invalid dates
            trips_df = trips_df[trips_df["date"].notna()]

            app.logger.info(
                f"Before filtering: {len(trips_df)} trips, date range: {trips_df['date'].min()} to {trips_df['date'].max()}"
            )

            if hours == "custom" and start_date and end_date:
                # Filter by custom date range
                start = pd.to_datetime(start_date)
                end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
                trips_df = trips_df[
                    (trips_df["date"] >= start) & (trips_df["date"] < end)
                ]
                app.logger.info(
                    f"Custom date filter: {start} to {end}, {len(trips_df)} trips remain"
                )
            elif hours != "all":
                try:
                    # Filter trips by time range
                    hours_int = int(hours)
                    cutoff = pd.Timestamp.now() - pd.Timedelta(hours=hours_int)
                    app.logger.info(
                        f"Time filter: last {hours_int} hours, cutoff: {cutoff}"
                    )
                    trips_df = trips_df[trips_df["date"] >= cutoff]
                    app.logger.info(f"After time filter: {len(trips_df)} trips remain")
                except (ValueError, TypeError) as e:
                    app.logger.error(f"Error filtering by hours: {e}")
                    pass  # Use all data if conversion fails

        if trips_df.empty:
            app.logger.warning("No trips found in DataFrame")
            return jsonify([])

        app.logger.info(f"Found {len(trips_df)} trips total")

        # Get locations with valid coordinates
        locations = []
        coords_count = 0
        for _, trip in trips_df.iterrows():
            if pd.notna(trip.get("end_latitude")) and pd.notna(
                trip.get("end_longitude")
            ):
                coords_count += 1
                locations.append(
                    {
                        "lat": float(trip["end_latitude"]),
                        "lng": float(trip["end_longitude"]),
                        "date": str(trip["date"]),
                        "distance": (
                            float(trip["distance"]) if pd.notna(trip["distance"]) else 0
                        ),
                        "duration": (
                            int(trip["duration"]) if pd.notna(trip["duration"]) else 0
                        ),
                        "efficiency": (
                            round(trip["total_consumed"] / trip["distance"], 1)
                            if trip["distance"] and trip["distance"] > 0
                            else None
                        ),
                        "temperature": (
                            float(trip["end_temperature"])
                            if pd.notna(trip.get("end_temperature"))
                            else None
                        ),
                    }
                )

        # Also add current location if available
        battery_df = storage.get_battery_df()
        if not battery_df.empty:
            latest = battery_df.iloc[-1]
            # Get location from API client
            if client:
                try:
                    data = client.get_vehicle_data()
                    if data and data.get("location"):
                        loc = data["location"]
                        if loc.get("latitude") and loc.get("longitude"):
                            locations.append(
                                {
                                    "lat": float(loc["latitude"]),
                                    "lng": float(loc["longitude"]),
                                    "date": "Current Location",
                                    "distance": 0,
                                    "duration": 0,
                                    "efficiency": None,
                                    "temperature": None,
                                    "is_current": True,
                                }
                            )
                except:
                    pass

        app.logger.info(
            f"Found {coords_count} trips with coordinates, returning {len(locations)} locations"
        )
        return jsonify(locations)

    except Exception as e:
        app.logger.error(f"Error getting locations: {e}")
        return jsonify([])


@app.route("/api/charging-sessions")
def get_charging_sessions():
    """Get charging session history"""
    try:
        sessions_df = storage.get_charging_sessions_df()
        app.logger.info(f"Loading charging sessions, found {len(sessions_df)} sessions")

        if sessions_df.empty:
            app.logger.warning("No charging sessions found in storage")
            return jsonify([])

        # Apply time filtering
        hours = request.args.get("hours", "all")
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        app.logger.info(
            f"Filtering charging sessions: hours={hours}, start_date={start_date}, end_date={end_date}"
        )

        # Keep a copy so we can fall back to recent history if filters remove everything
        original_sessions = sessions_df.copy()

        # For sessions with missing start_time, try to extract from session_id
        sessions_df = sessions_df[sessions_df["start_time"].notna()]

        if sessions_df.empty:
            app.logger.warning(
                "All charging sessions missing start_time; falling back to original dataframe"
            )
            sessions_df = original_sessions[original_sessions["start_time"].notna()]
            if sessions_df.empty:
                return jsonify([])

        # Apply date filtering
        if start_date and end_date:
            # Custom date range
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date) + pd.Timedelta(days=1)  # Include end date
            sessions_df = sessions_df[
                (sessions_df["start_time"] >= start_dt)
                & (sessions_df["start_time"] < end_dt)
            ]
        elif hours != "all":
            # Hours-based filtering
            try:
                hours_int = int(hours)
                # Use timezone-aware datetime if sessions have timezone info
                if sessions_df["start_time"].dt.tz is not None:
                    from datetime import timezone

                    cutoff_time = datetime.now(timezone.utc) - timedelta(
                        hours=hours_int
                    )
                else:
                    cutoff_time = datetime.now() - timedelta(hours=hours_int)

                app.logger.info(f"Current time: {datetime.now()}")
                app.logger.info(f"Cutoff time ({hours_int}h ago): {cutoff_time}")
                app.logger.info(f"Filtering sessions from {cutoff_time} onwards")

                # Debug: show which sessions pass the filter
                for idx, row in sessions_df.iterrows():
                    passes = row["start_time"] >= cutoff_time
                    app.logger.info(
                        f"  - {row['session_id']}: {row['start_time']} >= {cutoff_time} ? {passes}"
                    )

                sessions_df = sessions_df[sessions_df["start_time"] >= cutoff_time]
            except ValueError:
                pass  # If hours is invalid, show all

        # Log the filtered dataframe info
        app.logger.info(f"After filtering: {len(sessions_df)} sessions remain")
        if not sessions_df.empty:
            app.logger.info(
                f"Sessions dates: {sessions_df['start_time'].min()} to {sessions_df['start_time'].max()}"
            )
            app.logger.info(
                f"Active sessions: {len(sessions_df[~sessions_df['is_complete']])}"
            )
        elif not original_sessions.empty:
            # fall back to most recent sessions across full history so UI still has content
            fallback_candidates = original_sessions[
                original_sessions["start_time"].notna()
            ]
            if fallback_candidates.empty:
                return jsonify([])
            fallback_count = min(len(fallback_candidates), 10)
            sessions_df = fallback_candidates.sort_values(
                "start_time", ascending=False
            ).head(fallback_count)
            app.logger.info(
                "No sessions matched filter; returning %s most recent sessions instead",
                fallback_count,
            )

        # Sort by start time descending (most recent first)
        sessions_df = sessions_df.sort_values("start_time", ascending=False)

        # Convert to JSON-friendly format
        sessions = []
        for idx, session in sessions_df.iterrows():
            is_active = not session["is_complete"]
            app.logger.debug(
                f"Processing session {session['session_id']}: is_complete={session['is_complete']}, is_active={is_active}"
            )

            session_data = {
                "session_id": session["session_id"],
                "start_time": (
                    session["start_time"].isoformat()
                    if pd.notna(session["start_time"])
                    else None
                ),
                "end_time": (
                    session["end_time"].isoformat()
                    if pd.notna(session["end_time"])
                    else None
                ),
                "duration_minutes": (
                    float(session["duration_minutes"])
                    if pd.notna(session["duration_minutes"])
                    else 0
                ),
                "start_battery": (
                    int(session["start_battery"])
                    if pd.notna(session["start_battery"])
                    else 0
                ),
                "end_battery": (
                    int(session["end_battery"])
                    if pd.notna(session["end_battery"])
                    else 0
                ),
                "energy_added": (
                    float(session["energy_added"])
                    if pd.notna(session["energy_added"])
                    else 0
                ),
                "avg_power": (
                    float(session["avg_power"]) if pd.notna(session["avg_power"]) else 0
                ),
                "max_power": (
                    float(session["max_power"]) if pd.notna(session["max_power"]) else 0
                ),
                "location_lat": (
                    float(session["location_lat"])
                    if pd.notna(session["location_lat"])
                    else None
                ),
                "location_lon": (
                    float(session["location_lon"])
                    if pd.notna(session["location_lon"])
                    else None
                ),
                "is_complete": (
                    str(session["is_complete"]).lower() == "true"
                    if isinstance(session["is_complete"], str)
                    else bool(session["is_complete"])
                ),
            }
            sessions.append(session_data)

        app.logger.info(f"Returning {len(sessions)} charging sessions")
        return jsonify(sessions)

    except Exception as e:
        app.logger.error(f"Error getting charging sessions: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/collection-status")
def get_collection_status():
    """Get data collection status"""
    try:
        history_file = Path("data/api_call_history.json")
        if history_file.exists():
            with open(history_file, "r") as f:
                history = json.load(f)

            # Calculate next collection time
            calls_today = history.get("calls_today", 0)
            daily_limit = int(os.getenv("API_DAILY_LIMIT", 30))

            if calls_today < daily_limit:
                # Calculate based on evenly distributed collections
                last_call_str = history.get("last_call")
                interval_minutes = (24 * 60) // daily_limit
                now = datetime.now()

                if last_call_str:
                    last_call = datetime.fromisoformat(last_call_str)
                    next_collection = last_call + timedelta(minutes=interval_minutes)

                    # If the calculated time has passed, find the next scheduled slot
                    if next_collection <= now:
                        # Calculate today's scheduled times
                        today_start = now.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )

                        # Find next available slot
                        for i in range(calls_today, daily_limit):
                            scheduled_time = today_start + timedelta(
                                minutes=interval_minutes * i
                            )
                            if scheduled_time > now:
                                next_collection = scheduled_time
                                break
                        else:
                            # No more slots today, schedule for tomorrow
                            tomorrow = today_start + timedelta(days=1)
                            next_collection = tomorrow
                else:
                    # No last call, schedule for next available slot
                    next_collection = now + timedelta(minutes=1)
            else:
                # Next collection tomorrow at midnight
                tomorrow = datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                next_collection = tomorrow

            return jsonify(
                {
                    "calls_today": calls_today,
                    "daily_limit": daily_limit,
                    "next_collection": next_collection.isoformat(),
                    "last_call": history.get("last_call"),
                }
            )
        else:
            daily_limit = int(os.getenv("API_DAILY_LIMIT", 30))
            return jsonify(
                {
                    "calls_today": 0,
                    "daily_limit": daily_limit,
                    "next_collection": None,
                    "last_call": None,
                }
            )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/current-status")
def get_current_status():
    try:
        battery_df = storage.get_battery_df()
        if not battery_df.empty:
            # Use pandas to_json to handle NaN properly
            latest_json = battery_df.iloc[[-1]].to_json(
                orient="records", date_format="iso"
            )
            latest_data = json.loads(latest_json)[0]

            # Get weather data if using meteo
            weather_data = None
            weather_source = os.getenv("WEATHER_SOURCE", "meteo").lower()
            if weather_source == "meteo":
                # Get latest location
                location_df = storage.get_locations_df()
                if not location_df.empty:
                    latest_location = location_df.iloc[-1]
                    lat = latest_location.get("latitude")
                    lon = latest_location.get("longitude")

                    if lat and lon:
                        from src.utils.weather import WeatherService

                        weather_service = WeatherService()
                        weather_data = weather_service.get_current_weather(lat, lon)

            # Get the most recent cached data to check api_last_updated
            latest_cache_data = None
            if client:
                cache_key = client._get_cache_key("full_data")
                cache_path = client._get_cache_path(cache_key)
                if cache_path.exists():
                    try:
                        with open(cache_path) as f:
                            latest_cache_data = json.load(f)
                    except:
                        pass

            response_data = {
                "battery_level": latest_data.get("battery_level"),
                "is_charging": latest_data.get("is_charging"),
                "charging_power": latest_data.get("charging_power"),
                "range": latest_data.get("range"),
                "temperature": latest_data.get("temperature"),
                "meteo_temp": latest_data.get("meteo_temp"),
                "vehicle_temp": latest_data.get("vehicle_temp"),
                "odometer": latest_data.get("odometer"),
                "last_updated": latest_data.get("timestamp"),
                "is_cached": latest_data.get("is_cached", False),
                "weather_source": weather_source,
            }

            # Add API freshness information
            if latest_cache_data and "api_last_updated" in latest_cache_data:
                response_data["api_last_updated"] = latest_cache_data[
                    "api_last_updated"
                ]
            if latest_cache_data:
                response_data["hyundai_data_fresh"] = latest_cache_data.get(
                    "hyundai_data_fresh"
                )

            # Add weather data if available
            if weather_data:
                response_data["weather"] = {
                    "temperature": weather_data.get("temperature"),
                    "temperature_unit": weather_data.get("temperature_unit", "F"),
                    "feels_like": weather_data.get("feels_like"),
                    "humidity": weather_data.get("humidity"),
                    "description": weather_data.get("description"),
                    "wind_speed": weather_data.get("wind_speed"),
                }

            return jsonify(response_data)
        return jsonify(
            {
                "battery_level": None,
                "is_charging": None,
                "range": None,
                "temperature": None,
                "odometer": None,
                "last_updated": None,
            }
        )
    except Exception as e:
        print(f"Error in current-status: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
