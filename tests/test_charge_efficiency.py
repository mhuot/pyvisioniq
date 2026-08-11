"""Tests for AC-to-pack charging efficiency measurement."""

import pandas as pd
import pytest

from src.utils.charge_efficiency import MIN_SOC_POINTS, compute_efficiency_points, summarize

PACK_KWH = 74.0


def _ac_samples(start, hours, watts, step_minutes=15):
    """Constant-power plug samples, followed by idle samples once charging ends.

    The idle tail matters: a span still drawing power at the final sample is
    reported as ``ongoing`` and deliberately excluded, since its SOC gain is not
    yet complete. Real plug data always carries these idle samples.
    """
    periods = int(hours * 60 / step_minutes) + 1
    stamps = pd.date_range(start, periods=periods + 4, freq=f"{step_minutes}min")
    return pd.DataFrame(
        {
            "timestamp": stamps,
            "watts": [watts] * periods + [2.0] * 4,
        }
    )


def _battery(start, hours, soc_start, soc_end, step_minutes=60):
    """Battery readings rising linearly from soc_start to soc_end.

    Hourly steps keep the rounded SOC series strictly increasing, so the peak
    first occurs at the final reading. At finer intervals rounding makes SOC
    reach its peak early and the measured window is legitimately shorter.
    """
    periods = int(hours * 60 / step_minutes) + 1
    stamps = pd.date_range(start, periods=periods, freq=f"{step_minutes}min")
    step = (soc_end - soc_start) / max(periods - 1, 1)
    return pd.DataFrame(
        {
            "timestamp": stamps,
            "battery_level": [round(soc_start + step * i) for i in range(periods)],
            "meteo_temp": [20.0] * periods,
        }
    )


def test_efficiency_matches_hand_calculation():
    """A 1.35 kW draw yielding 10 SOC points over 10 h is ~54.8% efficient."""
    start = "2026-01-01 18:00"
    points = compute_efficiency_points(
        _battery(start, 10, 60, 70), _ac_samples(start, 10, 1350), PACK_KWH
    )
    assert len(points) == 1
    point = points[0]
    # 10 points of 74 kWh = 7.4 kWh into the pack; 1.35 kW x 10 h = 13.5 kWh drawn.
    assert point["pack_kwh"] == pytest.approx(7.4, abs=0.05)
    assert point["ac_kwh"] == pytest.approx(13.5, abs=0.2)
    assert point["efficiency_pct"] == pytest.approx(54.8, abs=1.0)


def test_small_sessions_are_excluded_as_quantisation_noise():
    """Below MIN_SOC_POINTS the whole-percent SOC makes the ratio meaningless."""
    start = "2026-01-02 18:00"
    gain = MIN_SOC_POINTS - 5
    points = compute_efficiency_points(
        _battery(start, 4, 60, 60 + gain), _ac_samples(start, 4, 1350), PACK_KWH
    )
    assert points == []


def test_charge_limit_tail_is_trimmed():
    """Energy drawn after SOC stops rising must not count against efficiency."""
    start = pd.Timestamp("2026-01-03 18:00")
    rising = _battery(start, 10, 60, 72)
    # Four more hours plugged in at the limit: SOC flat, plug still drawing.
    flat = pd.DataFrame(
        {
            "timestamp": pd.date_range(start + pd.Timedelta(hours=10.5), periods=8, freq="30min"),
            "battery_level": [72] * 8,
            "meteo_temp": [20.0] * 8,
        }
    )
    battery = pd.concat([rising, flat], ignore_index=True)
    points = compute_efficiency_points(battery, _ac_samples(start, 14, 1350), PACK_KWH)

    assert len(points) == 1
    # Only the 10 rising hours are charged for, not the full 14.
    assert points[0]["duration_hours"] == pytest.approx(10.0, abs=0.3)
    assert points[0]["ac_kwh"] == pytest.approx(13.5, abs=0.3)


def test_implausible_results_are_dropped():
    """More energy into the pack than drawn at the wall signals bad measurement."""
    start = "2026-01-04 18:00"
    points = compute_efficiency_points(
        _battery(start, 10, 40, 70), _ac_samples(start, 10, 300), PACK_KWH
    )
    assert points == []


def test_summary_is_energy_weighted_not_a_plain_mean():
    """The headline figure is total delivered over total drawn."""
    points = [
        {"efficiency_pct": 90.0, "ac_kwh": 1.0, "pack_kwh": 0.9},
        {"efficiency_pct": 60.0, "ac_kwh": 20.0, "pack_kwh": 12.0},
    ]
    result = summarize(points)
    # A plain mean would be 75%; weighting by energy gives 12.9 / 21.0.
    assert result["efficiency_pct"] == pytest.approx(61.4, abs=0.2)
    assert result["total_lost_kwh"] == pytest.approx(8.1, abs=0.05)
    assert result["count"] == 2


def test_summary_handles_no_qualifying_sessions():
    """An empty result set must not raise or divide by zero."""
    result = summarize([])
    assert result["count"] == 0
    assert result["efficiency_pct"] is None
    assert result["total_lost_kwh"] == 0.0
