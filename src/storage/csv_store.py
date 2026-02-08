"""
csv_store.py
This module provides the CSVStorage class for storing and retrieving vehicle data in CSV files.
It manages three types of data: trips, battery status, and locations, each stored in separate
CSV files.
The class supports appending new data, initializing files with headers, and loading data as 
pandas DataFrames.
Classes:
    CSVStorage: Handles initialization, storage, and retrieval of vehicle data in CSV files.
CSV Files:
    - trips.csv: Stores trip-related data such as distance, duration, speed, and energy consumption.
    - battery_status.csv: Stores battery-related data including level, charging status, range, 
      temperature, and odometer.
    - locations.csv: Stores location data with latitude, longitude, and last updated timestamp.
Dependencies:
    - csv
    - datetime
    - pathlib
    - pandas
Usage:
    storage = CSVStorage(data_dir="data")
    storage.store_vehicle_data(data)
    trips_df = storage.get_trips_df()
    battery_df = storage.get_battery_df()
    locations_df = storage.get_locations_df()
"""
import csv
import io
import logging
import os
from datetime import datetime
from pathlib import Path

from filelock import FileLock
import pandas as pd
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))
from src.utils.weather import WeatherService
from src.utils.debug import DebugLogger, DataValidator

# Set up logging
logger = logging.getLogger(__name__)
debug_logger = DebugLogger(__name__)
class CSVStorage:
    """CSVStorage class for storing and retrieving vehicle data in CSV files.
    This class manages three types of data: trips, battery status, and locations,
    each stored in separate CSV files. It supports appending new data, initializing
    files with headers, and loading data as pandas DataFrames.
    Attributes:
        data_dir (Path): Directory to store CSV files.
        trips_file (Path): Path to the trips CSV file.
        battery_file (Path): Path to the battery status CSV file.
        location_file (Path): Path to the locations CSV file.
    """
    def __init__(self, data_dir="data"):
        """Initialize CSVStorage with a directory to store data"""
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        self.trips_file = self.data_dir / "trips.csv"
        self.battery_file = self.data_dir / "battery_status.csv"
        self.location_file = self.data_dir / "locations.csv"
        self.charging_sessions_file = self.data_dir / "charging_sessions.csv"
        self.external_battery_file = self.data_dir / "external_battery.csv"

        # Initialize weather service
        self.weather_service = WeatherService()
        self.use_meteo = os.getenv('WEATHER_SOURCE', 'meteo').lower() == 'meteo'
        daily_limit = float(os.getenv('API_DAILY_LIMIT', '30'))
        poll_interval_minutes = (24 * 60) / daily_limit if daily_limit > 0 else 48.0
        gap_multiplier = float(os.getenv('CHARGING_SESSION_GAP_MULTIPLIER', '1.5'))
        self.charging_gap_threshold_minutes = max(poll_interval_minutes * gap_multiplier, 5.0)
        self.battery_capacity_kwh = float(os.getenv('BATTERY_CAPACITY_KWH', '77.4'))
        
        self._init_files()
    
    def _init_files(self):
        """Initialize CSV files with headers if they do not exist"""
        if not self.trips_file.exists():
            with open(self.trips_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'date', 'distance', 'duration', 
                    'average_speed', 'max_speed', 'idle_time', 'trips_count',
                    'total_consumed', 'regenerated_energy',
                    'accessories_consumed', 'climate_consumed', 'drivetrain_consumed',
                    'battery_care_consumed', 'odometer_start',
                    'end_latitude', 'end_longitude', 'end_temperature'
                ])
                writer.writeheader()
        
        if not self.battery_file.exists():
            with open(self.battery_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'battery_level', 'is_charging', 'charging_power',
                    'remaining_time', 'range', 'temperature', 'odometer',
                    'meteo_temp', 'vehicle_temp'
                ])
                writer.writeheader()
        
        if not self.location_file.exists():
            with open(self.location_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'latitude', 'longitude', 'last_updated'
                ])
                writer.writeheader()
        
        if not self.charging_sessions_file.exists():
            with open(self.charging_sessions_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'session_id', 'start_time', 'end_time', 'duration_minutes',
                    'start_battery', 'end_battery', 'energy_added', 'avg_power',
                    'max_power', 'location_lat', 'location_lon', 'is_complete'
                ])
                writer.writeheader()

        if not self.external_battery_file.exists():
            with open(self.external_battery_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'voltage', 'soc', 'temperature'
                ])
                writer.writeheader()
    
    def store_vehicle_data(self, data):
        """store vehicle data in CSV files
        This method will store the vehicle data in CSV files.
        It will create the files if they do not exist and append the data to them.
        The data should be a dictionary containing the relevant information.
        The keys in the dictionary should match the field names in the CSV files.

        Args:
            data (_type_): _description_
        """
        if not data:
            return
        
        timestamp = data.get('timestamp', datetime.now().isoformat())
        
        # Store trips with deduplication
        trips = data.get('trips', [])
        if trips:
            # Load existing trips to check for duplicates
            existing_trips = set()
            if self.trips_file.exists():
                existing_df = pd.read_csv(self.trips_file)
                if not existing_df.empty:
                    # Create unique identifier from date and distance (and odometer if available)
                    for _, row in existing_df.iterrows():
                        # Normalize date format (remove .0 from end)
                        date_str = str(row['date']).replace('.0', '') if pd.notna(row['date']) else ''
                        trip_id = f"{date_str}_{row['distance']}"
                        if pd.notna(row.get('odometer_start')):
                            trip_id += f"_{row['odometer_start']}"
                        existing_trips.add(trip_id)
            
            # Only write new trips
            new_trips = []
            skipped_count = 0
            for trip in trips:
                # Normalize date format (remove .0 from end)
                date_str = str(trip['date']).replace('.0', '') if trip.get('date') else ''
                trip_id = f"{date_str}_{trip['distance']}"
                if trip.get('odometer_start') is not None:
                    trip_id += f"_{trip['odometer_start']}"
                
                if trip_id not in existing_trips:
                    new_trips.append(trip)
                    existing_trips.add(trip_id)
                else:
                    skipped_count += 1
            
            if skipped_count > 0:
                logger.info(f"Skipped {skipped_count} duplicate trips")
            
            if new_trips:
                logger.info(f"Storing {len(new_trips)} new trips")
                with open(self.trips_file, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=[
                        'timestamp', 'date', 'distance', 'duration', 
                        'average_speed', 'max_speed', 'idle_time', 'trips_count',
                        'total_consumed', 'regenerated_energy',
                        'accessories_consumed', 'climate_consumed', 'drivetrain_consumed',
                        'battery_care_consumed', 'odometer_start',
                        'end_latitude', 'end_longitude', 'end_temperature'
                    ])
                    for trip in new_trips:
                        trip['timestamp'] = timestamp
                        writer.writerow(trip)
        
        # Store battery status
        battery = data.get('battery', {})
        if battery:
            # Get BOTH temperatures for logging
            meteo_temp = None
            vehicle_temp = None
            
            # Get vehicle sensor temperature
            temp_f_vehicle = data.get('raw_data', {}).get('airTemp', {}).get('value')
            vehicle_temp = round((temp_f_vehicle - 32) * 5/9, 1) if temp_f_vehicle else None
            
            # Get Meteo weather temperature
            location = data.get('location', {})
            lat = location.get('latitude')
            lon = location.get('longitude')
            
            if lat and lon:
                weather_data = self.weather_service.get_current_weather(lat, lon)
                if weather_data:
                    temp_f_meteo = weather_data.get('temperature')
                    meteo_temp = self.weather_service.get_temperature_in_celsius(temp_f_meteo) if temp_f_meteo else None
                    logger.info(f"Temperatures - Meteo: {temp_f_meteo}°F ({meteo_temp}°C), Vehicle: {temp_f_vehicle}°F ({vehicle_temp}°C)")
            else:
                logger.warning("No vehicle location available for weather data")
            
            # Use the configured source for the main temperature field
            temperature = meteo_temp if self.use_meteo else vehicle_temp
            
            with open(self.battery_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'battery_level', 'is_charging', 'charging_power',
                    'remaining_time', 'range', 'temperature', 'odometer',
                    'meteo_temp', 'vehicle_temp', 'is_cached'
                ])
                
                writer.writerow({
                    'timestamp': timestamp,
                    'battery_level': battery.get('level'),
                    'is_charging': battery.get('is_charging'),
                    'charging_power': battery.get('charging_power'),
                    'remaining_time': battery.get('remaining_time'),
                    'range': battery.get('range'),
                    'temperature': temperature,
                    'odometer': data.get('odometer'),
                    'meteo_temp': meteo_temp,
                    'vehicle_temp': vehicle_temp,
                    'is_cached': data.get('is_cached', False)
                })
            
            # Track charging sessions
            self._track_charging_session(timestamp, battery, location)
        
        # Store location
        location = data.get('location', {})
        if location and location.get('latitude'):
            with open(self.location_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'latitude', 'longitude', 'last_updated'
                ])
                writer.writerow({
                    'timestamp': timestamp,
                    'latitude': location.get('latitude'),
                    'longitude': location.get('longitude'),
                    'last_updated': location.get('last_updated')
                })
    
    def get_trips_df(self):
        """Get trips data as a DataFrame"""
        if self.trips_file.exists():
            return pd.read_csv(self.trips_file, parse_dates=['timestamp', 'date'])
        return pd.DataFrame()
    
    def get_battery_df(self):
        """Get battery data as a DataFrame"""
        if self.battery_file.exists():
            return pd.read_csv(self.battery_file, parse_dates=['timestamp'])
        return pd.DataFrame()
    
    def get_locations_df(self):
        """Get location data as a DataFrame"""
        if self.location_file.exists():
            return pd.read_csv(self.location_file, parse_dates=['timestamp', 'last_updated'])
        return pd.DataFrame()
    
    def get_charging_sessions_df(self):
        """Get charging sessions data as a DataFrame with normalized fields."""
        if not self.charging_sessions_file.exists():
            return pd.DataFrame()

        try:
            # Read CSV without automatic date parsing so we can handle partial data
            df = pd.read_csv(self.charging_sessions_file)
        except Exception as e:
            logger.error(f"Error reading charging sessions CSV: {e}")
            return pd.DataFrame()

        if df.empty:
            return df

        changed = False

        # Normalize start and end timestamps
        if 'start_time' in df.columns:
            df['start_time'] = pd.to_datetime(df['start_time'], errors='coerce')
            missing_start = df['start_time'].isna()

            if missing_start.any() and 'session_id' in df.columns:
                for idx in df[missing_start].index:
                    session_id = df.at[idx, 'session_id']
                    if isinstance(session_id, str) and session_id.startswith('charge_'):
                        parts = session_id.split('_')
                        if len(parts) >= 3:
                            date_part, time_part = parts[1], parts[2]
                            try:
                                derived = datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S")
                                df.at[idx, 'start_time'] = derived
                                changed = True
                            except ValueError:
                                logger.debug(f"Could not reconstruct start_time for {session_id}")

        if 'end_time' in df.columns:
            df['end_time'] = pd.to_datetime(df['end_time'], errors='coerce')

        # Normalize numeric columns
        for column in ['start_battery', 'end_battery', 'energy_added', 'avg_power', 'max_power', 'duration_minutes']:
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors='coerce')

        # Ensure boolean type for completion flag
        if 'is_complete' in df.columns:
            df['is_complete'] = df['is_complete'].apply(
                lambda val: str(val).strip().lower() == 'true' if pd.notna(val) else False
            )

        # Recompute duration when possible
        if {'start_time', 'end_time'}.issubset(df.columns):
            derived_duration = (df['end_time'] - df['start_time']).dt.total_seconds() / 60
            if 'duration_minutes' in df.columns:
                existing_duration = df['duration_minutes']
                needs_update = existing_duration.isna() | (existing_duration <= 0)
                # Replace clearly wrong values (>1 minute difference)
                delta = (derived_duration - existing_duration).abs()
                needs_update |= delta > 1
            else:
                needs_update = derived_duration.notna()
                df['duration_minutes'] = None
            if needs_update.any():
                df.loc[needs_update, 'duration_minutes'] = derived_duration.round(1)
                changed = True

        # Recompute energy_added from battery delta when available
        if {'start_battery', 'end_battery'}.issubset(df.columns):
            battery_delta = df['end_battery'] - df['start_battery']
            estimated_energy = (battery_delta / 100.0) * self.battery_capacity_kwh
            if 'energy_added' in df.columns:
                needs_energy = df['energy_added'].isna() | (df['energy_added'] <= 0)
            else:
                needs_energy = battery_delta.notna()
                df['energy_added'] = None
            needs_energy &= battery_delta > 0
            if needs_energy.any():
                df.loc[needs_energy, 'energy_added'] = estimated_energy.round(2)
                changed = True

        # Recompute average power when we have duration and energy
        if {'energy_added', 'duration_minutes'}.issubset(df.columns):
            duration_hours = df['duration_minutes'] / 60.0
            duration_hours = duration_hours.replace({0: pd.NA})
            avg_power = df['energy_added'] / duration_hours
            needs_avg = duration_hours > 0
            if 'avg_power' in df.columns:
                existing_avg = df['avg_power']
                update_mask = needs_avg & (existing_avg.isna() | (existing_avg <= 0))
                # Replace if differs by more than 0.5 kW to correct rounding issues
                diff_mask = needs_avg & existing_avg.notna() & (avg_power - existing_avg).abs() > 0.5
                update_mask |= diff_mask
            else:
                update_mask = needs_avg
                df['avg_power'] = None
            if update_mask.any():
                df.loc[update_mask, 'avg_power'] = avg_power.round(2)
                changed = True

        if changed:
            try:
                df.to_csv(
                    self.charging_sessions_file,
                    index=False,
                    date_format='%Y-%m-%d %H:%M:%S.%f'
                )
            except Exception as e:
                logger.error(f"Failed to persist reconstructed charging session data: {e}")

        return df
    
    def _track_charging_session(self, timestamp, battery, location):
        """Track charging sessions - detect start/stop and update session data"""
        debug_logger.push_context("_track_charging_session")
        
        try:
            # Extract and validate data
            is_charging = battery.get('is_charging', False)
            battery_level_raw = battery.get('level')
            charging_power_raw = battery.get('charging_power', 0)
            
            # Log raw data in debug mode
            debug_logger.log_data('debug', 'Raw charging data', {
                'timestamp': timestamp,
                'battery': battery,
                'location': location
            })
            
            # Validate battery level
            try:
                battery_level = DataValidator.validate_battery_level(
                    battery_level_raw, 
                    context="charging_session_tracking"
                )
            except ValueError as e:
                logger.error(f"Battery validation failed: {e}")
                debug_logger.log_error_with_data("Battery validation failed", e, {
                    'battery_level_raw': battery_level_raw,
                    'battery_data': battery
                })
                return
            
            if battery_level is None:
                return
            
            # Validate charging power
            try:
                charging_power = DataValidator.validate_numeric(
                    charging_power_raw,
                    context="charging_power",
                    min_val=0,
                    max_val=350  # Max DC fast charging
                ) or 0
            except ValueError as e:
                logger.warning(f"Charging power validation failed, using 0: {e}")
                charging_power = 0
            
            # Read existing sessions to find active one
            sessions_df = self.get_charging_sessions_df()
            active_session = None
            
            if not sessions_df.empty:
                # Find incomplete sessions
                incomplete = sessions_df[sessions_df['is_complete'] == False]
                if not incomplete.empty:
                    active_session = incomplete.iloc[-1].to_dict()
            
            if is_charging:
                if active_session is None:
                    # Start new charging session
                    session_id = f"charge_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    new_session = {
                        'session_id': session_id,
                        'start_time': timestamp,
                        'end_time': None,
                        'duration_minutes': 0,
                        'start_battery': battery_level,
                        'end_battery': battery_level,
                        'energy_added': 0,
                        'avg_power': charging_power or 0,
                        'max_power': charging_power or 0,
                        'location_lat': location.get('latitude') if location else None,
                        'location_lon': location.get('longitude') if location else None,
                        'is_complete': False
                    }
                    
                    with open(self.charging_sessions_file, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=[
                            'session_id', 'start_time', 'end_time', 'duration_minutes',
                            'start_battery', 'end_battery', 'energy_added', 'avg_power',
                            'max_power', 'location_lat', 'location_lon', 'is_complete'
                        ])
                        writer.writerow(new_session)
                else:
                    # Check if this is truly the same session or a new one
                    threshold = getattr(self, 'charging_gap_threshold_minutes', 45.0)
                    if 'end_time' in active_session and pd.notna(active_session['end_time']):
                        last_update = pd.to_datetime(active_session['end_time'])
                        current_time = pd.to_datetime(timestamp)
                        time_diff = (current_time - last_update).total_seconds() / 60
                        
                        if time_diff > threshold:
                            # Complete the old session first
                            self._complete_charging_session(active_session['session_id'], 
                                                         active_session['end_time'], 
                                                         active_session.get('end_battery', battery_level))
                            
                            # Start new charging session
                            session_id = f"charge_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                            new_session = {
                                'session_id': session_id,
                                'start_time': timestamp,
                                'end_time': None,
                                'duration_minutes': 0,
                                'start_battery': battery_level,
                                'end_battery': battery_level,
                                'energy_added': 0,
                                'avg_power': charging_power or 0,
                                'max_power': charging_power or 0,
                                'location_lat': location.get('latitude') if location else None,
                                'location_lon': location.get('longitude') if location else None,
                                'is_complete': False
                            }
                            
                            with open(self.charging_sessions_file, 'a', newline='', encoding='utf-8') as f:
                                writer = csv.DictWriter(f, fieldnames=[
                                    'session_id', 'start_time', 'end_time', 'duration_minutes',
                                    'start_battery', 'end_battery', 'energy_added', 'avg_power',
                                    'max_power', 'location_lat', 'location_lon', 'is_complete'
                                ])
                                writer.writerow(new_session)
                        else:
                            # Update existing session
                            self._update_charging_session(active_session['session_id'], timestamp, battery_level, charging_power)
                    else:
                        # Update existing session
                        self._update_charging_session(active_session['session_id'], timestamp, battery_level, charging_power)
            else:
                if active_session is not None:
                    # End charging session
                    self._complete_charging_session(active_session['session_id'], timestamp, battery_level)
                    
        except Exception as e:
            logger.error(f"Error tracking charging session: {e}")
            debug_logger.log_error_with_data("Charging session tracking failed", e, {
                'timestamp': timestamp,
                'battery': battery,
                'location': location
            })
        finally:
            debug_logger.pop_context()
    
    def _update_charging_session(self, session_id, timestamp, battery_level, charging_power):
        """Update an ongoing charging session"""
        sessions_df = self.get_charging_sessions_df()
        
        if sessions_df.empty:
            return
        
        # Update the session
        idx = sessions_df[sessions_df['session_id'] == session_id].index
        if len(idx) > 0:
            idx = idx[0]
            sessions_df.loc[idx, 'end_battery'] = battery_level
            sessions_df.loc[idx, 'end_time'] = timestamp
            
            # Calculate duration
            start_time = pd.to_datetime(sessions_df.loc[idx, 'start_time'])
            end_time = pd.to_datetime(timestamp)
            duration = (end_time - start_time).total_seconds() / 60
            sessions_df.loc[idx, 'duration_minutes'] = round(duration, 1)
            
            # Update max power
            if charging_power and charging_power > sessions_df.loc[idx, 'max_power']:
                sessions_df.loc[idx, 'max_power'] = charging_power
            
            # Calculate energy added
            start_battery_raw = sessions_df.loc[idx, 'start_battery']
            
            # Validate start battery
            try:
                start_battery = DataValidator.validate_battery_level(
                    start_battery_raw,
                    context="start_battery_from_session"
                )
                if start_battery is None:
                    logger.warning(f"Missing start_battery for session {idx}, using 0")
                    start_battery = 0
            except ValueError as e:
                logger.error(f"Start battery validation failed: {e}")
                debug_logger.log_error_with_data("Start battery validation failed", e, {
                    'start_battery_raw': start_battery_raw,
                    'session_idx': idx,
                    'session_data': sessions_df.loc[idx].to_dict() if idx in sessions_df.index else None
                })
                start_battery = 0
            
            battery_diff = battery_level - start_battery
            if battery_diff > 0:
                # Assume 77.4 kWh battery (Ioniq 5 standard)
                energy_added = (battery_diff / 100) * 77.4
                sessions_df.loc[idx, 'energy_added'] = round(energy_added, 2)
            
            # Calculate average power
            if duration > 0 and sessions_df.loc[idx, 'energy_added'] > 0:
                avg_power = sessions_df.loc[idx, 'energy_added'] / (duration / 60)
                sessions_df.loc[idx, 'avg_power'] = round(avg_power, 2)
            
            # Save updated dataframe
            sessions_df.to_csv(self.charging_sessions_file, index=False)
    
    def _complete_charging_session(self, session_id, timestamp, battery_level):
        """Mark a charging session as complete"""
        self._update_charging_session(session_id, timestamp, battery_level, 0)
        
        sessions_df = self.get_charging_sessions_df()
        idx = sessions_df[sessions_df['session_id'] == session_id].index
        if len(idx) > 0:
            sessions_df.loc[idx[0], 'is_complete'] = True
            sessions_df.to_csv(self.charging_sessions_file, index=False)
    
    def get_latest_trips(self, limit=10):
        """Get the latest trips data"""
        df = self.get_trips_df()
        if not df.empty:
            return df.sort_values('timestamp', ascending=False).head(limit)
        return df
    
    def get_battery_history(self, days=7):
        """Get battery history for the last 'days' days. If days is None, return all data."""
        df = self.get_battery_df()
        if not df.empty:
            # Ensure timestamp is datetime (handle mixed formats)
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')

            if days is not None:
                cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
                return df[df['timestamp'] >= cutoff].sort_values('timestamp')
            else:
                # Return all data
                return df.sort_values('timestamp')
        return df

    def _read_recent_timestamps(self, filepath, tail_lines=500):
        """Read the timestamp column from the last *tail_lines* of a CSV file.

        Uses a seek-from-end approach so we never load the entire file into
        memory, even if the CSV grows to millions of rows.

        Returns:
            set[str]: Timestamp strings found in the tail portion.
        """
        timestamps = set()
        if not filepath.exists():
            return timestamps

        try:
            size = filepath.stat().st_size
            # Read a generous chunk from the end (100 bytes per line is plenty)
            read_size = min(size, tail_lines * 100)
            with open(filepath, 'rb') as f:
                f.seek(max(0, size - read_size))
                tail_bytes = f.read()

            # Decode, split into lines, skip any partial first line
            lines = tail_bytes.decode('utf-8', errors='replace').splitlines()
            if size > read_size:
                lines = lines[1:]  # first line is likely truncated

            reader = csv.DictReader(lines)
            for row in reader:
                ts = row.get('timestamp', '').strip()
                if ts:
                    timestamps.add(ts)
        except Exception as e:
            logger.warning("Could not read recent timestamps from %s: %s", filepath, e)

        return timestamps

    def write_unique_rows(self, rows):
        """Append rows to external_battery.csv, skipping duplicates by timestamp.

        Uses a file lock so the data_collector and the Flask API can safely
        write concurrently without corrupting the CSV.

        Args:
            rows: list[dict] — each dict must have keys: timestamp, voltage,
                  soc, and optionally temperature.

        Returns:
            tuple[int, int]: (added_count, skipped_count).
        """
        fieldnames = ['timestamp', 'voltage', 'soc', 'temperature']
        lock_path = self.external_battery_file.with_suffix('.lock')
        lock = FileLock(lock_path, timeout=10)

        with lock:
            existing_ts = self._read_recent_timestamps(self.external_battery_file)

            added = 0
            skipped = 0
            with open(self.external_battery_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                for row in rows:
                    ts = row.get('timestamp', '')
                    if ts in existing_ts:
                        skipped += 1
                        continue
                    writer.writerow({k: row.get(k) for k in fieldnames})
                    existing_ts.add(ts)
                    added += 1

        return added, skipped

    def get_external_battery_df(self):
        """Get external 12V battery data as a DataFrame."""
        if self.external_battery_file.exists():
            return pd.read_csv(self.external_battery_file, parse_dates=['timestamp'])
        return pd.DataFrame()
