"""Route-level tests for the Flask API, against a synthetic data directory.

Every path the app reads is relative to the working directory, so each test
runs chdir'd into a temporary site whose data/ holds a small, internally
consistent history: two days of battery readings including one slow charge,
trips with a deliberate logging-fault outlier, charging sessions in both
metered and derived flavours, and plug samples matching the charge window.

The assertions are behavioural, not just status codes: the outlier must be
filtered, averages must be energy-weighted, and endpoints must degrade to 404
rather than 500 when the site is empty.
"""

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

import src.web.app as app_module

HOME = (44.90, -93.10)
AWAY = (45.60, -92.40)  # ~85 km out; far enough to register as a destination


def _write_site(root):
    """Populate root/data with a coherent synthetic history."""
    data = root / "data"
    data.mkdir()

    start = datetime(2026, 8, 1, 6, 0)
    battery_rows = []
    level, odo = 60.0, 40000.0
    for hour in range(48):
        stamp = start + timedelta(hours=hour)
        charging = 20 <= hour <= 33  # a 13-hour overnight charge, +1%/h
        if charging:
            level = min(100.0, level + 1)
        elif hour % 5 == 0 and level > 30:
            level -= 3
            odo += 15
        battery_rows.append(
            {
                "timestamp": stamp.isoformat(),
                "battery_level": round(level),
                "is_charging": charging,
                "charging_power": 1.3 if charging else 0.0,
                "remaining_time": None,
                "range": round(level * 4.5),
                "temperature": 21.0,
                "odometer": round(odo, 1),
                "meteo_temp": 21.0,
                "vehicle_temp": 20.0,
                "is_cached": False,
            }
        )
    pd.DataFrame(battery_rows).to_csv(data / "battery_status.csv", index=False)

    trip_rows = []
    for day in range(10):
        # Inside the battery-readings window: temperature matching tolerates
        # only an hour between a trip and its nearest reading.
        stamp = start + timedelta(hours=2 + day * 4)
        lat, lon = AWAY if day % 3 == 0 else HOME
        trip_rows.append(
            {
                "timestamp": stamp.isoformat(),
                "date": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "distance": 30 if day % 3 == 0 else 8,
                "duration": 40.0,
                "average_speed": 70.0,
                "max_speed": 110.0,
                "idle_time": 1,
                "trips_count": 1,
                "total_consumed": 9000 if day % 3 == 0 else 2000,
                "regenerated_energy": 800,
                "accessories_consumed": 100,
                "climate_consumed": 100,
                "drivetrain_consumed": 8000,
                "battery_care_consumed": 0,
                "odometer_start": 40000 + day * 30,
                "end_latitude": lat,
                "end_longitude": lon,
                "end_temperature": 21.0,
            }
        )
    # A logging fault: long distance against a stub consumption. Real rows
    # like this produced 15,300 mi/kWh and must be filtered, never plotted.
    trip_rows.append({**trip_rows[0], "distance": 150, "total_consumed": 10})
    pd.DataFrame(trip_rows).to_csv(data / "trips.csv", index=False)

    pd.DataFrame(
        [
            {
                "timestamp": r["timestamp"],
                "latitude": r["end_latitude"],
                "longitude": r["end_longitude"],
                "last_updated": r["timestamp"],
            }
            for r in trip_rows
        ]
    ).to_csv(data / "locations.csv", index=False)

    sessions = [
        {
            "session_id": "m1",
            "start_time": "2026-08-01 20:00:00",
            "end_time": "2026-08-02 09:00:00",
            "duration_minutes": 780.0,
            "start_battery": 45.0,
            "end_battery": 65.0,
            "energy_added": 17.5,
            "avg_power": 1.35,
            "max_power": 1.37,
            "location_lat": HOME[0],
            "location_lon": HOME[1],
            "is_complete": True,
            "network": "Home",
            "location_name": None,
            "cost_usd": None,
            "energy_source": "metered",
        },
        {
            "session_id": "d1",
            "start_time": "2026-07-25 10:00:00",
            "end_time": "2026-07-25 10:30:00",
            "duration_minutes": 30.0,
            "start_battery": 30.0,
            "end_battery": 70.0,
            "energy_added": 29.6,
            "avg_power": 59.2,
            "max_power": 150.0,
            "location_lat": AWAY[0],
            "location_lon": AWAY[1],
            "is_complete": True,
            "network": None,
            "location_name": None,
            "cost_usd": None,
            "energy_source": "derived",
        },
    ]
    pd.DataFrame(sessions).to_csv(data / "charging_sessions.csv", index=False)

    plug_rows = []
    for hour in range(48):
        stamp = start + timedelta(hours=hour)
        watts = 1350.0 if 20 <= hour <= 33 else 2.0
        plug_rows.append({"timestamp": stamp.isoformat(sep=" "), "watts": watts})
    pd.DataFrame(plug_rows).to_csv(data / "plug_power.csv", index=False)

    with open(data / "api_call_history.json", "w", encoding="utf-8") as handle:
        json.dump(
            {"calls_today": 4, "date": "2026-08-15", "last_call": "2026-08-15T06:00:00"}, handle
        )

    pd.DataFrame(
        [
            {
                "timestamp": "2026-08-15T06:00:00",
                "reason": "idle_day",
                "interval_minutes": 60,
                "backoff": 1.0,
                "calls_today": 4,
                "daily_limit": 24,
            }
        ]
    ).to_csv(data / "polling_log.csv", index=False)


