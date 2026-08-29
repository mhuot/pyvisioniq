"""Tests for the pre-trip charging readiness core.

The central risk is double-counting: reporting a trip as still pending against
a battery that has already paid for it. Progress is therefore derived from SOC,
which cannot lag the battery reading the way trip logs and the odometer can.

These exercise the shared core in src/utils/trip_planner.py, which the CLI, the
collector and the web app all delegate to. The functions take frames rather
than paths so the arithmetic can be tested without a filesystem; only
load_readiness_inputs touches disk, and its tolerance of a missing or corrupt
feed is tested through that.
"""

from datetime import datetime

import pandas as pd
import pytest

from src.utils.trip_planner import (
    energy_consumed_on,
    load_readiness_inputs,
    plug_charging_state,
    remaining_load,
)

PACK = 74.0


def _battery_df(levels, day="2026-08-11"):
    """A battery frame with the given SOC series across one day."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(f"{day} 06:00", periods=len(levels), freq="90min"),
            "battery_level": levels,
            "is_charging": [False] * len(levels),
        }
    )


def _plug_df(samples):
    """A plug frame from (timestamp, watts) pairs."""
    frame = pd.DataFrame(samples, columns=["timestamp", "watts"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    return frame


def test_consumption_comes_from_soc_drops():
    """91% down to 39% is 52 points, regardless of what the trip log says."""
    consumed = energy_consumed_on(_battery_df([91, 89, 64, 39]), datetime(2026, 8, 11).date(), PACK)
    assert consumed == pytest.approx(52 / 100 * PACK, abs=0.1)


def test_charging_does_not_offset_earlier_driving():
    """A drop then a recharge still counts the drop; netting would hide it."""
    consumed = energy_consumed_on(_battery_df([90, 50, 70]), datetime(2026, 8, 11).date(), PACK)
    assert consumed == pytest.approx(40 / 100 * PACK, abs=0.1)


def _consumed(levels, now):
    return energy_consumed_on(_battery_df(levels), now.date(), PACK)


def test_completed_trip_stops_counting_even_if_logs_lag():
    """The regression this replaced: a done trip still reported as half-pending."""
    now = datetime(2026, 8, 11, 15, 30)
    events = [{"date": "2026-08-11", "label": "Round trip", "miles": 153, "kwh": 38.5}]
    total, detail = remaining_load(events, _consumed([91, 89, 64, 39], now), now.date())
    assert total == pytest.approx(0.0, abs=0.1)
    assert "done" in detail[0]


def test_partial_trip_leaves_the_remainder():
    """Halfway through, only the unspent half should still be pending."""
    now = datetime(2026, 8, 11, 12, 0)
    events = [{"date": "2026-08-11", "label": "Round trip", "miles": 153, "kwh": 37.0}]
    total, _ = remaining_load(events, _consumed([91, 66], now), now.date())
    # 25 SOC points = 18.5 kWh spent of the planned 37.0.
    assert total == pytest.approx(18.5, abs=0.2)


def test_future_days_are_untouched_by_todays_driving():
    """Consumption today must not be credited against tomorrow's plan."""
    now = datetime(2026, 8, 11, 18, 0)
    events = [
        {"date": "2026-08-11", "label": "Today", "miles": 100, "kwh": 20.0},
        {"date": "2026-08-12", "label": "Tomorrow", "miles": 40, "kwh": 10.1},
    ]
    total, detail = remaining_load(events, _consumed([90, 40], now), now.date())
    assert total == pytest.approx(10.1, abs=0.05)
    assert any("Tomorrow" in line and "pending" in line for line in detail)


def test_past_days_are_dropped():
    """Yesterday's plan is history whether or not it happened."""
    now = datetime(2026, 8, 11, 9, 0)
    events = [{"date": "2026-08-10", "label": "Yesterday", "miles": 60, "kwh": 15.0}]
    total, detail = remaining_load(events, _consumed([80, 78], now), now.date())
    assert total == 0.0
    assert detail == []


def test_fresh_plug_sample_reports_charging():
    """A recent above-threshold reading means plugged in, whatever the car says."""
    state = plug_charging_state(
        _plug_df([("2026-08-11 20:20:00", 1346.8)]), datetime(2026, 8, 11, 20, 26)
    )
    assert state["is_charging"] is True
    assert state["watts"] == pytest.approx(1346.8)
    assert state["age_minutes"] == pytest.approx(6.0, abs=0.1)


def test_fresh_plug_sample_reports_idle():
    """The plug draws ~2 W when nothing is charging; that is not charging."""
    state = plug_charging_state(
        _plug_df([("2026-08-11 20:20:00", 2.0)]), datetime(2026, 8, 11, 20, 26)
    )
    assert state["is_charging"] is False


def test_stale_plug_sample_is_ignored():
    """Beyond the freshness window the caller must fall back to the vehicle."""
    assert (
        plug_charging_state(
            _plug_df([("2026-08-11 19:00:00", 1346.8)]), datetime(2026, 8, 11, 20, 26)
        )
        is None
    )


def _data_dir(tmp_path):
    """A data directory holding just enough battery history to load."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _battery_df([80, 78]).to_csv(data_dir / "battery_status.csv", index=False)
    return data_dir


def test_missing_plug_feed_is_ignored(tmp_path):
    """No webhook file at all must not raise."""
    _, plug = load_readiness_inputs(str(_data_dir(tmp_path)), datetime(2026, 8, 11, 20, 26))
    assert plug is None


def test_unreadable_plug_feed_is_ignored(tmp_path):
    """A truncated or malformed feed falls back rather than crashing."""
    data_dir = _data_dir(tmp_path)
    (data_dir / "plug_power.csv").write_text("timestamp,watts\nnot-a-date,not-a-number\n")
    _, plug = load_readiness_inputs(str(data_dir), datetime(2026, 8, 11, 20, 26))
    assert plug is None
