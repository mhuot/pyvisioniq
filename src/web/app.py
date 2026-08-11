import hmac
import json
import os
import sys
import base64
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
import plotly.graph_objs as go
import plotly.utils
from flask import Flask, Response, jsonify, render_template, request, send_file

sys.path.append(str(Path(__file__).parent.parent.parent))

from src.api.client import APIError, CachedVehicleClient
from src.storage.csv_store import CSVStorage
from src.utils.charge_efficiency import MIN_SOC_POINTS, compute_efficiency_points, summarize
from src.utils.plug_sessions import append_plug_sample, load_ha_export, load_plug_log
from src.utils.receipts import upsert_receipts
from src.web.auth import admin_required, api_login_required, init_auth, login_required
from src.web.auth_routes import auth_bp
from src.web.cache_routes import cache_bp
from src.web.debug_routes import debug_bp

# trips.csv records distance in MILES, not kilometres. Verified against the
# odometer (which is in km) across 354 driving days: the ratio is ~1.61 before
# accounting for per-trip truncation to whole miles.
KM_PER_MILE = 1.60934

# Bounds on believable driving efficiency. Anything outside is a logging fault:
# the API sometimes records a stub consumption of a few Wh against a long
# distance, which would otherwise read as thousands of miles per kWh.
MIN_PLAUSIBLE_MI_PER_KWH = 0.5
MAX_PLAUSIBLE_MI_PER_KWH = 10.0

# Charging splits into populations two orders of magnitude apart: L1 sits near
# 1.3 kW while DC fast charging reaches 160 kW. Plotted on one linear axis the
# AC sessions, which are 97% of the history, collapse onto the baseline. Tagging
# each session lets the dashboard show one population at a time.
L2_POWER_FLOOR_KW = 2.5
DCFC_POWER_FLOOR_KW = 20.0


def classify_charge_type(avg_power_kw):
    """Bucket a session as l1, l2 or dcfc from its average power."""
    if avg_power_kw is None:
        return None
    if avg_power_kw >= DCFC_POWER_FLOOR_KW:
        return "dcfc"
    return "l2" if avg_power_kw >= L2_POWER_FLOOR_KW else "l1"


def efficiency_wh_per_km(total_consumed_wh, distance_miles):
    """Convert a trip's energy use into Wh per kilometre.

    Dividing consumption by the raw distance yields Wh per MILE. Reporting that
    as Wh/km overstates consumption by 61%, and converting it onward to mi/kWh
    divides by 1.60934 a second time, understating range by the same factor.
    """
    if not distance_miles or distance_miles <= 0 or not total_consumed_wh:
        return None
    return total_consumed_wh / (distance_miles * KM_PER_MILE)


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")  # nosec B105

# Initialize with error handling
try:
    client = CachedVehicleClient()
    app.config["cache_client"] = client  # Store client in app config for blueprints
except Exception as e:
    app.logger.warning("Failed to initialize API client: %s", e)
    client = None
    app.config["cache_client"] = None

storage = CSVStorage()
app.config["storage"] = storage

# Configure authentication. This is a no-op unless AUTH_ENABLED=true, in which
# case route decorators below start enforcing Entra ID login.
init_auth(app)

# Register blueprints
app.register_blueprint(cache_bp)
app.register_blueprint(debug_bp)
app.register_blueprint(auth_bp)

# Global cache for battery history
cached_battery_history = {"data": None, "timestamp": None}


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
@login_required
def index():
    return render_template(
        "index.html",
        battery_usable_kwh=float(os.getenv("BATTERY_USABLE_KWH", "74.0")),
    )


@app.route("/favicon.ico")
def favicon():
    # Check if we have a favicon.png to serve
    # Use absolute path relative to the app file location
    favicon_path = Path(__file__).parent / "static" / "favicon.png"
    if favicon_path.exists():
        return send_file(favicon_path, mimetype="image/png")
    return "", 204  # No content


