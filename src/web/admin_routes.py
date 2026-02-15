"""
admin_routes.py
Blueprint providing /admin/* routes for storage diagnostics.
Protected by admin_required — when AUTH_ENABLED=false, all users have access.
"""

import logging

from flask import Blueprint, current_app, jsonify, render_template, request

from src.web.auth import admin_required

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _get_oracle_storage():
    """Return the OracleStorage instance from whichever backend is active.

    Works with oracle, dual, or returns None for csv-only backends.
    """
    storage = current_app.config.get("storage")
    if storage is None:
        return None

    # Direct OracleStorage
    if hasattr(storage, "get_api_responses"):
        return storage

    # DualWriteStorage wraps an OracleStorage
    oracle = getattr(storage, "oracle_storage", None)
    if oracle and hasattr(oracle, "get_api_responses"):
        return oracle

    return None


@admin_bp.route("/")
@admin_required
def admin_dashboard():
    """Render the admin dashboard page."""
    return render_template("admin.html")


@admin_bp.route("/api/storage-stats")
@admin_required
def storage_stats():
    """Return storage diagnostics as JSON."""
    storage = current_app.config.get("storage")
    if storage is None:
        return jsonify({"error": "Storage not initialized"}), 500

    try:
        stats = storage.get_storage_stats()
        return jsonify(stats)
    except Exception as exc:
        logger.error("Error getting storage stats: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/api/raw-responses")
@admin_required
def raw_responses():
    """Return paginated list of raw API responses (metadata only)."""
    oracle = _get_oracle_storage()
    if oracle is None:
        return jsonify({"error": "Oracle storage not available"}), 404

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    per_page = min(per_page, 100)

    try:
        result = oracle.get_api_responses(page=page, per_page=per_page)
        return jsonify(result)
    except Exception as exc:
        logger.error("Error fetching raw responses: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/api/raw-response/<int:response_id>")
@admin_required
def raw_response_detail(response_id):
    """Return full JSON for a single raw API response."""
    oracle = _get_oracle_storage()
    if oracle is None:
        return jsonify({"error": "Oracle storage not available"}), 404

    try:
        result = oracle.get_api_response_by_id(response_id)
        if result is None:
            return jsonify({"error": "Response not found"}), 404
        return jsonify(result)
    except Exception as exc:
        logger.error(
            "Error fetching raw response %d: %s", response_id, exc, exc_info=True
        )
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/api/storage-consumption")
@admin_required
def storage_consumption():
    """Return api_responses storage consumption breakdown."""
    oracle = _get_oracle_storage()
    if oracle is None:
        return jsonify({"error": "Oracle storage not available"}), 404

    try:
        result = oracle.get_api_response_storage_stats()
        return jsonify(result)
    except Exception as exc:
        logger.error("Error fetching storage consumption: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
