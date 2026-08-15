"""Publish vehicle state and derived metrics to Home Assistant over MQTT.

One API consumer, many surfaces: the collector already spends the Bluelink
budget, so Home Assistant gets its entities from us rather than running the
kia_uvo integration against the same account and doubling the draw.

Uses MQTT Discovery: retained config messages under ``homeassistant/...``
make entities appear automatically, grouped under one PyVisionic device, with
availability tracked via a last-will message. Publishing is event-driven off
the collector's poll cycle, so it adds zero API calls.

Configuration (all optional; the publisher is inert without MQTT_HOST):
    MQTT_HOST, MQTT_PORT (1883), MQTT_USER, MQTT_PASS,
    MQTT_DISCOVERY_PREFIX (homeassistant), MQTT_BASE_TOPIC (pyvisionic)
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt

    PAHO_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    PAHO_AVAILABLE = False

DEVICE = {
    "identifiers": ["pyvisionic"],
    "name": "PyVisionic EV",
    "manufacturer": "PyVisionic",
    "model": "Bluelink telemetry bridge",
}

# key, friendly name, unit, device_class, state_class
SENSORS = [
    ("battery_level", "Battery", "%", "battery", "measurement"),
    ("range_km", "Range", "km", "distance", "measurement"),
    ("charging_power", "Charging power", "kW", "power", "measurement"),
    ("odometer_km", "Odometer", "km", "distance", "total_increasing"),
    ("twelve_v", "12V battery", "%", "battery", "measurement"),
    ("temperature", "Ambient temperature", "°C", "temperature", "measurement"),
    ("forces_today", "Force refreshes today", None, None, "measurement"),
    ("call_class", "Last fetch class", None, None, None),
    ("charge_efficiency", "Wall-to-battery efficiency", "%", None, "measurement"),
    ("est_range_full", "Estimated range at 100%", "mi", "distance", "measurement"),
    ("trip_readiness", "Trip readiness", None, None, None),
]

BINARY_SENSORS = [
    ("is_charging", "Charging", "battery_charging"),
    ("data_fresh", "Vehicle data fresh", None),
]


def discovery_messages(prefix, base):
    """Retained config payloads that make HA create the entities."""
    messages = []
    availability = {
        "availability_topic": f"{base}/status",
        "device": DEVICE,
    }
    for key, name, unit, device_class, state_class in SENSORS:
        config = {
            "name": name,
            "unique_id": f"pyvisionic_{key}",
            "state_topic": f"{base}/{key}/state",
            "json_attributes_topic": f"{base}/{key}/attributes",
            **availability,
        }
        if unit:
            config["unit_of_measurement"] = unit
        if device_class:
            config["device_class"] = device_class
        if state_class:
            config["state_class"] = state_class
        messages.append((f"{prefix}/sensor/pyvisionic/{key}/config", json.dumps(config)))
    for key, name, device_class in BINARY_SENSORS:
        config = {
            "name": name,
            "unique_id": f"pyvisionic_{key}",
            "state_topic": f"{base}/{key}/state",
            "payload_on": "ON",
            "payload_off": "OFF",
            **availability,
        }
        if device_class:
            config["device_class"] = device_class
        messages.append((f"{prefix}/binary_sensor/pyvisionic/{key}/config", json.dumps(config)))
    return messages


def state_messages(base, values, attributes=None):
    """State (and optional attribute) payloads for one publish cycle.

    ``values`` maps sensor keys to plain states; None values are skipped so a
    metric that cannot be computed this cycle simply keeps its last state.
    """
    messages = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "ON" if value else "OFF"
        messages.append((f"{base}/{key}/state", str(value)))
    for key, attrs in (attributes or {}).items():
        if attrs:
            messages.append((f"{base}/{key}/attributes", json.dumps(attrs)))
    return messages


def values_from_vehicle(data, forces_today=None):
    """Extract the raw-state sensor values from a collector payload."""
    battery = data.get("battery") or {}
    raw_status = (data.get("raw_data") or {}).get("vehicleStatus", {})
    return {
        "battery_level": battery.get("level"),
        "range_km": battery.get("range"),
        "charging_power": battery.get("charging_power"),
        "odometer_km": data.get("odometer"),
        "twelve_v": (raw_status.get("battery") or {}).get("batSoc"),
        "is_charging": bool(battery.get("is_charging")),
        "data_fresh": bool(data.get("hyundai_data_fresh")),
        "call_class": data.get("call_class"),
        "forces_today": forces_today,
    }


class HAPublisher:
    """Thin connection wrapper; a construction failure disables publishing."""

    def __init__(self):
        self.enabled = False
        host = os.getenv("MQTT_HOST", "")
        if not host or not PAHO_AVAILABLE:
            if host and not PAHO_AVAILABLE:
                logger.warning("MQTT_HOST set but paho-mqtt is not installed")
            return
        self.base = os.getenv("MQTT_BASE_TOPIC", "pyvisionic")
        self.prefix = os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant")
        try:
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="pyvisionic-collector",
            )
            user = os.getenv("MQTT_USER")
            if user:
                self.client.username_pw_set(user, os.getenv("MQTT_PASS", ""))
            self.client.will_set(f"{self.base}/status", "offline", retain=True)
            self.client.connect(host, int(os.getenv("MQTT_PORT", "1883")), keepalive=120)
            self.client.loop_start()
            self.client.publish(f"{self.base}/status", "online", retain=True)
            for topic, payload in discovery_messages(self.prefix, self.base):
                self.client.publish(topic, payload, retain=True)
            self.enabled = True
            logger.info("MQTT publishing to %s enabled", host)
        except Exception as connect_error:  # pylint: disable=broad-except
            logger.warning("MQTT disabled: %s", connect_error)

    def publish(self, values, attributes=None):
        """Best-effort publish; failures log and never disturb collection."""
        if not self.enabled:
            return
        try:
            # Retained: polls are 60-150 minutes apart, so a subscriber that
            # connects between them (HA restarting, integration added later)
            # should get the last known state immediately instead of showing
            # "unknown" until the next cycle.
            for topic, payload in state_messages(self.base, values, attributes):
                self.client.publish(topic, payload, retain=True)
        except Exception as publish_error:  # pylint: disable=broad-except
            logger.warning("MQTT publish failed: %s", publish_error)
