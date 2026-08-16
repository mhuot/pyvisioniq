"""Tests for the Home Assistant MQTT bridge's pure message builders."""

import json

from src.utils.ha_mqtt import discovery_messages, state_messages, values_from_vehicle


def test_discovery_covers_every_sensor_with_device_and_availability():
    messages = discovery_messages("homeassistant", "pyvisionic")
    assert len(messages) == 21  # 12 sensors + 8 binary + device tracker
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
        "raw_data": {
            "vehicleStatus": {
                "battery": {"batSoc": 85},
                "doorLockStatus": "true",
                "tirePressure": {"tirePressureWarningLampAll": 0},
                "evStatus": {
                    "batteryPlugin": 2,
                    "remainTime2": {"atc": {"unit": 1, "value": 530}},
                },
            }
        },
    }
    values = values_from_vehicle(data, forces_today=2)
    assert values["battery_level"] == 81
    assert values["twelve_v"] == 85
    assert values["call_class"] == "cached"
    assert values["forces_today"] == 2
    assert values["is_charging"] is True
    assert values["plugged_in"] is True
    assert values["doors_unlocked"] is False
    assert values["tire_warning"] is False
    assert values["charge_time_remaining"] == 530
    assert values["opening_open"] is False
    assert values["climate_on"] is False


def test_detail_attributes_name_what_is_open_and_running():
    from src.utils.ha_mqtt import detail_attributes

    detail = detail_attributes(
        {
            "raw_data": {
                "vehicleStatus": {
                    "doorOpen": {"frontLeft": 1, "frontRight": 0},
                    "trunkOpen": True,
                    "airCtrlOn": True,
                    "steerWheelHeat": 1,
                }
            }
        }
    )
    assert detail["opening_open"]["open"] == ["door frontLeft", "trunk"]
    assert set(detail["climate_on"]["active"]) == {"hvac", "steering wheel heat"}


def test_warnings_walk_nested_lamp_flags():
    from src.utils.ha_mqtt import warnings_from_status

    warnings = warnings_from_status(
        {
            "washerFluidStatus": True,
            "lampWireStatus": {"headLamp": {"leftLowLamp": True}},
            "breakOilStatus": False,
        }
    )
    assert warnings == ["washer fluid low", "exterior lamp fault"]


def test_device_block_promotes_vehicle_identity():
    from src.utils.ha_mqtt import device_from_vehicle

    device = device_from_vehicle(
        {
            "raw_data": {
                "vehicleDetails": {
                    "nickName": "2024 IONIQ 5",
                    "modelYear": "2024",
                    "modelCode": "IONIQ 5",
                    "trim": "LIMITED",
                    "vin": "KM8TEST",
                }
            }
        }
    )
    assert device["name"] == "2024 IONIQ 5"
    assert device["model"] == "2024 IONIQ 5 LIMITED"
    assert device["serial_number"] == "KM8TEST"
    assert device["identifiers"] == ["pyvisionic"]  # updates in place


def test_location_requires_a_fix():
    from src.utils.ha_mqtt import location_from_vehicle

    state, attrs = location_from_vehicle({"location": {"latitude": 44.9, "longitude": -93.1}})
    assert state == "gps" and attrs["latitude"] == 44.9
    assert location_from_vehicle({}) == (None, None)
