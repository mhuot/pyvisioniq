"""Tests for smart-plug charging span detection and session refinement."""

from datetime import datetime, timedelta

import pandas as pd

from src.utils.plug_sessions import detect_charging_spans, refine_sessions

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

    def test_refinement_subsumes_late_tracker_session(self, tmp_path):
        # An ha_ row exists from an earlier refinement; the tracker then opens
        # its own session (with no end_time yet) once polling sees the charge.
        # A later refinement must subsume the tracker row, not leave two
        # active sessions.
        sessions_csv = tmp_path / "charging_sessions.csv"
        pd.DataFrame(
            [
                {
                    "session_id": "ha_20260807_152000",
                    "start_time": "2026-08-07 15:20:00",
                    "end_time": "2026-08-07 16:05:00",
                    "duration_minutes": 45.0,
                    "start_battery": None,
                    "end_battery": None,
                    "energy_added": 1.0,
                    "avg_power": 1.35,
                    "max_power": 1.35,
                    "location_lat": None,
                    "location_lon": None,
                    "is_complete": False,
                    "network": "Home",
                    "location_name": "",
                    "cost_usd": None,
                },
                {
                    "session_id": "charge_20260807_160228",
                    "start_time": "2026-08-07 15:59:58",
                    "end_time": None,
                    "duration_minutes": 0,
                    "start_battery": 72,
                    "end_battery": 72,
                    "energy_added": 0,
                    "avg_power": 1.3,
                    "max_power": 1.3,
                    "location_lat": 44.88,
                    "location_lon": -93.13,
                    "is_complete": False,
                    "network": "",
                    "location_name": "",
                    "cost_usd": None,
                },
            ]
        ).to_csv(sessions_csv, index=False)

        spans = [
            {
                "start": datetime(2026, 8, 7, 15, 20, 0),
                "end": datetime(2026, 8, 7, 17, 30, 0),
                "kwh": 2.9,
                "avg_kw": 1.34,
                "max_kw": 1.35,
                "ongoing": True,
            }
        ]
        refine_sessions(spans, sessions_csv, write=True, make_backup=False)

        result = pd.read_csv(sessions_csv)
        assert len(result) == 1
        assert result.iloc[0]["session_id"] == "ha_20260807_152000"
        assert result.iloc[0]["end_time"] == "2026-08-07 17:30:00"

    def test_recent_idle_samples_prove_completion(self):
        # A single high sample followed by idle samples is complete, even if
        # the idle samples arrived within the gap-bridging window
        samples = make_samples([(0, 1234.5)] + [(m, 2) for m in range(11, 40, 5)])
        spans = detect_charging_spans(samples)
        assert len(spans) == 1
        assert spans[0]["ongoing"] is False
