"""Tests for trip planning: destination detection and reachability."""

from datetime import datetime

import pandas as pd
import pytest

from src.utils.trip_planner import assess_trip, day_energy_combined, energy_by_day

PACK = 74.0


def _battery(levels, day="2026-06-03"):
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(f"{day} 06:00", periods=len(levels), freq="60min"),
            "battery_level": levels,
        }
    )


def _trips(kwh_by_day):
    return pd.DataFrame(
        {
            "date": pd.to_datetime(list(kwh_by_day)),
            "total_consumed": [v * 1000 for v in kwh_by_day.values()],
            "distance": [100] * len(kwh_by_day),
        }
    )


def test_only_soc_drops_count():
    """Charging must not net off earlier driving."""
    result = energy_by_day(_battery([90, 50, 70]), PACK)
    assert result.iloc[0] == pytest.approx(40 / 100 * PACK, abs=0.1)


def test_combined_source_covers_a_poller_blind_spot():
    """The June Duluth run: SOC saw 86->37 because a charge began between polls.

    The trip log recorded the day more completely, so the combined figure must
    take the larger rather than trusting state of charge alone.
    """
    battery = _battery([86, 37, 89], day="2026-06-04")
    trips = _trips({"2026-06-04": 56.4})
    combined = day_energy_combined(trips, battery, PACK)
    assert combined.iloc[0] == pytest.approx(56.4, abs=0.1)


def test_combined_source_covers_a_trip_log_gap():
    """And the reverse: a day the trip log under-recorded but SOC captured."""
    battery = _battery([90, 30])
    trips = _trips({"2026-06-03": 9.6})
    combined = day_energy_combined(trips, battery, PACK)
    assert combined.iloc[0] == pytest.approx(60 / 100 * PACK, abs=0.1)


def test_reachable_trip_is_on_track():
    """84% now, a 53.5 kWh trip tomorrow, one ordinary day in between."""
    result = assess_trip(
        now=datetime(2026, 8, 13, 4, 30),
        departure=datetime(2026, 8, 14, 7, 0),
        soc_pct=84.0,
        pack_kwh=PACK,
        charger_kw=0.95,
        trip_kwh=53.5,
        arrival_buffer_pct=10,
        daily_kwh=6.0,
    )
    assert result["status"] == "on_track"
    assert result["projected_arrival_pct"] > 10


def test_unreachable_trip_reports_the_shortfall():
    """From 30% with hours to go, the gap must be stated in fast-charge terms."""
    result = assess_trip(
        now=datetime(2026, 8, 13, 18, 0),
        departure=datetime(2026, 8, 14, 7, 0),
        soc_pct=30.0,
        pack_kwh=PACK,
        charger_kw=0.95,
        trip_kwh=53.5,
        arrival_buffer_pct=10,
        daily_kwh=6.0,
    )
    assert result["status"] == "off_track"
    assert result["shortfall_kwh"] > 0
    assert result["dcfc_minutes"] > 0


def test_departed_trip_is_not_assessed():
    """A departure in the past must not produce a negative-hours plan."""
    result = assess_trip(
        now=datetime(2026, 8, 14, 9, 0),
        departure=datetime(2026, 8, 14, 7, 0),
        soc_pct=90.0,
        pack_kwh=PACK,
        charger_kw=0.95,
        trip_kwh=53.5,
        arrival_buffer_pct=10,
        daily_kwh=6.0,
    )
    assert result["status"] == "departed"
