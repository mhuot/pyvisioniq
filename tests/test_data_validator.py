"""Tests for DataValidator utility class."""

import numpy as np
import pandas as pd
import pytest

from src.utils.debug import DataValidator


class TestValidateBatteryLevel:
    def test_int_value(self):
        assert DataValidator.validate_battery_level(50) == 50

    def test_float_value(self):
        assert DataValidator.validate_battery_level(75.9) == 75

    def test_string_value(self):
        assert DataValidator.validate_battery_level("80") == 80

    def test_string_with_percent(self):
        assert DataValidator.validate_battery_level("65%") == 65

    def test_numpy_int(self):
        assert DataValidator.validate_battery_level(np.int64(42)) == 42

    def test_numpy_float(self):
        assert DataValidator.validate_battery_level(np.float64(99.1)) == 99

    def test_none_returns_none(self):
        assert DataValidator.validate_battery_level(None) is None

    def test_nan_returns_none(self):
        assert DataValidator.validate_battery_level(float("nan")) is None

    def test_below_zero_raises(self):
        with pytest.raises(ValueError, match="outside valid range"):
            DataValidator.validate_battery_level(-1)

    def test_above_100_raises(self):
        with pytest.raises(ValueError, match="outside valid range"):
            DataValidator.validate_battery_level(101)

    def test_boundary_zero(self):
        assert DataValidator.validate_battery_level(0) == 0

    def test_boundary_100(self):
        assert DataValidator.validate_battery_level(100) == 100


class TestValidateNumeric:
    def test_int_value(self):
        assert DataValidator.validate_numeric(42) == 42.0

    def test_float_value(self):
        assert DataValidator.validate_numeric(3.14) == pytest.approx(3.14)

    def test_string_value(self):
        assert DataValidator.validate_numeric("7.2") == pytest.approx(7.2)

    def test_string_with_unit_kw(self):
        assert DataValidator.validate_numeric("7.2kW") == pytest.approx(7.2)

    def test_comma_decimal(self):
        assert DataValidator.validate_numeric("3,14") == pytest.approx(3.14)

    def test_numpy_int(self):
        assert DataValidator.validate_numeric(np.int64(10)) == 10.0

    def test_none_returns_none(self):
        assert DataValidator.validate_numeric(None) is None

    def test_min_val_violation(self):
        with pytest.raises(ValueError, match="below minimum"):
            DataValidator.validate_numeric(-5, min_val=0)

    def test_max_val_violation(self):
        with pytest.raises(ValueError, match="above maximum"):
            DataValidator.validate_numeric(400, max_val=350)

    def test_within_range(self):
        result = DataValidator.validate_numeric(50, min_val=0, max_val=100)
        assert result == 50.0


class TestValidateTimestamp:
    def test_iso_string(self):
        result = DataValidator.validate_timestamp("2024-01-15T10:30:00")
        assert isinstance(result, pd.Timestamp)

    def test_none_returns_none(self):
        assert DataValidator.validate_timestamp(None) is None

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid timestamp"):
            DataValidator.validate_timestamp("not-a-date")
