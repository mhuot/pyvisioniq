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

# The identifiers stay "pyvisionic" so device-block changes update the
# existing HA device in place rather than creating a second one. The rest is
# populated from the vehicle payload at publish time when available.
DEVICE = {
    "identifiers": ["pyvisionic"],
    "name": "PyVisionic EV",
    "manufacturer": "Hyundai",
    "model": "Bluelink telemetry bridge",
}


def device_from_vehicle(data):
    """A device block presenting like a first-class car integration."""
    details = (data.get("raw_data") or {}).get("vehicleDetails", {}) or {}
    device = dict(DEVICE)
    if details.get("nickName"):
        device["name"] = details["nickName"]
    model = " ".join(
        str(part)
        for part in (details.get("modelYear"), details.get("modelCode"), details.get("trim"))
        if part
    )
    if model:
        device["model"] = model
    if details.get("vin"):
        device["serial_number"] = details["vin"]
    return device


# key, friendly name, unit, device_class, state_class, icon, entity_category
SENSORS = [
    ("battery_level", "Battery", "%", "battery", "measurement", None, None),
    ("range_km", "Range", "km", "distance", "measurement", "mdi:map-marker-distance", None),
    ("charging_power", "Charging power", "kW", "power", "measurement", "mdi:ev-station", None),
    (
        "charge_time_remaining",
        "Time to charge limit",
        "min",
        "duration",
        "measurement",
        "mdi:battery-clock",
        None,
    ),
    ("odometer_km", "Odometer", "km", "distance", "total_increasing", "mdi:counter", None),
    ("twelve_v", "12V battery", "%", "battery", "measurement", "mdi:car-battery", "diagnostic"),
    ("temperature", "Ambient temperature", "°C", "temperature", "measurement", None, None),
    (
        "forces_today",
        "Force refreshes today",
        None,
        None,
        "measurement",
        "mdi:sleep-off",
        "diagnostic",
    ),
    ("call_class", "Last fetch class", None, None, None, "mdi:swap-vertical", "diagnostic"),
    (
        "charge_efficiency",
        "Wall-to-battery efficiency",
        "%",
        None,
        "measurement",
        "mdi:transmission-tower",
        None,
    ),
    (
        "est_range_full",
        "Estimated range at 100%",
        "mi",
        "distance",
        "measurement",
        "mdi:map-marker-radius",
        None,
    ),
    ("trip_readiness", "Trip readiness", None, None, None, "mdi:routes-clock", None),
]

# key, friendly name, device_class, entity_category
BINARY_SENSORS = [
    ("is_charging", "Charging", "battery_charging", None),
    ("plugged_in", "Plugged in", "plug", None),
    ("doors_unlocked", "Doors", "lock", None),
    ("tire_warning", "Tire pressure", "problem", None),
    ("opening_open", "Doors, windows, trunk", "opening", None),
    ("climate_on", "Climate", "running", None),
    ("data_fresh", "Vehicle data fresh", None, "diagnostic"),
]


