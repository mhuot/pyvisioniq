import logging
import os
from datetime import datetime
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


def _validate_reading(reading):
    """Validate a single reading dict.  Returns (cleaned_row, errors)."""
    errors = []
    voltage = reading.get('voltage')
    soc = reading.get('soc')
    temp = reading.get('temp')
    ts = reading.get('timestamp')

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

    if ts is not None and not isinstance(ts, str):
        errors.append("'timestamp' must be an ISO-format string if provided")

    if errors:
        return None, errors

    # Resolve timestamp: use supplied value or fall back to server time
    if ts:
        # Normalise to the canonical format used in the CSV
        try:
            parsed = datetime.fromisoformat(ts)
            ts_str = parsed.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None, ["'timestamp' is not a valid ISO-format datetime"]
    else:
        ts_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    row = {
        'timestamp': ts_str,
        'voltage': float(voltage),
        'soc': float(soc),
        'temperature': float(temp) if temp is not None else None,
    }
    return row, []


@external_bp.route('/api/external/battery', methods=['POST'])
def post_external_battery():
    """Accept 12V battery data from an external device (e.g. Raspberry Pi + BM2).

    Accepts a single reading **or** a batch (JSON array).

    Single reading:
        {"voltage": 12.6, "soc": 95, "temp": 22.5, "timestamp": "2025-06-01T14:30:00"}

    Batch:
        [{"voltage": 12.6, "soc": 95, "timestamp": "..."}, ...]

    The ``timestamp`` field is optional; when omitted the server's current
    time is used.  Duplicate timestamps (compared against the last ~500
    rows) are silently skipped.

    Headers:
        X-API-KEY: <EXTERNAL_API_KEY>

    Response (201):
        {"status": "success", "added": 12, "skipped": 88}
    """
    # --- Auth ---
    auth_ok, auth_error = _check_api_key()
    if not auth_ok:
        logger.warning("[EXTERNAL_API] Unauthorized request from %s: %s",
                       request.remote_addr, auth_error)
        return jsonify({"error": auth_error}), 401

    # --- Parse body ---
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    # Normalise to a list so single-object and batch use the same path
    if isinstance(payload, dict):
        readings = [payload]
    elif isinstance(payload, list):
        readings = payload
    else:
        return jsonify({"error": "Payload must be a JSON object or array"}), 400

    if not readings:
        return jsonify({"error": "Payload array is empty"}), 400

    # --- Validate every reading ---
    rows = []
    all_errors = []
    for i, reading in enumerate(readings):
        row, errs = _validate_reading(reading)
        if errs:
            all_errors.append({"index": i, "errors": errs})
        else:
            rows.append(row)

    if all_errors and not rows:
        # Everything failed validation
        return jsonify({"error": "Validation failed", "details": all_errors}), 400

    # --- Persist via CSVStorage.write_unique_rows ---
    from flask import current_app
    storage = current_app.config.get('storage')
    if storage is None:
        logger.error("[EXTERNAL_API] CSVStorage not available in app config")
        return jsonify({"error": "Storage not available"}), 500

    added, skipped = storage.write_unique_rows(rows)

    logger.info("[EXTERNAL_API] Batch result: added=%d skipped=%d validation_errors=%d (from %s)",
                added, skipped, len(all_errors), request.remote_addr)

    response = {"status": "success", "added": added, "skipped": skipped}
    if all_errors:
        response["validation_errors"] = all_errors

    return jsonify(response), 201
