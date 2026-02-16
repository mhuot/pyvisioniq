"""
Debug routes for PyVisionic
Provides endpoints for debugging and monitoring
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Blueprint, jsonify, render_template_string, request

debug_bp = Blueprint("debug", __name__)


@debug_bp.route("/api/debug/errors")
def get_recent_errors():
    """Get recent errors from debug directory"""
    debug_dir = Path("debug")
    if not debug_dir.exists():
        return jsonify([])

    errors = []
    for error_file in sorted(debug_dir.glob("error_*.json"), reverse=True)[:20]:
        try:
            with open(error_file) as f:
                error_data = json.load(f)
                errors.append(
                    {
                        "filename": error_file.name,
                        "timestamp": error_data.get("timestamp"),
                        "error_type": error_data.get("error_type"),
                        "message": error_data.get("message"),
                        "context": error_data.get("context"),
                        "error_id": error_data.get("error_id"),
                    }
                )
        except Exception as e:
            errors.append({"filename": error_file.name, "error": f"Failed to read: {e}"})

    return jsonify(errors)


@debug_bp.route("/api/debug/error/<error_id>")
def get_error_detail(error_id):
    """Get detailed error information"""
    error_file = Path(f"debug/error_{error_id}.json")
    if not error_file.exists():
        return jsonify({"error": "Error not found"}), 404

    try:
        with open(error_file) as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": f"Failed to read error: {e}"}), 500


@debug_bp.route("/api/debug/logs")
def get_recent_logs():
    """Get recent log entries"""
    log_files = ["data_collector.log", "debug.log", "app.log"]
    logs = {}

    for log_file in log_files:
        if Path(log_file).exists():
            try:
                with open(log_file) as f:
                    # Get last 100 lines
                    lines = f.readlines()
                    logs[log_file] = lines[-100:]
            except Exception as e:
                logs[log_file] = [f"Error reading log: {e}"]
        else:
            logs[log_file] = ["Log file not found"]

    return jsonify(logs)


@debug_bp.route("/api/debug/data-types")
def check_data_types():
    """Check data types in storage"""
    from src.storage.factory import create_storage

    storage = create_storage()

    type_info = {}

    # Check battery data
    battery_df = storage.get_battery_df()
    if not battery_df.empty:
        type_info["battery"] = {
            "shape": battery_df.shape,
            "dtypes": {col: str(dtype) for col, dtype in battery_df.dtypes.items()},
            "sample": battery_df.tail(5).to_dict(orient="records"),
        }

    # Check trips data
    trips_df = storage.get_trips_df()
    if not trips_df.empty:
        type_info["trips"] = {
            "shape": trips_df.shape,
            "dtypes": {col: str(dtype) for col, dtype in trips_df.dtypes.items()},
            "sample": trips_df.tail(5).to_dict(orient="records"),
        }

    # Check charging sessions
    sessions_df = storage.get_charging_sessions_df()
    if not sessions_df.empty:
        type_info["charging_sessions"] = {
            "shape": sessions_df.shape,
            "dtypes": {col: str(dtype) for col, dtype in sessions_df.dtypes.items()},
            "data": sessions_df.to_dict(orient="records"),
        }

    return jsonify(type_info)


@debug_bp.route("/api/debug/validate")
def validate_data():
    """Validate current data and check for issues"""
    from src.storage.factory import create_storage
    from src.utils.debug import DataValidator

    storage = create_storage()
    issues = []

    # Check battery data
    battery_df = storage.get_battery_df()
    if not battery_df.empty:
        for idx, row in battery_df.iterrows():
            # Check battery level
            try:
                DataValidator.validate_battery_level(row["battery_level"], f"battery_df[{idx}]")
            except ValueError as e:
                issues.append(
                    {
                        "file": "battery_status.csv",
                        "row": idx,
                        "field": "battery_level",
                        "value": row["battery_level"],
                        "error": str(e),
                    }
                )

    # Check charging sessions
    sessions_df = storage.get_charging_sessions_df()
    if not sessions_df.empty:
        for idx, row in sessions_df.iterrows():
            # Check battery levels
            for field in ["start_battery", "end_battery"]:
                try:
                    DataValidator.validate_battery_level(row[field], f"sessions[{idx}].{field}")
                except ValueError as e:
                    issues.append(
                        {
                            "file": "charging_sessions.csv",
                            "row": idx,
                            "field": field,
                            "value": row[field],
                            "error": str(e),
                        }
                    )

    return jsonify(
        {
            "issues_found": len(issues),
            "issues": issues[:50],  # Limit to first 50 issues
            "checked_at": datetime.now().isoformat(),
        }
    )


@debug_bp.route("/debug")
def debug_dashboard():
    """Simple debug dashboard"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>PyVisionic Debug Dashboard</title>
        <style>
            body { font-family: monospace; margin: 20px; }
            .section { margin: 20px 0; padding: 10px; border: 1px solid #ccc; }
            .error { color: red; }
            .warning { color: orange; }
            pre { background: #f0f0f0; padding: 10px; overflow-x: auto; }
            button { margin: 5px; padding: 5px 10px; }
        </style>
    </head>
    <body>
        <h1>PyVisionic Debug Dashboard</h1>
        
        <div class="section">
            <h2>Recent Errors</h2>
            <button onclick="loadErrors()">Load Errors</button>
            <div id="errors"></div>
        </div>
        
        <div class="section">
            <h2>Data Validation</h2>
            <button onclick="validateData()">Validate Data</button>
            <div id="validation"></div>
        </div>
        
        <div class="section">
            <h2>Data Types</h2>
            <button onclick="checkDataTypes()">Check Data Types</button>
            <div id="datatypes"></div>
        </div>
        
        <div class="section">
            <h2>Recent Logs</h2>
            <button onclick="loadLogs()">Load Logs</button>
            <div id="logs"></div>
        </div>
        
        <script>
            async function loadErrors() {
                const resp = await fetch('/api/debug/errors');
                const errors = await resp.json();
                document.getElementById('errors').innerHTML = '<pre>' + JSON.stringify(errors, null, 2) + '</pre>';
            }
            
            async function validateData() {
                const resp = await fetch('/api/debug/validate');
                const result = await resp.json();
                document.getElementById('validation').innerHTML = '<pre>' + JSON.stringify(result, null, 2) + '</pre>';
            }
            
            async function checkDataTypes() {
                const resp = await fetch('/api/debug/data-types');
                const types = await resp.json();
                document.getElementById('datatypes').innerHTML = '<pre>' + JSON.stringify(types, null, 2) + '</pre>';
            }
            
            async function loadLogs() {
                const resp = await fetch('/api/debug/logs');
                const logs = await resp.json();
                document.getElementById('logs').innerHTML = '<pre>' + JSON.stringify(logs, null, 2) + '</pre>';
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html)
