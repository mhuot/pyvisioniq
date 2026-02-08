import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify, request

external_bp = Blueprint('external', __name__)

logger = logging.getLogger('external_api')

# Add a file handler so external API activity lands in logs/collector.log
_log_dir = Path('logs')
_log_dir.mkdir(exist_ok=True)
_fh = logging.FileHandler(_log_dir / 'collector.log')
_fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(_fh)
logger.setLevel(logging.INFO)


def _check_api_key():
    """Validate the X-API-KEY header against the configured EXTERNAL_API_KEY."""
    expected_key = os.getenv('EXTERNAL_API_KEY')
    if not expected_key:
        return False, "EXTERNAL_API_KEY not configured on server"
    provided_key = request.headers.get('X-API-KEY')
    if not provided_key or provided_key != expected_key:
        return False, "Invalid or missing API key"
    return True, None


@external_bp.route('/api/external/battery', methods=['POST'])
def post_external_battery():
    """Accept 12V battery data from an external device (e.g. Raspberry Pi + BM2).

    Expected JSON payload:
        {
            "voltage": float,   # required
            "soc": float,       # required
            "temp": float       # optional
        }

    Headers:
        X-API-KEY: <EXTERNAL_API_KEY>
    """
    # --- Auth ---
    auth_ok, auth_error = _check_api_key()
    if not auth_ok:
        logger.warning("[EXTERNAL_API] Unauthorized request from %s: %s",
                       request.remote_addr, auth_error)
        return jsonify({"error": auth_error}), 401

    # --- Parse body ---
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    voltage = data.get('voltage')
    soc = data.get('soc')
    temp = data.get('temp')

    # --- Validate required fields ---
    errors = []
    if voltage is None:
        errors.append("'voltage' is required")
    elif not isinstance(voltage, (int, float)):
        errors.append("'voltage' must be a number")

    if soc is None:
        errors.append("'soc' is required")
    elif not isinstance(soc, (int, float)):
        errors.append("'soc' must be a number")

    if temp is not None and not isinstance(temp, (int, float)):
        errors.append("'temp' must be a number if provided")

    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # --- Persist via CSVStorage ---
    from flask import current_app
    storage = current_app.config.get('storage')
    if storage is None:
        logger.error("[EXTERNAL_API] CSVStorage not available in app config")
        return jsonify({"error": "Storage not available"}), 500

    row = storage.store_external_battery(
        voltage=float(voltage),
        soc=float(soc),
        temperature=float(temp) if temp is not None else None,
    )

    logger.info("[EXTERNAL_API] Stored 12V battery data: voltage=%.2f soc=%.1f temp=%s",
                voltage, soc, temp)

    return jsonify({"status": "created", "data": row}), 201
