"""Tests pinning the units of driving-efficiency calculations.

trips.csv records distance in MILES. Dividing consumption by it yields Wh per
mile; reporting that as Wh/km overstated consumption by 61%, and the onward
mi/kWh conversion divided by 1.60934 a second time. These tests fix the units
in place so the mistake cannot quietly return.
"""

import pytest

from src.web.app import KM_PER_MILE, efficiency_wh_per_km


def _mi_per_kwh(wh_per_km):
    """The conversion the frontend applies to the API's Wh/km value."""
    return 1000 / (wh_per_km * KM_PER_MILE)


def test_known_trip_matches_hand_calculation():
    """2026-08-03 Rochester outbound: 76 miles for 21761 Wh."""
    wh_per_km = efficiency_wh_per_km(21761, 76)
    # 21761 / (76 * 1.60934) = 178 Wh/km, not the 286 Wh/mi figure.
    assert wh_per_km == pytest.approx(178, abs=1)
    assert _mi_per_kwh(wh_per_km) == pytest.approx(3.49, abs=0.02)


def test_distance_is_treated_as_miles_not_kilometres():
    """Guards the specific regression: dividing by raw distance."""
    wh_per_km = efficiency_wh_per_km(1000, 10)
    naive = 1000 / 10  # what the code did before, i.e. Wh per mile
    assert wh_per_km == pytest.approx(naive / KM_PER_MILE, abs=0.01)
    assert wh_per_km < naive


def test_round_trip_through_mi_per_kwh_is_stable():
    """Wh/km and mi/kWh must describe the same trip."""
    wh_per_km = efficiency_wh_per_km(15000, 60)
    miles_per_kwh = _mi_per_kwh(wh_per_km)
    # 60 miles on 15 kWh is 4 mi/kWh by definition.
    assert miles_per_kwh == pytest.approx(4.0, abs=0.01)


@pytest.mark.parametrize("consumed,distance", [(0, 10), (1000, 0), (1000, None), (None, 10)])
def test_unusable_inputs_return_none(consumed, distance):
    """Missing or zero values must not raise or divide by zero."""
    assert efficiency_wh_per_km(consumed, distance) is None