def discovery_messages(prefix, base, device=None):
    """Retained config payloads that make HA create the entities."""
    messages = []
    availability = {
        "availability_topic": f"{base}/status",
        "device": device or DEVICE,
    }
    for key, name, unit, device_class, state_class, icon, category in SENSORS:
        config = {
            "name": name,
            # has_entity_name + default_entity_id give clean, prefixed ids
            # (sensor.pyvisionic_battery_level) instead of collision-prone
            # bare names. object_id did the same job until HA Core 2026.4
            # removed it; default_entity_id is its domain-prefixed successor.
            "has_entity_name": True,
            "default_entity_id": f"sensor.pyvisionic_{key}",
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
        if icon:
            config["icon"] = icon
        if category:
            config["entity_category"] = category
        messages.append((f"{prefix}/sensor/pyvisionic/{key}/config", json.dumps(config)))
    for key, name, device_class, category in BINARY_SENSORS:
        config = {
            "name": name,
            "has_entity_name": True,
            "default_entity_id": f"binary_sensor.pyvisionic_{key}",
            "unique_id": f"pyvisionic_{key}",
            "state_topic": f"{base}/{key}/state",
            "json_attributes_topic": f"{base}/{key}/attributes",
            "payload_on": "ON",
            "payload_off": "OFF",
            **availability,
        }
        if device_class:
            config["device_class"] = device_class
        if category:
            config["entity_category"] = category
        messages.append((f"{prefix}/binary_sensor/pyvisionic/{key}/config", json.dumps(config)))

    # The car on the map: a GPS device_tracker fed from the location topic.
    messages.append(
        (
            f"{prefix}/device_tracker/pyvisionic/location/config",
            json.dumps(
                {
                    "name": "Location",
                    "has_entity_name": True,
                    "default_entity_id": "device_tracker.pyvisionic_location",
                    "unique_id": "pyvisionic_location",
                    "state_topic": f"{base}/location/state",
                    "json_attributes_topic": f"{base}/location/attributes",
                    "source_type": "gps",
                    **availability,
                }
            ),
        )
    )
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
    ev_status = raw_status.get("evStatus") or {}
    tires = raw_status.get("tirePressure") or {}

    charging = bool(battery.get("is_charging"))
    remain = ((ev_status.get("remainTime2") or {}).get("atc") or {}).get("value")

    def truthy(value):
        return str(value).lower() == "true" or value is True

    openings = {}
    for side, flag in (raw_status.get("doorOpen") or {}).items():
        if flag:
            openings[f"door {side}"] = True
    for side, flag in (raw_status.get("windowOpen") or {}).items():
        if flag:
            openings[f"window {side}"] = True
    if truthy(raw_status.get("trunkOpen")):
        openings["trunk"] = True
    if truthy(raw_status.get("hoodOpen")):
        openings["hood"] = True

    climate = {}
    if truthy(raw_status.get("airCtrlOn")):
        climate["hvac"] = True
    if truthy(raw_status.get("defrost")):
        climate["defrost"] = True
    if raw_status.get("steerWheelHeat"):
        climate["steering wheel heat"] = True
    if raw_status.get("sideBackWindowHeat"):
        climate["rear window heat"] = True

    return {
        "battery_level": battery.get("level"),
        "range_km": battery.get("range"),
        "charging_power": battery.get("charging_power"),
        # atc only means "time to the charge limit" while actually charging;
        # parked, the API leaves the last value behind.
        "charge_time_remaining": remain if charging else 0,
        "odometer_km": data.get("odometer"),
        "twelve_v": (raw_status.get("battery") or {}).get("batSoc"),
        "is_charging": charging,
        "plugged_in": (ev_status.get("batteryPlugin") or 0) > 0,
        # binary_sensor device_class lock reads ON as unlocked.
        "doors_unlocked": str(raw_status.get("doorLockStatus")).lower() != "true",
        "tire_warning": any(int(v or 0) for v in tires.values()),
        "opening_open": bool(openings),
        "climate_on": bool(climate),
        "data_fresh": bool(data.get("hyundai_data_fresh")),
        "call_class": data.get("call_class"),
        "forces_today": forces_today,
    }


def detail_attributes(data):
    """Which openings and climate loads are active, for the attribute topics."""
    raw_status = (data.get("raw_data") or {}).get("vehicleStatus", {})

    def truthy(value):
        return str(value).lower() == "true" or value is True

    open_list = [
        f"door {side}" for side, flag in (raw_status.get("doorOpen") or {}).items() if flag
    ] + [f"window {side}" for side, flag in (raw_status.get("windowOpen") or {}).items() if flag]
    if truthy(raw_status.get("trunkOpen")):
        open_list.append("trunk")
    if truthy(raw_status.get("hoodOpen")):
        open_list.append("hood")

    active = []
    if truthy(raw_status.get("airCtrlOn")):
        active.append("hvac")
    if truthy(raw_status.get("defrost")):
        active.append("defrost")
    if raw_status.get("steerWheelHeat"):
        active.append("steering wheel heat")
    if raw_status.get("sideBackWindowHeat"):
        active.append("rear window heat")

    return {
        "opening_open": {"open": open_list or ["none"]},
        "climate_on": {"active": active or ["none"]},
    }


def location_from_vehicle(data):
    """State + attributes for the device tracker, or (None, None) without a fix."""
    location = data.get("location") or {}
    lat, lon = location.get("latitude"), location.get("longitude")
    if lat is None or lon is None:
        return None, None
    return "gps", {"latitude": lat, "longitude": lon, "gps_accuracy": 10}


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

    def publish(self, values, attributes=None, device=None):
        """Best-effort publish; failures log and never disturb collection."""
        if not self.enabled:
            return
        try:
            # Discovery configs are republished every cycle, not just at
            # startup: deleting the device in HA wipes the retained configs
            # from the broker (HA publishes empty payloads to prevent
            # rediscovery), which otherwise leaves the bridge silently dead
            # until the next collector restart. Idempotent and tiny.
            for topic, payload in discovery_messages(self.prefix, self.base, device):
                self.client.publish(topic, payload, retain=True)
            # Retained: polls are 60-150 minutes apart, so a subscriber that
            # connects between them (HA restarting, integration added later)
            # should get the last known state immediately instead of showing
            # "unknown" until the next cycle.
            for topic, payload in state_messages(self.base, values, attributes):
                self.client.publish(topic, payload, retain=True)
        except Exception as publish_error:  # pylint: disable=broad-except
            logger.warning("MQTT publish failed: %s", publish_error)