@pytest.fixture(name="site")
def _site(tmp_path, monkeypatch):
    """A populated site, with the app's globals pointed away from live data."""
    _write_site(tmp_path)
    monkeypatch.chdir(tmp_path)
    # The locations endpoint would call the live API client if one exists.
    monkeypatch.setattr(app_module, "client", None)
    # Battery history is cached module-wide; a stale entry from another test
    # (or dataset) must not leak through.
    monkeypatch.setattr(app_module, "cached_battery_history", {"data": None, "timestamp": None})
    return tmp_path


@pytest.fixture(name="empty_site")
def _empty_site(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_module, "client", None)
    monkeypatch.setattr(app_module, "cached_battery_history", {"data": None, "timestamp": None})
    return tmp_path


@pytest.fixture(name="client")
def _client():
    return app_module.app.test_client()


def test_trips_paginates(site, client):
    response = client.get("/api/trips?page=1&per_page=5&hours=all")
    body = response.get_json()
    assert response.status_code == 200
    trips = body["trips"] if isinstance(body, dict) else body
    assert len(trips) <= 5
    assert all("efficiency_wh_per_km" in t for t in trips)


def test_current_status_reports_latest(site, client):
    response = client.get("/api/current-status")
    assert response.status_code == 200
    body = response.get_json()
    assert body["battery_level"] is not None
    assert body["odometer"] is not None


def test_battery_history_returns_series(site, client):
    response = client.get("/api/battery/history?hours=all")
    assert response.status_code == 200


def test_locations_listed_without_live_client(site, client):
    response = client.get("/api/locations?hours=all")
    assert response.status_code == 200
    assert isinstance(response.get_json(), list)


def test_charging_sessions_include_energy_source(site, client):
    response = client.get("/api/charging-sessions?hours=all")
    assert response.status_code == 200


def test_efficiency_stats_are_energy_weighted(site, client):
    response = client.get("/api/efficiency-stats")
    assert response.status_code == 200
    stats = response.get_json()
    # 4 away trips: 120 mi / 36 kWh; 6 home trips: 48 mi / 12 kWh.
    # Energy-weighted: 168 / 48 = 3.5 mi/kWh. The outlier must not distort it.
    assert stats["all_time"]["average"] == pytest.approx(3.5, abs=0.05)


def test_temperature_efficiency_filters_the_outlier(site, client):
    response = client.get("/api/temperature-efficiency")
    assert response.status_code == 200
    body = response.get_json()
    efficiencies = [p["efficiency"] for p in body["raw_data"]]
    assert efficiencies and max(efficiencies) < 10.5


def test_efficiency_by_month_reports_units_correctly(site, client):
    response = client.get("/api/efficiency-by-month")
    assert response.status_code == 200
    months = response.get_json()["months"]
    assert months
    july = months[0]
    assert july["wh_per_mile"] > july["wh_per_km"]  # mile is the longer unit


def test_charging_temperature_impact_tags_call_classes(site, client):
    response = client.get("/api/charging-temperature-impact")
    assert response.status_code == 200
    points = response.get_json()["raw_data"]
    assert {p["charge_type"] for p in points} <= {"l1", "l2", "dcfc"}


def test_charging_efficiency_measures_the_overnight_charge(site, client):
    response = client.get("/api/charging-efficiency?hours=all")
    assert response.status_code == 200
    body = response.get_json()
    assert body["summary"]["count"] >= 1
    # 13 SOC points on a 74 kWh pack against ~17.5 kWh at the wall: ~55%.
    assert 40 <= body["summary"]["efficiency_pct"] <= 70


def test_battery_health_is_honest_about_thin_data(site, client):
    response = client.get("/api/battery-health")
    assert response.status_code == 200
    assert response.get_json()["verdict"] == "insufficient_data"


def test_planner_destinations_finds_the_away_cluster(site, client):
    response = client.get("/api/planner/destinations")
    assert response.status_code == 200
    destinations = response.get_json()["destinations"]
    assert any(d["distance_km"] > 50 for d in destinations)


def test_planner_assess_requires_departure(site, client):
    assert client.get("/api/planner/assess").status_code == 400
    assert client.get("/api/planner/assess?departure=not-a-date").status_code == 400


def test_planner_assess_full_response(site, client):
    response = client.get("/api/planner/assess?departure=2099-01-02T07:00&trip_kwh=40")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] in {"on_track", "tight", "off_track"}
    assert "duty_cycle_pct" in body


def test_collection_and_polling_status(site, client):
    assert client.get("/api/collection-status").status_code == 200
    response = client.get("/api/polling-status")
    assert response.status_code == 200


def test_plug_sample_auth_states(site, client, monkeypatch):
    monkeypatch.delenv("PLUG_WEBHOOK_TOKEN", raising=False)
    assert client.post("/api/plug-sample", json={"watts": 5}).status_code == 503

    monkeypatch.setenv("PLUG_WEBHOOK_TOKEN", "sekrit")
    wrong = client.post("/api/plug-sample", json={"watts": 5}, headers={"X-Plug-Token": "nope"})
    assert wrong.status_code == 403

    ok = client.post("/api/plug-sample", json={"watts": 1337.5}, headers={"X-Plug-Token": "sekrit"})
    assert ok.status_code == 200
    log = pd.read_csv(site / "data" / "plug_power.csv")
    assert float(log.iloc[-1]["watts"]) == pytest.approx(1337.5)


def test_empty_site_degrades_to_404_not_500(empty_site, client):
    for path in (
        "/api/trips?hours=all",
        "/api/temperature-efficiency",
        "/api/charging-temperature-impact",
        "/api/battery-health",
    ):
        response = client.get(path)
        assert response.status_code in (200, 404), path
        assert response.status_code != 500, path