@app.route("/api/clear-cache")
@admin_required
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
@admin_required
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

                    api_time = datetime.fromisoformat(str(api_updated).replace("Z", "+00:00"))
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
            jsonify({"status": "error", "message": e.message, "error_type": e.error_type}),
            status_code,
        )

    except Exception as e:
        # Fallback for unexpected errors
        app.logger.error(f"Unexpected error in refresh_data: {type(e).__name__}: {str(e)}")
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
@api_login_required
def get_trip_detail(trip_id):
    """Get detailed information about a specific trip"""
    try:
        app.logger.info("=== Trip Detail Request ===")
        app.logger.info("Raw trip_id: %s", trip_id)

        trips_df = storage.get_trips_df()

        if trips_df.empty:
            app.logger.error("No trips found in database")
            return jsonify({"error": "No trips found"}), 404

        app.logger.info(f"Total trips in database: {len(trips_df)}")

        # Trip ID is composed of date_distance_odometer
        parts = trip_id.split("_")
        app.logger.info(f"Trip ID parts: {parts}")

        if len(parts) < 2:
            app.logger.error("Invalid trip ID format: %s", parts)
            return jsonify({"error": "Invalid trip ID"}), 400

        # Decode the base64 encoded date
        try:
            # Add padding if needed
            encoded_date = parts[0]
            padding = 4 - (len(encoded_date) % 4)
            if padding != 4:
                encoded_date += "=" * padding
            date_str = base64.b64decode(encoded_date).decode("utf-8")
            app.logger.info("Decoded date from base64: '%s'", date_str)
        except Exception as e:
            # Fallback to old format if decoding fails
            date_str = parts[0]
            app.logger.warning("Base64 decode failed: %s, using raw date: '%s'", e, date_str)

        distance = float(parts[1])
        odometer = float(parts[2]) if len(parts) > 2 and parts[2] else None

        app.logger.info(
            "Parsed values - date: '%s', distance: %s, odometer: %s",
            date_str,
            distance,
            odometer,
        )

        # Find the trip - handle various date formats
        # The date might come as "2025-05-25T10:05:49" (from JSON) but CSV has "2025-05-25 10:05:49.0"
        # Convert T to space for matching
        clean_date_str = date_str.replace("T", " ").replace(".0", "").strip()
        trips_df["clean_date"] = trips_df["date"].astype(str).str.replace(".0", "").str.strip()

        # Debug logging - show all available trips
        app.logger.info("=== Searching for trip ===")
        app.logger.info(
            "Looking for: date='%s', distance=%s, odometer=%s",
            clean_date_str,
            distance,
            odometer,
        )
        app.logger.info("Available trips (first 10):")
        for idx, row in trips_df.head(10).iterrows():
            app.logger.info(
                "  Trip %s: date='%s', distance=%s, odometer=%s",
                idx,
                row["clean_date"],
                row["distance"],
                row.get("odometer_start", "N/A"),
            )

        mask = (trips_df["clean_date"] == clean_date_str) & (trips_df["distance"] == distance)
        if odometer is not None:
            mask = mask & (trips_df["odometer_start"] == odometer)

        trip_data = trips_df[mask]

        app.logger.info("Matching trips found: %s", len(trip_data))

        if trip_data.empty:
            app.logger.error(
                "Trip not found! No match for date='%s', distance=%s, odometer=%s",
                clean_date_str,
                distance,
                odometer,
            )
            return jsonify({"error": "Trip not found"}), 404

        # Get the first matching trip (should be unique after deduplication)
        trip = trip_data.iloc[0].to_dict()

        # Clean NaN values
        trip = {k: (None if pd.isna(v) else v) for k, v in trip.items()}

        # Calculate energy efficiency if possible
        if trip["distance"] and trip["total_consumed"]:
            trip["efficiency_wh_per_km"] = round(
                efficiency_wh_per_km(trip["total_consumed"], trip["distance"]), 1
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
@api_login_required
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
                    row.get("total_consumed", 0) if pd.notna(row.get("total_consumed")) else 0
                )

                # Efficiency in Wh/km (distance is recorded in miles)
                per_km = efficiency_wh_per_km(total_consumed, row["distance"])
                page_trips.loc[idx, "efficiency_wh_per_km"] = round(per_km, 1) if per_km else 0
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


@app.route("/api/battery/history")
@api_login_required
def get_battery_history():
    """Get battery history data"""
    try:
        # Check if cache is fresh enough
        if cached_battery_history["data"] is not None:
            age = (datetime.now() - cached_battery_history["timestamp"]).total_seconds()
            if age < 60:  # Cache for 1 minute
                return jsonify(cached_battery_history["data"])

        battery_df = storage.get_battery_df()

        if battery_df.empty:
            return jsonify([])

        # Normalize mixed timestamp formats so filtering and sorting work
        battery_df["timestamp"] = pd.to_datetime(battery_df["timestamp"], format="mixed")

        # Explicit window (used by the charge detail modal) takes precedence
        window_start = request.args.get("start")
        window_end = request.args.get("end")
        if window_start and window_end:
            try:
                start_ts = pd.to_datetime(window_start)
                end_ts = pd.to_datetime(window_end)
                battery_df = battery_df[
                    (battery_df["timestamp"] >= start_ts) & (battery_df["timestamp"] <= end_ts)
                ]
            except (ValueError, TypeError):
                pass
            hours = None
        else:
            hours = request.args.get("hours")

        # Filter by hours if requested
        if hours:
            try:
                hours_int = int(hours)
                cutoff = datetime.now() - timedelta(hours=hours_int)
                battery_df = battery_df[battery_df["timestamp"] >= cutoff]
            except ValueError:
                pass

        # Sort by timestamp
        battery_df = battery_df.sort_values("timestamp")

        # ISO timestamps (naive local time, full precision) so the browser parses
        # them consistently and boundary comparisons against session times work
        battery_df["timestamp"] = battery_df["timestamp"].apply(lambda t: t.isoformat())

        # Convert to JSON friendly format, replacing NaN with null
        result = clean_nan_values(battery_df.to_dict(orient="records"))
        return jsonify(result)

    except Exception as e:
        app.logger.error("Error fetching battery history: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug")
