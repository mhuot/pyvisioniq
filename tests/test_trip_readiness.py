"""Tests for the pre-trip charging readiness checker.

The central risk is double-counting: reporting a trip as still pending against
a battery that has already paid for it. Progress is therefore derived from SOC,
which cannot lag the battery reading the way trip logs and the odometer can.
"""

import os
import sys
from datetime import datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from trip_readiness_check import (  # noqa: E402
    energy_consumed_on,
    plug_charging_state,
    remaining_load,
)

PACK = 74.0


def _battery_csv(tmp_path, levels, day="2026-08-11"):
    """Write a battery_status.csv with the given SOC series for one day."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    stamps = pd.date_range(f"{day} 06:00", periods=len(levels), freq="90min")
    pd.DataFrame(
        {
            "timestamp": stamps,
            "battery_level": levels,
            "is_charging": [False] * len(levels),
        }
    ).to_csv(data_dir / "battery_status.csv", index=False)
    return str(tmp_path)


def _plan(events):
    return {"pack_kwh": PACK, "planned_driving": events}


def test_consumption_comes_from_soc_drops(tmp_path):
    """91% down to 39% is 52 points, regardless of what the trip log says."""
    root = _battery_csv(tmp_path, [91, 89, 64, 39])
    consumed = energy_consumed_on(root, datetime(2026, 8, 11).date(), PACK)
    assert consumed == pytest.approx(52 / 100 * PACK, abs=0.1)


def test_charging_does_not_offset_earlier_driving(tmp_path):
    """A drop then a recharge still counts the drop; netting would hide it."""
    root = _battery_csv(tmp_path, [90, 50, 70])
    consumed = energy_consumed_on(root, datetime(2026, 8, 11).date(), PACK)
    assert consumed == pytest.approx(40 / 100 * PACK, abs=0.1)


def test_completed_trip_stops_counting_even_if_logs_lag(tmp_path):
    """The regression this replaced: a done trip still reported as half-pending."""
    root = _battery_csv(tmp_path, [91, 89, 64, 39])
    plan = _plan([{"date": "2026-08-11", "label": "Round trip", "miles": 153, "kwh": 38.5}])
    total, detail = remaining_load(plan, root, datetime(2026, 8, 11, 15, 30))
    assert total == pytest.approx(0.0, abs=0.1)
    assert "done" in detail[0]


def test_partial_trip_leaves_the_remainder(tmp_path):
    """Halfway through, only the unspent half should still be pending."""
    root = _battery_csv(tmp_path, [91, 66])
    plan = _plan([{"date": "2026-08-11", "label": "Round trip", "miles": 153, "kwh": 37.0}])
    total, _ = remaining_load(plan, root, datetime(2026, 8, 11, 12, 0))
    # 25 SOC points = 18.5 kWh spent of the planned 37.0.
    assert total == pytest.approx(18.5, abs=0.2)


def test_future_days_are_untouched_by_todays_driving(tmp_path):
    """Consumption today must not be credited against tomorrow's plan."""
    root = _battery_csv(tmp_path, [90, 40])
    plan = _plan(
        [
            {"date": "2026-08-11", "label": "Today", "miles": 100, "kwh": 20.0},
            {"date": "2026-08-12", "label": "Tomorrow", "miles": 40, "kwh": 10.1},
        ]
    )
    total, detail = remaining_load(plan, root, datetime(2026, 8, 11, 18, 0))
    assert total == pytest.approx(10.1, abs=0.05)
    assert any("Tomorrow" in line and "pending" in line for line in detail)


def test_past_days_are_dropped(tmp_path):
    """Yesterday's plan is history whether or not it happened."""
    root = _battery_csv(tmp_path, [80, 78])
    plan = _plan([{"date": "2026-08-10", "label": "Yesterday", "miles": 60, "kwh": 15.0}])
    total, detail = remaining_load(plan, root, datetime(2026, 8, 11, 9, 0))
    assert total == 0.0
    assert detail == []


def _plug_csv(tmp_path, samples):
    """Write data/plug_power.csv from (timestamp, watts) pairs."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    pd.DataFrame(samples, columns=["timestamp", "watts"]).to_csv(
        data_dir / "plug_power.csv", index=False
    )
    return str(tmp_path)


def test_fresh_plug_sample_reports_charging(tmp_path):
    """A recent above-threshold reading means plugged in, whatever the car says."""
    now = datetime(2026, 8, 11, 20, 26)
    root = _plug_csv(tmp_path, [("2026-08-11 20:20:00", 1346.8)])
    state = plug_charging_state(root, now)
    assert state["is_charging"] is True
    assert state["watts"] == pytest.approx(1346.8)
    assert state["age_minutes"] == pytest.approx(6.0, abs=0.1)


def test_fresh_plug_sample_reports_idle(tmp_path):
    """The plug draws ~2 W when nothing is charging; that is not charging."""
    now = datetime(2026, 8, 11, 20, 26)
    root = _plug_csv(tmp_path, [("2026-08-11 20:20:00", 2.0)])
    assert plug_charging_state(root, now)["is_charging"] is False


def test_stale_plug_sample_is_ignored(tmp_path):
    """Beyond the freshness window the caller must fall back to the vehicle."""
    now = datetime(2026, 8, 11, 20, 26)
    root = _plug_csv(tmp_path, [("2026-08-11 19:00:00", 1346.8)])
    assert plug_charging_state(root, now) is None


def test_missing_plug_feed_is_ignored(tmp_path):
    """No webhook file at all must not raise."""
    assert plug_charging_state(str(tmp_path), datetime(2026, 8, 11, 20, 26)) is None


def test_unreadable_plug_feed_is_ignored(tmp_path):
    """A truncated or malformed feed falls back rather than crashing."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "plug_power.csv").write_text("timestamp,watts\nnot-a-date,not-a-number\n")
    assert plug_charging_state(str(tmp_path), datetime(2026, 8, 11, 20, 26)) is None
