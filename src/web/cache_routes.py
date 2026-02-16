import json
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request

cache_bp = Blueprint("cache", __name__, url_prefix="/cache")


def get_cache_client():
    """Get the cache client from the main app"""
    from flask import current_app

    return current_app.config.get("cache_client")


def get_file_info(file_path):
    """Get detailed information about a cache file"""
    stat = file_path.stat()
    return {
        "name": file_path.name,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "age_hours": round(
            (datetime.now() - datetime.fromtimestamp(stat.st_mtime)).total_seconds() / 3600,
            1,
        ),
        "is_history": file_path.name.startswith("history_"),
        "is_error": file_path.name.startswith("error_"),
    }


@cache_bp.route("/")
def cache_viewer():
    """Render the cache viewer page"""
    return render_template("cache.html")


@cache_bp.route("/api/files")
def list_cache_files():
    """List all cache files with metadata"""
    client = get_cache_client()
    if not client:
        return jsonify({"error": "Cache client not initialized"}), 500

    try:
        cache_dir = client.cache_dir
        files = []

        # Get all JSON files
        for file_path in cache_dir.glob("*.json"):
            files.append(get_file_info(file_path))

        # Sort by modified time (newest first)
        files.sort(key=lambda x: x["modified"], reverse=True)

        # Get cache statistics
        total_size = sum(f["size"] for f in files)
        history_files = [f for f in files if f["is_history"]]
        error_files = [f for f in files if f["is_error"]]
        current_files = [f for f in files if not f["is_history"] and not f["is_error"]]

        return jsonify(
            {
                "files": files,
                "stats": {
                    "total_files": len(files),
                    "total_size": total_size,
                    "history_files": len(history_files),
                    "error_files": len(error_files),
                    "current_files": len(current_files),
                    "cache_validity_minutes": client.cache_validity.total_seconds() / 60,
                    "cache_retention_hours": client.cache_retention.total_seconds() / 3600,
                },
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cache_bp.route("/api/file/<filename>")
def get_cache_file(filename):
    """Get contents of a specific cache file"""
    client = get_cache_client()
    if not client:
        return jsonify({"error": "Cache client not initialized"}), 500

    # Validate filename (security check)
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    try:
        file_path = client.cache_dir / filename
        if not file_path.exists():
            return jsonify({"error": "File not found"}), 404

        with open(file_path, "r") as f:
            content = json.load(f)

        # Get file info
        info = get_file_info(file_path)

        # Check if this is the current cache
        cache_key = client._get_cache_key("full_data")
        is_current = filename == f"{cache_key}.json"
        is_valid = client._is_cache_valid(file_path) if is_current else False

        return jsonify(
            {
                "filename": filename,
                "info": info,
                "content": content,
                "is_current": is_current,
                "is_valid": is_valid,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cache_bp.route("/api/delete/<filename>", methods=["DELETE"])
def delete_cache_file(filename):
    """Delete a specific cache file"""
    client = get_cache_client()
    if not client:
        return jsonify({"error": "Cache client not initialized"}), 500

    # Validate filename
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    try:
        file_path = client.cache_dir / filename
        if not file_path.exists():
            return jsonify({"error": "File not found"}), 404

        # Don't delete the current valid cache
        cache_key = client._get_cache_key("full_data")
        if filename == f"{cache_key}.json" and client._is_cache_valid(file_path):
            return jsonify({"error": "Cannot delete current valid cache"}), 400

        file_path.unlink()
        return jsonify({"success": True, "message": f"Deleted {filename}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cache_bp.route("/api/clear-old", methods=["POST"])
def clear_old_cache():
    """Clear cache files older than retention period"""
    client = get_cache_client()
    if not client:
        return jsonify({"error": "Cache client not initialized"}), 500

    try:
        # Run the cleanup
        client._cleanup_old_cache_files()
        return jsonify({"success": True, "message": "Old cache files cleaned up"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@cache_bp.route("/api/force-update", methods=["POST"])
def force_cache_update():
    """Force a cache update from the API"""
    client = get_cache_client()
    if not client:
        return jsonify({"error": "Cache client not initialized"}), 500

    try:
        # Use the force_cache_update method we created earlier
        data = client.force_cache_update()
        if data:
            return jsonify(
                {
                    "success": True,
                    "message": "Cache updated successfully",
                    "data": {
                        "battery_level": data.get("battery", {}).get("level"),
                        "range": data.get("battery", {}).get("range"),
                        "temperature": data.get("raw_data", {}).get("airTemp", {}).get("value"),
                        "timestamp": data.get("timestamp"),
                    },
                }
            )
        else:
            return jsonify({"error": "Failed to update cache"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
