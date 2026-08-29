"""Tests for temperature-banded efficiency.

The load-bearing property is energy weighting. Averaging per-trip ratios lets a
two mile hop count as much as a seventy-six mile run, which biases every band
toward its shortest trips -- a bug this code previously had and must not regain.
"""

import pytest

from src.utils.efficiency import (
    bin_label,
    seasonal_adjustment,
    temperature_bins,
    wh_per_mile_at,
)


def _point(temperature, efficiency, distance):
    return {"temperature": temperature, "efficiency": efficiency, "distance": distance}


@pytest.mark.parametrize(
    "celsius,expected",
    [
        (0.0, "0 to 5°C"),
        (4.9, "0 to 5°C"),
        (5.0, "5 to 10°C"),
        (-0.1, "-5 to 0°C"),
        (-17, "-20 to -15°C"),
    ],
)
def test_bin_label_brackets_correctly(celsius, expected):
    assert bin_label(celsius) == expected


def test_bands_are_energy_weighted_not_mean_of_ratios():
    """One long inefficient run must outweigh a short efficient hop."""
    points = [_point(2.0, 4.0, 2.0), _point(2.0, 2.0, 76.0)]
    band = temperature_bins(points)[0]

    # Energy: 2/4 + 76/2 = 38.5 kWh for 78 miles -> 2.03 mi/kWh.
    assert band["avg_efficiency"] == pytest.approx(2.03, abs=0.01)
    # The mean of the two ratios would have been 3.0, a third too optimistic.
    assert band["avg_efficiency"] < 2.5
    assert band["trip_count"] == 2


def test_bands_are_sorted_by_temperature():
    points = [_point(20.0, 4.0, 10.0), _point(-10.0, 2.5, 10.0), _point(5.0, 3.0, 10.0)]
    labels = [band["temperature_range"] for band in temperature_bins(points)]
    assert labels == ["-10 to -5°C", "5 to 10°C", "20 to 25°C"]


def test_wh_per_mile_prefers_the_matching_band():
    points = [_point(2.0, 2.5, 50.0) for _ in range(4)]
    wh, basis = wh_per_mile_at(temperature_bins(points), 3.0)
    assert wh == pytest.approx(400.0, abs=1.0)
    assert "0 to 5°C" in basis


def test_wh_per_mile_falls_back_to_the_nearest_populated_band():
    """A temperature we have never driven in still gets a measured answer."""
    points = [_point(22.0, 4.0, 50.0) for _ in range(4)]
    wh, basis = wh_per_mile_at(temperature_bins(points), -15.0)
    assert wh == pytest.approx(250.0, abs=1.0)
    assert "nearest measured band" in basis and "none at -15 to -10°C" in basis


def test_thin_bands_are_not_trusted():
    """A single trip is not evidence about a temperature band."""
    wh, reason = wh_per_mile_at(temperature_bins([_point(2.0, 2.5, 50.0)]), 3.0)
    assert wh is None
    assert "no temperature band has enough trips" in reason


def test_seasonal_adjustment_scales_a_route_for_the_season():
    """A route measured warm costs more when it will be driven cold."""
    warm = [_point(22.0, 4.0, 50.0) for _ in range(4)]  # 250 Wh/mi
    cold = [_point(-12.0, 2.5, 50.0) for _ in range(4)]  # 400 Wh/mi
    bands = temperature_bins(warm + cold)

    multiplier, basis = seasonal_adjustment(bands, forecast_c=-12.0, history_c=22.0)
    assert multiplier == pytest.approx(1.6, abs=0.01)
    assert "1.60x" in basis


def test_seasonal_adjustment_is_neutral_without_evidence():
    """With nothing measured the multiplier must be safe to apply blindly."""
    multiplier, basis = seasonal_adjustment([], forecast_c=-10.0, history_c=20.0)
    assert multiplier == 1.0
    assert "no seasonal adjustment" in basis
