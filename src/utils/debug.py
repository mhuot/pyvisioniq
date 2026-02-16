"""
Debug utilities for PyVisionic
Provides enhanced logging, data validation, and debugging tools
"""

import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

import numpy as np
import pandas as pd


class DebugLogger:
    """Enhanced logger with data validation and context tracking"""

    def __init__(self, name: str, debug_dir: str = "debug"):
        self.logger = logging.getLogger(name)
        self.debug_dir = Path(debug_dir)
        self.debug_dir.mkdir(exist_ok=True)
        self.context_stack = []

    def push_context(self, context: str):
        """Add context to help track where errors occur"""
        self.context_stack.append(context)
        self.logger.debug(f"Entering: {context}")

    def pop_context(self):
        """Remove context when leaving a section"""
        if self.context_stack:
            context = self.context_stack.pop()
            self.logger.debug(f"Leaving: {context}")

    def get_context(self) -> str:
        """Get current context path"""
        return " > ".join(self.context_stack)

    def log_data(self, level: str, message: str, data: Any = None, **kwargs):
        """Log with data validation and optional data dump"""
        context = self.get_context()
        full_message = f"[{context}] {message}" if context else message

        # Log the message
        getattr(self.logger, level)(full_message)

        # If debug mode and data provided, save it
        if data is not None and self.logger.isEnabledFor(logging.DEBUG):
            self._save_debug_data(message, data, **kwargs)

    def log_error_with_data(self, message: str, error: Exception, data: Dict[str, Any] = None):
        """Log error with full context and data dump"""
        error_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        context = self.get_context()

        error_info = {
            "error_id": error_id,
            "timestamp": datetime.now().isoformat(),
            "context": context,
            "message": message,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "data": data or {},
        }

        # Log to file
        self.logger.error(f"[{context}] {message}: {error} (Error ID: {error_id})")

        # Save detailed error info
        error_file = self.debug_dir / f"error_{error_id}.json"
        with open(error_file, "w") as f:
            json.dump(error_info, f, indent=2, default=str)

        return error_id

    def _save_debug_data(self, label: str, data: Any, **kwargs):
        """Save debug data to file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"debug_{label.replace(' ', '_')}_{timestamp}.json"

        debug_file = self.debug_dir / filename

        try:
            if isinstance(data, pd.DataFrame):
                data_dict = {
                    "type": "DataFrame",
                    "shape": data.shape,
                    "columns": list(data.columns),
                    "dtypes": {col: str(dtype) for col, dtype in data.dtypes.items()},
                    "sample": data.head(10).to_dict(orient="records"),
                    "info": str(data.info()),
                }
            else:
                data_dict = data

            output = {
                "timestamp": datetime.now().isoformat(),
                "label": label,
                "context": self.get_context(),
                "data": data_dict,
                "metadata": kwargs,
            }

            with open(debug_file, "w") as f:
                json.dump(output, f, indent=2, default=str)

        except Exception as e:
            self.logger.warning(f"Failed to save debug data: {e}")


class DataValidator:
    """Validate and convert data types with detailed error reporting"""

    @staticmethod
    def validate_battery_level(value: Any, context: str = "") -> Optional[int]:
        """Validate and convert battery level to int"""
        if value is None or pd.isna(value):
            return None

        try:
            # Handle various input types including numpy
            if hasattr(value, "item"):  # numpy scalar
                battery_int = int(value.item())
            elif isinstance(value, (int, float, np.integer, np.floating)):
                battery_int = int(value)
            elif isinstance(value, str):
                # Remove any whitespace or percentage signs
                cleaned = value.strip().rstrip("%")
                battery_int = int(float(cleaned))
            else:
                raise ValueError(f"Unexpected type: {type(value)}")

            # Validate range
            if not 0 <= battery_int <= 100:
                raise ValueError(f"Battery level {battery_int} outside valid range 0-100")

            return battery_int

        except Exception as e:
            raise ValueError(f"Invalid battery level in {context}: {value} ({type(value)}): {e}")

    @staticmethod
    def validate_numeric(
        value: Any, context: str = "", min_val: float = None, max_val: float = None
    ) -> Optional[float]:
        """Validate and convert to numeric value"""
        if value is None or pd.isna(value):
            return None

        try:
            # Handle numpy types
            if hasattr(value, "item"):  # numpy scalar
                num_val = float(value.item())
            elif isinstance(value, (int, float, np.integer, np.floating)):
                num_val = float(value)
            elif isinstance(value, str):
                # Handle comma decimals and remove units
                cleaned = value.strip().replace(",", ".")
                # Remove common units
                for unit in ["kW", "kWh", "km", "mi", "%", "°C", "°F"]:
                    cleaned = cleaned.replace(unit, "").strip()
                num_val = float(cleaned)
            else:
                num_val = float(value)

            # Validate range if provided
            if min_val is not None and num_val < min_val:
                raise ValueError(f"Value {num_val} below minimum {min_val}")
            if max_val is not None and num_val > max_val:
                raise ValueError(f"Value {num_val} above maximum {max_val}")

            return num_val

        except Exception as e:
            raise ValueError(f"Invalid numeric value in {context}: {value} ({type(value)}): {e}")

    @staticmethod
    def validate_timestamp(value: Any, context: str = "") -> Optional[pd.Timestamp]:
        """Validate and convert timestamp"""
        if value is None or pd.isna(value):
            return None

        try:
            return pd.to_datetime(value)
        except Exception as e:
            raise ValueError(f"Invalid timestamp in {context}: {value}: {e}")


def setup_debug_logging(debug_mode: bool = False):
    """Setup enhanced logging configuration"""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    if debug_mode:
        log_level = logging.DEBUG
        # Add more detailed format for debug mode
        log_format = (
            "%(asctime)s - %(name)s - [%(filename)s:%(lineno)d] - %(levelname)s - %(message)s"
        )
    else:
        log_level = logging.INFO

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.FileHandler("debug.log" if debug_mode else "app.log"),
            logging.StreamHandler(),
        ],
    )

    # Set specific loggers to appropriate levels
    if debug_mode:
        logging.getLogger("src").setLevel(logging.DEBUG)
        logging.getLogger("hyundai_kia_connect_api").setLevel(logging.DEBUG)
    else:
        logging.getLogger("hyundai_kia_connect_api").setLevel(logging.WARNING)

    return log_level
