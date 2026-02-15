"""
base.py
Abstract base class for storage backends.
Defines the interface that all storage implementations must follow.
"""

from abc import ABC, abstractmethod

import pandas as pd


class StorageBackend(ABC):
    """Abstract base class for vehicle data storage backends.

    All storage implementations (CSV, Oracle, etc.) must implement
    these methods to provide a consistent interface for data access.
    """

    @abstractmethod
    def store_vehicle_data(self, data):
        """Store vehicle data (trips, battery, location, charging sessions).

        Args:
            data: Dictionary containing vehicle data with keys like
                  'trips', 'battery', 'location', 'timestamp', etc.
        """

    @abstractmethod
    def get_trips_df(self) -> pd.DataFrame:
        """Get trips data as a DataFrame.

        Returns:
            DataFrame with columns: timestamp, date, distance, duration,
            average_speed, max_speed, idle_time, trips_count,
            total_consumed, regenerated_energy, accessories_consumed,
            climate_consumed, drivetrain_consumed, battery_care_consumed,
            odometer_start, end_latitude, end_longitude, end_temperature
        """

    @abstractmethod
    def get_battery_df(self) -> pd.DataFrame:
        """Get battery status data as a DataFrame.

        Returns:
            DataFrame with columns: timestamp, battery_level, is_charging,
            charging_power, remaining_time, range, temperature, odometer,
            meteo_temp, vehicle_temp
        """

    @abstractmethod
    def get_locations_df(self) -> pd.DataFrame:
        """Get location data as a DataFrame.

        Returns:
            DataFrame with columns: timestamp, latitude, longitude,
            last_updated
        """

    @abstractmethod
    def get_charging_sessions_df(self) -> pd.DataFrame:
        """Get charging sessions data as a DataFrame.

        Returns:
            DataFrame with columns: session_id, start_time, end_time,
            duration_minutes, start_battery, end_battery, energy_added,
            avg_power, max_power, location_lat, location_lon, is_complete
        """

    @abstractmethod
    def get_latest_trips(self, limit=10) -> pd.DataFrame:
        """Get the latest trips data.

        Args:
            limit: Maximum number of trips to return.

        Returns:
            DataFrame of latest trips sorted by timestamp descending.
        """

    @abstractmethod
    def get_battery_history(self, days=7) -> pd.DataFrame:
        """Get battery history for a given time period.

        Args:
            days: Number of days of history. None for all data.

        Returns:
            DataFrame of battery data sorted by timestamp.
        """
