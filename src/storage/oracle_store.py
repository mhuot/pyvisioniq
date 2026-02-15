"""
oracle_store.py
Oracle Autonomous Database storage backend for vehicle data.
Uses python-oracledb in thin mode (no Oracle Instant Client required).
"""

import logging
import os
from datetime import datetime

import oracledb
import pandas as pd

from src.storage.base import StorageBackend
from src.storage.oracle_schema import ALL_TABLES
from src.utils.debug import DataValidator, DebugLogger
from src.utils.weather import WeatherService

logger = logging.getLogger(__name__)
debug_logger = DebugLogger(__name__)


class OracleStorage(StorageBackend):
    """Oracle Autonomous Database storage backend.

    Implements the StorageBackend interface using Oracle ADB with
    connection pooling and wallet-based authentication.
    """

    def __init__(self):
        """Initialize OracleStorage with connection pool and table creation."""
        self.oracle_user = os.getenv("ORACLE_USER")
        self.oracle_password = os.getenv("ORACLE_PASSWORD")
        self.oracle_dsn = os.getenv("ORACLE_DSN")
        self.wallet_location = os.getenv("ORACLE_WALLET_LOCATION")
        self.wallet_password = os.getenv("ORACLE_WALLET_PASSWORD")

        if not all([self.oracle_user, self.oracle_password, self.oracle_dsn]):
            raise ValueError("ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN must be set")

        # Initialize weather service (same as CSVStorage)
        self.weather_service = WeatherService()
        self.use_meteo = os.getenv("WEATHER_SOURCE", "meteo").lower() == "meteo"
        daily_limit = float(os.getenv("API_DAILY_LIMIT", "30"))
        poll_interval_minutes = (24 * 60) / daily_limit if daily_limit > 0 else 48.0
        gap_multiplier = float(os.getenv("CHARGING_SESSION_GAP_MULTIPLIER", "1.5"))
        self.charging_gap_threshold_minutes = max(
            poll_interval_minutes * gap_multiplier, 5.0
        )
        self.battery_capacity_kwh = float(os.getenv("BATTERY_CAPACITY_KWH", "77.4"))

        self._init_pool()
        self._init_tables()

    def _init_pool(self):
        """Create the Oracle connection pool."""
        pool_params = {
            "user": self.oracle_user,
            "password": self.oracle_password,
            "dsn": self.oracle_dsn,
            "min": 1,
            "max": 4,
            "increment": 1,
        }

        if self.wallet_location:
            pool_params["config_dir"] = self.wallet_location
            pool_params["wallet_location"] = self.wallet_location
            if self.wallet_password:
                pool_params["wallet_password"] = self.wallet_password

        self.pool = oracledb.create_pool(**pool_params)
        logger.info("Oracle connection pool created (DSN: %s)", self.oracle_dsn)

    def _init_tables(self):
        """Create tables if they do not exist (idempotent)."""
        with self.pool.acquire() as connection:
            cursor = connection.cursor()
            for table_name, ddl in ALL_TABLES.items():
                try:
                    cursor.execute(
                        "SELECT 1 FROM user_tables WHERE table_name = :1",
                        [table_name.upper()],
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(ddl)
                        connection.commit()
                        logger.info("Created Oracle table: %s", table_name)
                    else:
                        logger.debug("Oracle table already exists: %s", table_name)
                except oracledb.DatabaseError as exc:
                    logger.error("Error creating table %s: %s", table_name, exc)
                    connection.rollback()

    def _get_connection(self):
        """Acquire a connection from the pool."""
        return self.pool.acquire()

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def store_vehicle_data(self, data):
        """Store vehicle data in Oracle tables."""
        if not data:
            return

        timestamp = data.get("timestamp", datetime.now().isoformat())

        self._store_trips(data.get("trips", []), timestamp)
        self._store_battery(data, timestamp)
        self._store_location(data.get("location", {}), timestamp)

    def _store_trips(self, trips, timestamp):
        """Store trip data using MERGE for deduplication."""
        if not trips:
            return

        merge_sql = """
            MERGE INTO trips t
            USING (
                SELECT :trip_date AS trip_date,
                       :distance AS distance,
                       :odometer_start AS odometer_start,
                       :timestamp AS timestamp,
                       :duration AS duration,
                       :average_speed AS average_speed,
                       :max_speed AS max_speed,
                       :idle_time AS idle_time,
                       :trips_count AS trips_count,
                       :total_consumed AS total_consumed,
                       :regenerated_energy AS regenerated_energy,
                       :accessories_consumed AS accessories_consumed,
                       :climate_consumed AS climate_consumed,
                       :drivetrain_consumed AS drivetrain_consumed,
                       :battery_care_consumed AS battery_care_consumed,
                       :end_latitude AS end_latitude,
                       :end_longitude AS end_longitude,
                       :end_temperature AS end_temperature
                FROM dual
            ) s
            ON (t.trip_date = s.trip_date
                AND t.distance = s.distance
                AND (t.odometer_start = s.odometer_start
                     OR (t.odometer_start IS NULL AND s.odometer_start IS NULL)))
            WHEN NOT MATCHED THEN INSERT (
                timestamp, trip_date, distance, duration,
                average_speed, max_speed, idle_time, trips_count,
                total_consumed, regenerated_energy,
                accessories_consumed, climate_consumed,
                drivetrain_consumed, battery_care_consumed,
                odometer_start, end_latitude, end_longitude, end_temperature
            ) VALUES (
                s.timestamp, s.trip_date, s.distance, s.duration,
                s.average_speed, s.max_speed, s.idle_time, s.trips_count,
                s.total_consumed, s.regenerated_energy,
                s.accessories_consumed, s.climate_consumed,
                s.drivetrain_consumed, s.battery_care_consumed,
                s.odometer_start, s.end_latitude, s.end_longitude,
                s.end_temperature
            )
        """

        inserted_count = 0
        with self._get_connection() as connection:
            cursor = connection.cursor()
            for trip in trips:
                try:
                    trip_date = trip.get("date")
                    if isinstance(trip_date, str):
                        trip_date = self._parse_timestamp(trip_date)

                    params = {
                        "timestamp": self._parse_timestamp(timestamp),
                        "trip_date": trip_date,
                        "distance": self._to_float(trip.get("distance")),
                        "duration": self._to_float(trip.get("duration")),
                        "average_speed": self._to_float(trip.get("average_speed")),
                        "max_speed": self._to_float(trip.get("max_speed")),
                        "idle_time": self._to_float(trip.get("idle_time")),
                        "trips_count": self._to_int(trip.get("trips_count")),
                        "total_consumed": self._to_float(trip.get("total_consumed")),
                        "regenerated_energy": self._to_float(
                            trip.get("regenerated_energy")
                        ),
                        "accessories_consumed": self._to_float(
                            trip.get("accessories_consumed")
                        ),
                        "climate_consumed": self._to_float(
                            trip.get("climate_consumed")
                        ),
                        "drivetrain_consumed": self._to_float(
                            trip.get("drivetrain_consumed")
                        ),
                        "battery_care_consumed": self._to_float(
                            trip.get("battery_care_consumed")
                        ),
                        "odometer_start": self._to_float(trip.get("odometer_start")),
                        "end_latitude": self._to_float(trip.get("end_latitude")),
                        "end_longitude": self._to_float(trip.get("end_longitude")),
                        "end_temperature": self._to_float(trip.get("end_temperature")),
                    }
                    cursor.execute(merge_sql, params)
                    if cursor.rowcount > 0:
                        inserted_count += 1
                except oracledb.DatabaseError as exc:
                    logger.error("Error storing trip: %s", exc)

            connection.commit()
            if inserted_count > 0:
                logger.info("Stored %d new trips in Oracle", inserted_count)

    def _store_battery(self, data, timestamp):
        """Store battery status data."""
        battery = data.get("battery", {})
        if not battery:
            return

        # Get temperatures (same logic as CSVStorage)
        meteo_temp = None
        vehicle_temp = None

        temp_f_vehicle = data.get("raw_data", {}).get("airTemp", {}).get("value")
        if temp_f_vehicle:
            vehicle_temp = round((temp_f_vehicle - 32) * 5 / 9, 1)

        location = data.get("location", {})
        latitude = location.get("latitude")
        longitude = location.get("longitude")

        if latitude and longitude:
            weather_data = self.weather_service.get_current_weather(latitude, longitude)
            if weather_data:
                temp_f_meteo = weather_data.get("temperature")
                if temp_f_meteo:
                    meteo_temp = self.weather_service.get_temperature_in_celsius(
                        temp_f_meteo
                    )

        temperature = meteo_temp if self.use_meteo else vehicle_temp

        insert_sql = """
            INSERT INTO battery_status (
                timestamp, battery_level, is_charging, charging_power,
                remaining_time, battery_range, temperature, odometer,
                meteo_temp, vehicle_temp, is_cached
            ) VALUES (
                :timestamp, :battery_level, :is_charging, :charging_power,
                :remaining_time, :battery_range, :temperature, :odometer,
                :meteo_temp, :vehicle_temp, :is_cached
            )
        """

        params = {
            "timestamp": self._parse_timestamp(timestamp),
            "battery_level": self._to_float(battery.get("level")),
            "is_charging": str(battery.get("is_charging", False)),
            "charging_power": self._to_float(battery.get("charging_power")),
            "remaining_time": self._to_float(battery.get("remaining_time")),
            "battery_range": self._to_float(battery.get("range")),
            "temperature": self._to_float(temperature),
            "odometer": self._to_float(data.get("odometer")),
            "meteo_temp": self._to_float(meteo_temp),
            "vehicle_temp": self._to_float(vehicle_temp),
            "is_cached": str(data.get("is_cached", False)),
        }

        with self._get_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(insert_sql, params)
                connection.commit()
            except oracledb.IntegrityError:
                logger.debug("Duplicate battery_status entry for %s", timestamp)
                connection.rollback()
            except oracledb.DatabaseError as exc:
                logger.error("Error storing battery status: %s", exc)
                connection.rollback()

        # Track charging sessions
        self._track_charging_session(timestamp, battery, location)

    def _store_location(self, location, timestamp):
        """Store location data."""
        if not location or not location.get("latitude"):
            return

        insert_sql = """
            INSERT INTO locations (
                timestamp, latitude, longitude, last_updated
            ) VALUES (
                :timestamp, :latitude, :longitude, :last_updated
            )
        """

        params = {
            "timestamp": self._parse_timestamp(timestamp),
            "latitude": self._to_float(location.get("latitude")),
            "longitude": self._to_float(location.get("longitude")),
            "last_updated": self._parse_timestamp(location.get("last_updated")),
        }

        with self._get_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(insert_sql, params)
                connection.commit()
            except oracledb.IntegrityError:
                logger.debug("Duplicate location entry for %s", timestamp)
                connection.rollback()
            except oracledb.DatabaseError as exc:
                logger.error("Error storing location: %s", exc)
                connection.rollback()

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def get_trips_df(self) -> pd.DataFrame:
        """Get trips data as a DataFrame."""
        query = """
            SELECT timestamp, trip_date AS "date", distance, duration,
                   average_speed, max_speed, idle_time, trips_count,
                   total_consumed, regenerated_energy,
                   accessories_consumed, climate_consumed,
                   drivetrain_consumed, battery_care_consumed,
                   odometer_start, end_latitude, end_longitude,
                   end_temperature
            FROM trips
            ORDER BY trip_date DESC
        """
        return self._read_sql(query, parse_dates=["timestamp", "date"])

    def get_battery_df(self) -> pd.DataFrame:
        """Get battery status data as a DataFrame."""
        query = """
            SELECT timestamp, battery_level, is_charging, charging_power,
                   remaining_time, battery_range AS "range",
                   temperature, odometer, meteo_temp, vehicle_temp
            FROM battery_status
            ORDER BY timestamp
        """
        return self._read_sql(query, parse_dates=["timestamp"])

    def get_locations_df(self) -> pd.DataFrame:
        """Get location data as a DataFrame."""
        query = """
            SELECT timestamp, latitude, longitude, last_updated
            FROM locations
            ORDER BY timestamp
        """
        return self._read_sql(query, parse_dates=["timestamp", "last_updated"])

    def get_charging_sessions_df(self) -> pd.DataFrame:
        """Get charging sessions data as a DataFrame."""
        query = """
            SELECT session_id, start_time, end_time, duration_minutes,
                   start_battery, end_battery, energy_added,
                   avg_power, max_power, location_lat, location_lon,
                   is_complete
            FROM charging_sessions
            ORDER BY start_time DESC
        """
        dataframe = self._read_sql(query, parse_dates=["start_time", "end_time"])

        if dataframe.empty:
            return dataframe

        # Normalize is_complete to boolean
        if "is_complete" in dataframe.columns:
            dataframe["is_complete"] = dataframe["is_complete"].apply(
                lambda val: (
                    str(val).strip().lower() == "true" if pd.notna(val) else False
                )
            )

        # Normalize numeric columns
        for column in [
            "start_battery",
            "end_battery",
            "energy_added",
            "avg_power",
            "max_power",
            "duration_minutes",
        ]:
            if column in dataframe.columns:
                dataframe[column] = pd.to_numeric(dataframe[column], errors="coerce")

        return dataframe

    def get_latest_trips(self, limit=10) -> pd.DataFrame:
        """Get the latest trips data."""
        dataframe = self.get_trips_df()
        if not dataframe.empty:
            return dataframe.sort_values("timestamp", ascending=False).head(limit)
        return dataframe

    def get_battery_history(self, days=7) -> pd.DataFrame:
        """Get battery history for the last N days."""
        dataframe = self.get_battery_df()
        if not dataframe.empty:
            dataframe["timestamp"] = pd.to_datetime(
                dataframe["timestamp"], format="mixed"
            )
            if days is not None:
                cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
                return dataframe[dataframe["timestamp"] >= cutoff].sort_values(
                    "timestamp"
                )
            return dataframe.sort_values("timestamp")
        return dataframe

    # ------------------------------------------------------------------
    # Charging session tracking
    # ------------------------------------------------------------------

    def _track_charging_session(self, timestamp, battery, location):
        """Track charging sessions with same state machine as CSVStorage."""
        debug_logger.push_context("_track_charging_session_oracle")
        try:
            is_charging = battery.get("is_charging", False)
            battery_level_raw = battery.get("level")
            charging_power_raw = battery.get("charging_power", 0)

            try:
                battery_level = DataValidator.validate_battery_level(
                    battery_level_raw, context="charging_session_tracking_oracle"
                )
            except ValueError as exc:
                logger.error("Battery validation failed: %s", exc)
                return

            if battery_level is None:
                return

            try:
                charging_power = (
                    DataValidator.validate_numeric(
                        charging_power_raw,
                        context="charging_power",
                        min_val=0,
                        max_val=350,
                    )
                    or 0
                )
            except ValueError:
                charging_power = 0

            # Find active session
            active_session = self._get_active_charging_session()

            if is_charging:
                if active_session is None:
                    self._start_charging_session(
                        timestamp, battery_level, charging_power, location
                    )
                else:
                    # Check for session gap
                    if active_session.get("end_time") and pd.notna(
                        active_session["end_time"]
                    ):
                        last_update = pd.to_datetime(active_session["end_time"])
                        current_time = pd.to_datetime(timestamp)
                        time_diff_minutes = (
                            current_time - last_update
                        ).total_seconds() / 60

                        if time_diff_minutes > self.charging_gap_threshold_minutes:
                            self._complete_charging_session(
                                active_session["session_id"],
                                active_session["end_time"],
                                active_session.get("end_battery", battery_level),
                            )
                            self._start_charging_session(
                                timestamp,
                                battery_level,
                                charging_power,
                                location,
                            )
                        else:
                            self._update_charging_session(
                                active_session["session_id"],
                                timestamp,
                                battery_level,
                                charging_power,
                            )
                    else:
                        self._update_charging_session(
                            active_session["session_id"],
                            timestamp,
                            battery_level,
                            charging_power,
                        )
            elif active_session is not None:
                self._complete_charging_session(
                    active_session["session_id"], timestamp, battery_level
                )

        except Exception as exc:
            logger.error("Error tracking charging session: %s", exc)
        finally:
            debug_logger.pop_context()

    def _get_active_charging_session(self):
        """Find the most recent incomplete charging session."""
        query = """
            SELECT session_id, start_time, end_time, duration_minutes,
                   start_battery, end_battery, energy_added,
                   avg_power, max_power, location_lat, location_lon,
                   is_complete
            FROM charging_sessions
            WHERE is_complete = 'False'
            ORDER BY start_time DESC
            FETCH FIRST 1 ROW ONLY
        """
        with self._get_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(query)
            row = cursor.fetchone()
            if row:
                columns = [desc[0].lower() for desc in cursor.description]
                return dict(zip(columns, row))
        return None

    def _start_charging_session(
        self, timestamp, battery_level, charging_power, location
    ):
        """Insert a new charging session."""
        session_id = f"charge_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        insert_sql = """
            INSERT INTO charging_sessions (
                session_id, start_time, end_time, duration_minutes,
                start_battery, end_battery, energy_added,
                avg_power, max_power, location_lat, location_lon, is_complete
            ) VALUES (
                :session_id, :start_time, NULL, 0,
                :start_battery, :end_battery, 0,
                :avg_power, :max_power, :location_lat, :location_lon, 'False'
            )
        """
        params = {
            "session_id": session_id,
            "start_time": self._parse_timestamp(timestamp),
            "start_battery": battery_level,
            "end_battery": battery_level,
            "avg_power": charging_power or 0,
            "max_power": charging_power or 0,
            "location_lat": (
                self._to_float(location.get("latitude")) if location else None
            ),
            "location_lon": (
                self._to_float(location.get("longitude")) if location else None
            ),
        }

        with self._get_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(insert_sql, params)
                connection.commit()
                logger.info("Started new Oracle charging session: %s", session_id)
            except oracledb.DatabaseError as exc:
                logger.error("Error starting charging session: %s", exc)
                connection.rollback()

    def _update_charging_session(
        self, session_id, timestamp, battery_level, charging_power
    ):
        """Update an ongoing charging session."""
        # First get the current session data
        with self._get_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT start_time, start_battery, max_power "
                "FROM charging_sessions WHERE session_id = :1",
                [session_id],
            )
            row = cursor.fetchone()
            if not row:
                return

            start_time = row[0]
            start_battery = row[1] or 0
            current_max_power = row[2] or 0

            end_time = self._parse_timestamp(timestamp)
            if start_time and end_time:
                duration_minutes = (end_time - start_time).total_seconds() / 60
            else:
                duration_minutes = 0

            max_power = max(charging_power or 0, current_max_power)

            energy_added = 0.0
            battery_diff = battery_level - start_battery
            if battery_diff > 0:
                energy_added = round(
                    (battery_diff / 100) * self.battery_capacity_kwh, 2
                )

            avg_power = 0.0
            if duration_minutes > 0 and energy_added > 0:
                avg_power = round(energy_added / (duration_minutes / 60), 2)

            update_sql = """
                UPDATE charging_sessions
                SET end_time = :end_time,
                    end_battery = :end_battery,
                    duration_minutes = :duration_minutes,
                    energy_added = :energy_added,
                    avg_power = :avg_power,
                    max_power = :max_power
                WHERE session_id = :session_id
            """
            params = {
                "end_time": end_time,
                "end_battery": battery_level,
                "duration_minutes": round(duration_minutes, 1),
                "energy_added": energy_added,
                "avg_power": avg_power,
                "max_power": max_power,
                "session_id": session_id,
            }
            try:
                cursor.execute(update_sql, params)
                connection.commit()
            except oracledb.DatabaseError as exc:
                logger.error("Error updating charging session: %s", exc)
                connection.rollback()

    def _complete_charging_session(self, session_id, timestamp, battery_level):
        """Mark a charging session as complete."""
        self._update_charging_session(session_id, timestamp, battery_level, 0)

        with self._get_connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPDATE charging_sessions SET is_complete = 'True' "
                    "WHERE session_id = :1",
                    [session_id],
                )
                connection.commit()
                logger.info("Completed Oracle charging session: %s", session_id)
            except oracledb.DatabaseError as exc:
                logger.error("Error completing charging session: %s", exc)
                connection.rollback()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_sql(self, query, parse_dates=None):
        """Execute a query and return results as a DataFrame."""
        try:
            with self._get_connection() as connection:
                dataframe = pd.read_sql(query, connection)
                # Lowercase column names to match CSV output
                dataframe.columns = [col.lower() for col in dataframe.columns]
                if parse_dates:
                    for col in parse_dates:
                        if col in dataframe.columns:
                            dataframe[col] = pd.to_datetime(
                                dataframe[col], errors="coerce"
                            )
                return dataframe
        except Exception as exc:
            logger.error("Error reading from Oracle: %s", exc)
            return pd.DataFrame()

    @staticmethod
    def _parse_timestamp(value):
        """Parse a timestamp string to a datetime object."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            try:
                return pd.to_datetime(value)
            except Exception:
                return None

    @staticmethod
    def _to_float(value):
        """Safely convert a value to float or None."""
        if value is None:
            return None
        try:
            result = float(value)
            if pd.isna(result):
                return None
            return result
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_int(value):
        """Safely convert a value to int or None."""
        if value is None:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None
