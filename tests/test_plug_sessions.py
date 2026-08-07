"""Tests for smart-plug charging span detection."""

from datetime import datetime, timedelta

import pandas as pd

from src.utils.plug_sessions import detect_charging_spans

BASE = datetime(2026, 8, 6, 20, 0, 0)


def make_samples(points):
    """points: list of (minutes_offset, watts)."""
    return pd.DataFrame(
        {
            "timestamp": [BASE + timedelta(minutes=m) for m, _ in points],
            "watts": [w for _, w in points],
        }
    )


class TestDetectChargingSpans:
    def test_continuous_charge_is_one_span(self):
        samples = make_samples([(m, 1350) for m in range(0, 120, 1)] + [(121, 2)])
        spans = detect_charging_spans(samples)
        assert len(spans) == 1
        span = spans[0]
        assert span["start"] == BASE
        assert abs(span["kwh"] - 1.35 * 2) < 0.1  # ~2 hours at 1.35 kW
        assert span["max_kw"] == 1.35

    def test_idle_power_is_ignored(self):
        samples = make_samples([(m, 2.0) for m in range(0, 60)])
        assert detect_charging_spans(samples) == []

    def test_long_gap_splits_spans(self):
        morning = [(m, 1350) for m in range(0, 30)]
        evening = [(m, 1350) for m in range(600, 630)]
        tail = [(631, 2)]
        spans = detect_charging_spans(make_samples(morning + evening + tail))
        assert len(spans) == 2

    def test_hourly_cadence_bridges_and_integrates(self):
        # Hourly statistics era: samples 60 min apart still form one span
        samples = make_samples([(0, 1300), (60, 1350), (120, 1330), (180, 2)])
        spans = detect_charging_spans(samples)
        assert len(spans) == 1
        assert abs(spans[0]["kwh"] - 4.0) < 0.2  # ~3 hours at ~1.33 kW

    def test_tiny_span_is_filtered(self):
        samples = make_samples([(0, 150), (1, 2)])
        assert detect_charging_spans(samples) == []

    def test_ongoing_span_flagged(self):
        samples = make_samples([(m, 1350) for m in range(0, 30)])
        spans = detect_charging_spans(samples)
        assert len(spans) == 1
        assert spans[0]["ongoing"] is True

    def test_completed_span_not_flagged_ongoing(self):
        samples = make_samples([(m, 1350) for m in range(0, 30)] + [(m, 2) for m in range(31, 120)])
        spans = detect_charging_spans(samples)
        assert len(spans) == 1
        assert spans[0]["ongoing"] is False
