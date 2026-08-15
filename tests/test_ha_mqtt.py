"""Tests for the Home Assistant MQTT bridge's pure message builders."""

import json

from src.utils.ha_mqtt import discovery_messages, state_messages, values_from_vehicle


def test_discovery_covers_every_sensor_with_device_and_availability():
    messages = discovery_messages("homeassistant", "pyvisionic")
    assert len(messages) == 13  # 11 sensors + 2 binary
    for topic, payload in messages:
        config = json.loads(payload)
        assert topic.startswith("homeassistant/")
        assert config["availability_topic"] == "pyvisionic/status"
        assert config["device"]["identifiers"] == ["pyvisionic"]
        assert config["unique_id"].startswith("pyvisionic_")


def test_state_messages_skip_none_and_render_booleans():
    messages = dict(
        state_messages("pyvisionic", {"battery_level": 78, "twelve_v": None, "is_charging": True})
    )
    assert messages["pyvisionic/battery_level/state"] == "78"
    assert messages["pyvisionic/is_charging/state"] == "ON"
    assert "pyvisionic/twelve_v/state" not in messages


def test_values_from_vehicle_reads_nested_payload():
    data = {
        "battery": {"level": 81, "is_charging": True, "charging_power": 1.3, "range": 420},
        "odometer": 43700,
        "hyundai_data_fresh": True,
        "call_class": "cached",
        "raw_data": {"vehicleStatus": {"battery": {"batSoc": 85}}},
    }
    values = values_from_vehicle(data, forces_today=2)
    assert values["battery_level"] == 81
    assert values["twelve_v"] == 85
    assert values["call_class"] == "cached"
    assert values["forces_today"] == 2
    assert values["is_charging"] is True
