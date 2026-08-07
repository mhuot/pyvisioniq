"""Tests for airTemp value normalization."""

from src.utils.temperature import normalize_airtemp_fahrenheit


class TestNormalizeAirtempFahrenheit:
    def test_numeric_value_passes_through(self):
        assert normalize_airtemp_fahrenheit(72) == 72.0

    def test_numeric_string_is_coerced(self):
        assert normalize_airtemp_fahrenheit("72") == 72.0

    def test_lo_maps_to_zero(self):
        assert normalize_airtemp_fahrenheit("LO") == 0.0

    def test_hi_maps_to_hundred(self):
        assert normalize_airtemp_fahrenheit("HI") == 100.0

    def test_case_and_whitespace_insensitive(self):
        assert normalize_airtemp_fahrenheit(" lo ") == 0.0
        assert normalize_airtemp_fahrenheit("Hi") == 100.0

    def test_none_returns_none(self):
        assert normalize_airtemp_fahrenheit(None) is None

    def test_off_returns_none(self):
        assert normalize_airtemp_fahrenheit("OFF") is None
        assert normalize_airtemp_fahrenheit("off") is None

    def test_garbage_returns_none(self):
        assert normalize_airtemp_fahrenheit("unknown") is None

    def test_zero_fahrenheit_is_preserved_not_dropped(self):
        # LO maps to 0.0, which is falsy - the caller must use "is not None"
        result = normalize_airtemp_fahrenheit("LO")
        assert result is not None
        assert result == 0.0
