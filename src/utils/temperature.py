"""Normalization for the API's HVAC temperature values."""

import logging

logger = logging.getLogger(__name__)

# The climate dial reports "LO" below 62F and "HI" above 82F; "OFF" means the
# climate system is off. Note this value is always the DESIRED temperature
# (the dial setting), never a measured cabin temperature.
DIAL_EXTREMES_FAHRENHEIT = {"LO": 0.0, "HI": 100.0}


def normalize_airtemp_fahrenheit(value):
    """Normalize the API's airTemp value to a float in Fahrenheit.

    Maps the dial extremes ("LO" -> 0, "HI" -> 100), returns None for "OFF"
    and for missing or unrecognizable values, and coerces numeric strings
    (the API intermittently returns e.g. "72" instead of 72).
    """
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in DIAL_EXTREMES_FAHRENHEIT:
            return DIAL_EXTREMES_FAHRENHEIT[normalized]
        if normalized == "OFF":
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Non-numeric vehicle airTemp value: %r", value)
        return None
