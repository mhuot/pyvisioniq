"""Tests for the adaptive polling interval policy."""

from datetime import datetime, timedelta

from data_collector import (
    ADAPTIVE_INTERVALS_MINUTES,
    adaptive_interval_minutes,
)

NOON = datetime(2026, 8, 6, 12, 0, 0)
MIDNIGHT = datetime(2026, 8, 6, 23, 30, 0)


class TestAdaptiveIntervalMinutes:
    def test_dcfc_polls_fastest(self):
        interval = adaptive_interval_minutes(True, 143.0, NOON, None, NOON)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["dcfc"]

    def test_ac_charge_start_window(self):
        charging_since = NOON - timedelta(minutes=10)
        interval = adaptive_interval_minutes(True, 1.3, charging_since, None, NOON)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["ac_charge_start"]

    def test_ac_charge_steady_backs_off(self):
        charging_since = NOON - timedelta(hours=2)
        interval = adaptive_interval_minutes(True, 1.3, charging_since, None, NOON)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["ac_charge_steady"]

    def test_unknown_charging_start_treated_as_new(self):
        interval = adaptive_interval_minutes(True, 1.3, None, None, NOON)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["ac_charge_start"]

    def test_post_trip_window_polls_fast(self):
        trip_end = NOON - timedelta(minutes=15)
        interval = adaptive_interval_minutes(False, 0, None, trip_end, NOON)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["post_trip"]

    def test_old_trip_does_not_trigger_fast_polling(self):
        trip_end = NOON - timedelta(hours=3)
        interval = adaptive_interval_minutes(False, 0, None, trip_end, NOON)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["idle_day"]

    def test_idle_day(self):
        interval = adaptive_interval_minutes(False, 0, None, None, NOON)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["idle_day"]

    def test_idle_night(self):
        interval = adaptive_interval_minutes(False, 0, None, None, MIDNIGHT)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["idle_night"]

    def test_early_morning_counts_as_night(self):
        early = datetime(2026, 8, 6, 4, 0, 0)
        interval = adaptive_interval_minutes(False, 0, None, None, early)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["idle_night"]

    def test_charging_wins_over_post_trip(self):
        trip_end = NOON - timedelta(minutes=5)
        interval = adaptive_interval_minutes(True, 143.0, NOON, trip_end, NOON)
        assert interval == ADAPTIVE_INTERVALS_MINUTES["dcfc"]