@admin_required
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
                    "vehicle_id": (client.vehicle_id[:10] + "..." if client.vehicle_id else None),
                },
                "cache": cache_info,
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/temperature-efficiency")
@api_login_required
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
            if trip["distance"] > 0 and trip["total_consumed"] and trip["total_consumed"] > 0:
                # Calculate efficiency
                per_km = efficiency_wh_per_km(trip["total_consumed"], trip["distance"])
                efficiency_mi_per_kwh = 1000 / (per_km * KM_PER_MILE)

                # Discard logging faults. Six rows in the history pair a long
                # distance with a stub consumption of a few Wh, the worst
                # reading 15,300 mi/kWh. Plotted raw they set the y-axis two
                # orders of magnitude too high and flatten every real point
                # onto the baseline.
                if not (
                    MIN_PLAUSIBLE_MI_PER_KWH <= efficiency_mi_per_kwh <= MAX_PLAUSIBLE_MI_PER_KWH
                ):
                    continue

                # Find closest battery reading to get temperature
                trip_time = pd.to_datetime(trip["date"])
                battery_df["timestamp"] = pd.to_datetime(battery_df["timestamp"], format="ISO8601")
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


@app.route("/api/charging-temperature-impact")
@api_login_required
def get_charging_temperature_impact():
    """Return charging session performance grouped by ambient temperature."""
    try:
        sessions_df = storage.get_charging_sessions_df()
        battery_df = storage.get_battery_df()

        if sessions_df.empty or battery_df.empty:
            return jsonify({"error": "No charging or temperature data available"}), 404

        sessions_df["start_time"] = pd.to_datetime(sessions_df["start_time"], errors="coerce")
        sessions_df["end_time"] = pd.to_datetime(sessions_df["end_time"], errors="coerce")
        battery_df["timestamp"] = pd.to_datetime(
            battery_df["timestamp"], format="ISO8601", errors="coerce"
        )

        sessions_df = sessions_df.dropna(subset=["start_time", "end_time"])
        battery_df = battery_df.dropna(subset=["timestamp", "temperature"])

        if sessions_df.empty or battery_df.empty:
            return jsonify({"error": "Insufficient data after sanitizing values"}), 404

        if sessions_df.empty:
            return jsonify({"error": "No charging sessions available"}), 404

        battery_df = battery_df.sort_values("timestamp").reset_index(drop=True)

        def lookup_temperature(session_time):
            deltas = (battery_df["timestamp"] - session_time).abs()
            if deltas.empty:
                return None
            idx = deltas.idxmin()
            if pd.isna(idx):
                return None
            if deltas.iloc[idx] > pd.Timedelta(minutes=90):
                return None
            return battery_df.iloc[idx]["temperature"]

        raw_points = []
        temp_bins: Dict[str, Dict[str, float]] = {}
        capacity = getattr(storage, "battery_capacity_kwh", 77.4)

        for _, session in sessions_df.iterrows():
            temperature = lookup_temperature(session["start_time"])
            if temperature is None or pd.isna(temperature):
                continue

            duration_minutes = session.get("duration_minutes")
            if pd.isna(duration_minutes) or duration_minutes <= 0:
                duration_minutes = (
                    session["end_time"] - session["start_time"]
                ).total_seconds() / 60
            if duration_minutes < 5:
                continue

            energy_added = session.get("energy_added")
            start_battery = session.get("start_battery", 0)
            end_battery = session.get("end_battery", 0)
            battery_delta = max(float(end_battery) - float(start_battery), 0.0)

            if pd.isna(energy_added) or energy_added <= 0:
                energy_added = (battery_delta / 100.0) * capacity
            if energy_added <= 0:
                continue

            avg_power = energy_added / (duration_minutes / 60.0) if duration_minutes > 0 else 0
            if avg_power <= 0:
                continue

            raw_points.append(
                {
                    "temperature": round(float(temperature), 2),
                    "avg_power": round(float(avg_power), 2),
                    "charge_type": classify_charge_type(avg_power),
                    "energy_added": round(float(energy_added), 2),
                    "duration_minutes": round(float(duration_minutes), 1),
                    "start_time": session["start_time"].isoformat(),
                    "end_time": (
                        session["end_time"].isoformat() if pd.notna(session["end_time"]) else None
                    ),
                }
            )

            bin_start = int(float(temperature) // 5) * 5
            label = f"{bin_start} to {bin_start + 5}°C"
            bucket = temp_bins.setdefault(
                label,
                {
                    "temperatures": [],
                    "avg_powers": [],
                    "durations": [],
                    "energy": 0.0,
                    "count": 0,
                },
            )
            bucket["temperatures"].append(float(temperature))
            bucket["avg_powers"].append(float(avg_power))
            bucket["durations"].append(float(duration_minutes))
            bucket["energy"] += float(energy_added)
            bucket["count"] += 1

        if not raw_points:
            return (
                jsonify({"error": "Could not match charging sessions with temperature readings"}),
                404,
            )

        bin_stats = []
        for label, bucket in temp_bins.items():
            avg_temp = sum(bucket["temperatures"]) / len(bucket["temperatures"])
            avg_power = sum(bucket["avg_powers"]) / len(bucket["avg_powers"])
            bin_stats.append(
                {
                    "temperature_range": label,
                    "avg_temperature": round(avg_temp, 1),
                    "avg_power": round(avg_power, 2),
                    "avg_duration_minutes": (
                        round(sum(bucket["durations"]) / len(bucket["durations"]), 1)
                        if bucket["durations"]
                        else 0
                    ),
                    "session_count": bucket["count"],
                    "total_energy": round(bucket["energy"], 2),
                }
            )

        bin_stats.sort(key=lambda x: x["avg_temperature"])

        temperatures = [point["temperature"] for point in raw_points]
        avg_powers = [point["avg_power"] for point in raw_points]
        durations = [point["duration_minutes"] for point in raw_points]
        energies = [point["energy_added"] for point in raw_points]

        best_bin = max(bin_stats, key=lambda b: b["avg_power"]) if bin_stats else None
        worst_bin = min(bin_stats, key=lambda b: b["avg_power"]) if bin_stats else None

        return jsonify(
            {
                "raw_data": raw_points,
                "temperature_bins": bin_stats,
                "summary": {
                    "total_sessions": len(raw_points),
                    "temperature_range": {
                        "min": round(min(temperatures), 1),
                        "max": round(max(temperatures), 1),
                    },
                    "avg_power_range": {
                        "min": round(min(avg_powers), 2),
                        "max": round(max(avg_powers), 2),
                    },
                    "average_power": round(sum(avg_powers) / len(avg_powers), 2),
                    "average_duration_minutes": round(sum(durations) / len(durations), 1),
                    "total_energy_kwh": round(sum(energies), 2),
                    "best_temperature_band": (
                        {
                            "range": best_bin["temperature_range"],
                            "avg_power": best_bin["avg_power"],
                            "avg_duration_minutes": best_bin["avg_duration_minutes"],
                            "session_count": best_bin["session_count"],
                        }
                        if best_bin
                        else None
                    ),
                    "worst_temperature_band": (
                        {
                            "range": worst_bin["temperature_range"],
                            "avg_power": worst_bin["avg_power"],
                            "avg_duration_minutes": worst_bin["avg_duration_minutes"],
                            "session_count": worst_bin["session_count"],
                        }
                        if worst_bin
                        else None
                    ),
                },
            }
        )

    except Exception as e:
        app.logger.error(f"Error in charging temperature impact analysis: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/efficiency-by-month")
@api_login_required
def get_efficiency_by_month():
    """Return driving efficiency and ambient temperature for each calendar month.

    Complements the temperature-binned chart: binning by temperature shows the
    relationship, while binning by month shows the seasonal cycle actually lived
    through, including how long each part of it lasted.

    Note on units: ``trips.distance`` is recorded in MILES (confirmed against the
    odometer, which is in km, at a ratio of ~1.61 before per-trip truncation).
    Efficiency is therefore computed as Wh per mile and converted from there.
    """
    try:
        trips_df = storage.get_trips_df()
        if trips_df.empty:
            return jsonify({"error": "No trip data available", "months": []}), 404

        trips_df = trips_df.copy()
        trips_df["date"] = pd.to_datetime(
            trips_df["date"].astype(str).str.replace(r"\.0+$", "", regex=True), errors="coerce"
        )
        trips_df = trips_df.dropna(subset=["date"])
        trips_df["distance"] = pd.to_numeric(trips_df["distance"], errors="coerce")
        trips_df["total_consumed"] = pd.to_numeric(trips_df["total_consumed"], errors="coerce")
        trips_df = trips_df[(trips_df["distance"] > 0) & (trips_df["total_consumed"] > 0)]
        if trips_df.empty:
            return jsonify({"error": "No usable trip data", "months": []}), 404

        battery_df = storage.get_battery_df()
        monthly_temp = {}
        if not battery_df.empty:
            battery_df = battery_df.copy()
            battery_df["timestamp"] = pd.to_datetime(
                battery_df["timestamp"], format="mixed", errors="coerce"
            )
            battery_df = battery_df.dropna(subset=["timestamp"])
            temp_column = "meteo_temp" if "meteo_temp" in battery_df.columns else "temperature"
            battery_df[temp_column] = pd.to_numeric(battery_df[temp_column], errors="coerce")
            grouped = battery_df.dropna(subset=[temp_column]).groupby(
                battery_df["timestamp"].dt.to_period("M")
            )[temp_column]
            monthly_temp = {
                str(period): round(float(value), 1) for period, value in grouped.mean().items()
            }

        months = []
        for period, group in trips_df.groupby(trips_df["date"].dt.to_period("M")):
            miles = float(group["distance"].sum())
            watt_hours = float(group["total_consumed"].sum())
            if miles <= 0:
                continue
            wh_per_mile = watt_hours / miles
            months.append(
                {
                    "month": str(period),
                    "trips": int(len(group)),
                    "miles": round(miles, 1),
                    "kwh": round(watt_hours / 1000.0, 1),
                    "wh_per_mile": round(wh_per_mile, 1),
                    "wh_per_km": round(wh_per_mile / KM_PER_MILE, 1),
                    "mi_per_kwh": round(1000.0 / wh_per_mile, 2),
                    "temperature": monthly_temp.get(str(period)),
                }
            )

        months.sort(key=lambda entry: entry["month"])
        return jsonify({"months": months})

    except Exception as e:  # pylint: disable=broad-except
        app.logger.error("Error computing efficiency by month: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/charging-efficiency")
@api_login_required
def get_charging_efficiency():
    """Return AC-to-pack charging efficiency per session, plus headline totals.

    Compares metered wall energy from the smart plug against pack energy implied
    by the SOC gain. Sessions too small for whole-percent SOC to be meaningful
    are excluded, so the response is sparse by design.
    """
    try:
        ac_frames = []
        ha_export = Path("data/ha_plug_history.csv")
        if ha_export.exists():
            ac_frames.append(load_ha_export(ha_export))
        ac_frames.append(load_plug_log())
        ac_samples = pd.concat(ac_frames, ignore_index=True) if ac_frames else pd.DataFrame()
        if not ac_samples.empty:
            ac_samples = (
                ac_samples.sort_values("timestamp")
                .drop_duplicates("timestamp")
                .reset_index(drop=True)
            )

        battery_df = storage.get_battery_df()
        if battery_df.empty or ac_samples.empty:
            return (
                jsonify({"error": "No smart-plug or battery data available", "points": []}),
                404,
            )

        battery_df["timestamp"] = pd.to_datetime(
            battery_df["timestamp"], format="mixed", errors="coerce"
        )

        # SOC spans the *usable* window, not the 77.4 kWh total pack. Using the
        # gross figure here would overstate delivered energy, and therefore
        # efficiency, by about 4.6%.
        pack_kwh = float(os.getenv("BATTERY_USABLE_KWH", "74.0"))
        points = compute_efficiency_points(battery_df, ac_samples, pack_kwh)

        hours = request.args.get("hours")
        if hours and hours != "all" and points:
            try:
                cutoff = datetime.now() - timedelta(hours=float(hours))
                points = [p for p in points if datetime.fromisoformat(p["start_time"]) >= cutoff]
            except ValueError:
                pass

        return jsonify(
            {
                "points": points,
                "summary": summarize(points),
                "pack_kwh": pack_kwh,
                "min_soc_points": MIN_SOC_POINTS,
            }
        )

    except Exception as e:  # pylint: disable=broad-except
        app.logger.error("Error computing charging efficiency: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/efficiency-stats")
@api_login_required
def get_efficiency_stats():
    """Get efficiency statistics for different time periods"""
    try:
        from datetime import datetime, timedelta

        import pandas as pd

        trips_df = storage.get_trips_df()

        if trips_df.empty:
            return jsonify({"error": "No trips found"}), 404

        # Convert date column to datetime, handling .0 suffix
        trips_df["date"] = trips_df["date"].astype(str).str.replace(r"\.0+$", "", regex=True)
        trips_df["date"] = pd.to_datetime(trips_df["date"])

        # Calculate efficiency in Wh/km for each trip
        trips_df["efficiency_wh_per_km"] = trips_df.apply(
            lambda row: efficiency_wh_per_km(row["total_consumed"], row["distance"]),
            axis=1,
        )

        # Convert to mi/kWh (miles per kilowatt-hour)
        # 1 Wh/km = 1.60934 Wh/mi
        # mi/kWh = 1000 / (Wh/mi) = 1000 / (Wh/km * 1.60934)
        trips_df["efficiency_mi_per_kwh"] = trips_df["efficiency_wh_per_km"].apply(
            lambda x: 1000 / (x * KM_PER_MILE) if x and x > 0 else None
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

        def summarize_period(period_trips):
            """Aggregate a period's trips into average, best and worst mi/kWh.

            The average is energy-weighted (total distance over total energy)
            rather than a mean of per-trip ratios. A mean of ratios lets a single
            short or mis-logged trip dominate: the API occasionally records a
            stub consumption of a few Wh against a long distance, which alone
            produced averages above 15,000 mi/kWh.

            Trips outside a physically plausible band are dropped entirely, since
            they are data faults rather than unusually efficient driving.
            """
            usable = period_trips.dropna(subset=["efficiency_mi_per_kwh"])
            usable = usable[
                usable["efficiency_mi_per_kwh"].between(
                    MIN_PLAUSIBLE_MI_PER_KWH, MAX_PLAUSIBLE_MI_PER_KWH
                )
            ]
            if usable.empty:
                return None

            total_miles = float(usable["distance"].sum())
            total_kwh = float(usable["total_consumed"].sum()) / 1000.0
            if total_kwh <= 0:
                return None

            return {
                "average": round(total_miles / total_kwh, 2),
                "best": round(float(usable["efficiency_mi_per_kwh"].max()), 2),
                "worst": round(float(usable["efficiency_mi_per_kwh"].min()), 2),
                "trip_count": int(len(usable)),
            }

        for period_name, start_date in periods.items():
            period_trips = trips_df[trips_df["date"] >= start_date]
            stats[period_name] = summarize_period(period_trips) if not period_trips.empty else None

        stats["all_time"] = summarize_period(trips_df)

        # Also calculate total energy and distance for context
        stats["totals"] = {
            "last_day": {
                "distance_km": float(
                    trips_df[trips_df["date"] >= periods["last_day"]]["distance"].sum()
                ),
                "energy_kwh": float(
                    trips_df[trips_df["date"] >= periods["last_day"]]["total_consumed"].sum() / 1000
                ),
            },
            "last_week": {
                "distance_km": float(
                    trips_df[trips_df["date"] >= periods["last_week"]]["distance"].sum()
                ),
                "energy_kwh": float(
                    trips_df[trips_df["date"] >= periods["last_week"]]["total_consumed"].sum()
                    / 1000
                ),
            },
            "last_month": {
                "distance_km": float(
                    trips_df[trips_df["date"] >= periods["last_month"]]["distance"].sum()
                ),
                "energy_kwh": float(
                    trips_df[trips_df["date"] >= periods["last_month"]]["total_consumed"].sum()
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
@api_login_required
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
                "Before filtering: %d trips, date range: %s to %s",
                len(trips_df),
                trips_df["date"].min(),
                trips_df["date"].max(),
            )

            if hours == "custom" and start_date and end_date:
                # Filter by custom date range
                start = pd.to_datetime(start_date)
                end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
                trips_df = trips_df[(trips_df["date"] >= start) & (trips_df["date"] < end)]
                app.logger.info(
                    "Custom date filter: %s to %s, %s trips remain", start, end, len(trips_df)
                )
            elif hours != "all":
                try:
                    # Filter trips by time range
                    hours_int = int(hours)
                    cutoff = pd.Timestamp.now() - pd.Timedelta(hours=hours_int)
                    app.logger.info("Time filter: last %s hours, cutoff: %s", hours_int, cutoff)
                    trips_df = trips_df[trips_df["date"] >= cutoff]
                    app.logger.info("After time filter: %s trips remain", len(trips_df))
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
            if pd.notna(trip.get("end_latitude")) and pd.notna(trip.get("end_longitude")):
                coords_count += 1
                locations.append(
                    {
                        "lat": float(trip["end_latitude"]),
                        "lng": float(trip["end_longitude"]),
                        "date": str(trip["date"]),
                        "distance": (float(trip["distance"]) if pd.notna(trip["distance"]) else 0),
                        "duration": (int(trip["duration"]) if pd.notna(trip["duration"]) else 0),
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
                except Exception:  # nosec B110
                    pass

        app.logger.info(
            f"Found {coords_count} trips with coordinates, returning {len(locations)} locations"
        )
        return jsonify(locations)

    except Exception as e:
        app.logger.error(f"Error getting locations: {e}")
        return jsonify([])


@app.route("/api/charging-sessions")
@api_login_required
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
            "Filtering charging sessions: hours=%s, start_date=%s, end_date=%s",
            hours,
            start_date,
            end_date,
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
                (sessions_df["start_time"] >= start_dt) & (sessions_df["start_time"] < end_dt)
            ]
        elif hours != "all":
            # Hours-based filtering
            try:
                hours_int = int(hours)
                # Use timezone-aware datetime if sessions have timezone info
                if sessions_df["start_time"].dt.tz is not None:
                    from datetime import timezone

                    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours_int)
                else:
                    cutoff_time = datetime.now() - timedelta(hours=hours_int)

                app.logger.info("Current time: %s", datetime.now())
                app.logger.info("Cutoff time (%sh ago): %s", hours_int, cutoff_time)
                app.logger.info("Filtering sessions from %s onwards", cutoff_time)

                # Debug: show which sessions pass the filter
                for idx, row in sessions_df.iterrows():
                    passes = row["start_time"] >= cutoff_time
                    app.logger.info(
                        "  - %s: %s >= %s ? %s",
                        row["session_id"],
                        row["start_time"],
                        cutoff_time,
                        passes,
                    )

                sessions_df = sessions_df[sessions_df["start_time"] >= cutoff_time]
            except ValueError:
                pass  # If hours is invalid, show all

        # Log the filtered dataframe info
        app.logger.info("After filtering: %s sessions remain", len(sessions_df))
        if not sessions_df.empty:
            app.logger.info(
                "Sessions dates: %s to %s",
                sessions_df["start_time"].min(),
                sessions_df["start_time"].max(),
            )
            app.logger.info("Active sessions: %s", len(sessions_df[~sessions_df["is_complete"]]))
        elif not original_sessions.empty:
            # fall back to most recent sessions across full history so UI still has content
            fallback_candidates = original_sessions[original_sessions["start_time"].notna()]
            if fallback_candidates.empty:
                return jsonify([])
            fallback_count = min(len(fallback_candidates), 10)
            sessions_df = fallback_candidates.sort_values("start_time", ascending=False).head(
                fallback_count
            )
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
                    session["start_time"].isoformat() if pd.notna(session["start_time"]) else None
                ),
                "end_time": (
                    session["end_time"].isoformat() if pd.notna(session["end_time"]) else None
                ),
                "duration_minutes": (
                    float(session["duration_minutes"])
                    if pd.notna(session["duration_minutes"])
                    else 0
                ),
                "start_battery": (
                    int(session["start_battery"]) if pd.notna(session["start_battery"]) else None
                ),
                "end_battery": (
                    int(session["end_battery"]) if pd.notna(session["end_battery"]) else None
                ),
                "energy_added": (
                    float(session["energy_added"]) if pd.notna(session["energy_added"]) else 0
                ),
                "avg_power": (float(session["avg_power"]) if pd.notna(session["avg_power"]) else 0),
                "max_power": (float(session["max_power"]) if pd.notna(session["max_power"]) else 0),
                "location_lat": (
                    float(session["location_lat"]) if pd.notna(session["location_lat"]) else None
                ),
                "location_lon": (
                    float(session["location_lon"]) if pd.notna(session["location_lon"]) else None
                ),
                "network": (
                    str(session["network"])
                    if pd.notna(session.get("network")) and str(session.get("network")).strip()
                    else None
                ),
                "location_name": (
                    str(session["location_name"])
                    if pd.notna(session.get("location_name"))
                    and str(session.get("location_name")).strip()
                    else None
                ),
                "cost_usd": (
                    float(session["cost_usd"]) if pd.notna(session.get("cost_usd")) else None
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
@api_login_required
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

            # Prefer the collector's own recorded decision (accurate under
            # adaptive polling); fall back to the fixed-interval estimate
            next_collection = None
            log_file = Path("data/polling_log.csv")
            if log_file.exists():
                try:
                    log_df = pd.read_csv(log_file)
                    last_row = log_df.iloc[-1]
                    estimate = pd.to_datetime(last_row["timestamp"]) + pd.Timedelta(
                        minutes=float(last_row["interval_minutes"])
                    )
                    if estimate > pd.Timestamp.now():
                        next_collection = estimate.to_pydatetime()
                except (ValueError, KeyError, IndexError) as e:
                    app.logger.warning("Could not read polling log: %s", e)

            if next_collection is None and calls_today < daily_limit:
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
                        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

                        # Find next available slot
                        for i in range(calls_today, daily_limit):
                            scheduled_time = today_start + timedelta(minutes=interval_minutes * i)
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
            elif next_collection is None:
                # Budget exhausted: next collection tomorrow at midnight
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


@app.route("/api/plug-sample", methods=["POST"])
def receive_plug_sample():
    """Receive a smart-plug power sample from a Home Assistant automation.

    Authenticated by a shared token (X-Plug-Token header) instead of the
    interactive login, since it is called machine-to-machine.
    """
    expected_token = os.getenv("PLUG_WEBHOOK_TOKEN")
    if not expected_token:
        return jsonify({"error": "PLUG_WEBHOOK_TOKEN not configured"}), 503
    provided_token = request.headers.get("X-Plug-Token", "")
    if not hmac.compare_digest(provided_token, expected_token):
        return jsonify({"error": "unauthorized"}), 403

    payload = request.get_json(silent=True) or {}
    try:
        watts = float(payload.get("watts"))
    except (TypeError, ValueError):
        return jsonify({"error": "numeric 'watts' field required"}), 400

    append_plug_sample(watts)
    return jsonify({"status": "ok"})


@app.route("/api/charge-receipt", methods=["POST"])
def receive_charge_receipts():
    """Receive parsed charging-network receipts from an automation.

    Body is a JSON array of normalized receipt records (see
    src/utils/receipts.py). Authenticated by the same shared token as the
    plug webhook, since both are machine-to-machine.
    """
    expected_token = os.getenv("PLUG_WEBHOOK_TOKEN")
    if not expected_token:
        return jsonify({"error": "PLUG_WEBHOOK_TOKEN not configured"}), 503
    provided_token = request.headers.get("X-Plug-Token", "")
    if not hmac.compare_digest(provided_token, expected_token):
        return jsonify({"error": "unauthorized"}), 403

    receipts = request.get_json(silent=True)
    if not isinstance(receipts, list) or not receipts:
        return jsonify({"error": "JSON array of receipt records required"}), 400
    for record in receipts:
        if not isinstance(record, dict) or not record.get("external_id"):
            return jsonify({"error": "each record needs an external_id"}), 400

    try:
        updated, inserted, skipped, messages = upsert_receipts(
            receipts, Path("data/charging_sessions.csv"), write=True, make_backup=False
        )
        return jsonify(
            {
                "corrected": updated,
                "inserted": inserted,
                "skipped": skipped,
                "detail": messages,
            }
        )
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"invalid receipt record: {e}"}), 400


@app.route("/api/polling-status")
@api_login_required
def get_polling_status():
    """Report the collector's polling decisions for observability"""
    try:
        adaptive_enabled = os.getenv("ADAPTIVE_POLLING", "false").lower() == "true"

        calls_today = None
        daily_limit = int(os.getenv("API_DAILY_LIMIT", 30))
        last_call = None
        charging_since = None
        history_file = Path("data/api_call_history.json")
        if history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
            calls_today = history.get("calls_today")
            last_call = history.get("last_call")
            charging_since = history.get("charging_since")

        decisions = []
        reason_counts_today = {}
        next_collection_estimate = None
        log_file = Path("data/polling_log.csv")
        if log_file.exists():
            log_df = pd.read_csv(log_file)
            log_df["timestamp"] = pd.to_datetime(log_df["timestamp"], format="mixed")
            log_df = log_df.sort_values("timestamp")

            today = pd.Timestamp.now().normalize()
            today_df = log_df[log_df["timestamp"] >= today]
            reason_counts_today = today_df["reason"].value_counts().to_dict()

            last_row = log_df.iloc[-1]
            next_collection_estimate = (
                last_row["timestamp"] + pd.Timedelta(minutes=float(last_row["interval_minutes"]))
            ).isoformat()

            recent = log_df.tail(20).copy()
            recent["timestamp"] = recent["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S")
            decisions = recent.to_dict(orient="records")

        return jsonify(
            {
                "adaptive_enabled": adaptive_enabled,
                "calls_today": calls_today,
                "daily_limit": daily_limit,
                "last_call": last_call,
                "charging_since": charging_since,
                "next_collection_estimate": next_collection_estimate,
                "reason_counts_today": reason_counts_today,
                "recent_decisions": decisions,
            }
        )
    except Exception as e:
        app.logger.error("Error building polling status: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/current-status")
@api_login_required
def get_current_status():
    try:
        battery_df = storage.get_battery_df()
        if not battery_df.empty:
            # Use pandas to_json to handle NaN properly
            latest_json = battery_df.iloc[[-1]].to_json(orient="records", date_format="iso")
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
                    except Exception:  # nosec B110
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
                response_data["api_last_updated"] = latest_cache_data["api_last_updated"]
            if latest_cache_data:
                response_data["hyundai_data_fresh"] = latest_cache_data.get("hyundai_data_fresh")

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
        app.logger.error("Error in current-status: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=os.getenv("DEBUG_MODE", "false").lower() == "true")  # nosec B201
