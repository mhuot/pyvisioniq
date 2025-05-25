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
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd

# Set up logging
logger = logging.getLogger(__name__)
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
                    'remaining_time', 'range', 'temperature', 'odometer'
                ])
                writer.writeheader()
        
        if not self.location_file.exists():
            with open(self.location_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'latitude', 'longitude', 'last_updated'
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
            with open(self.battery_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'battery_level', 'is_charging', 'charging_power',
                    'remaining_time', 'range', 'temperature', 'odometer'
                ])
                # Convert temperature from F to C if present
                temp_f = data.get('raw_data', {}).get('airTemp', {}).get('value')
                temp_c = round((temp_f - 32) * 5/9, 1) if temp_f else None
                
                writer.writerow({
                    'timestamp': timestamp,
                    'battery_level': battery.get('level'),
                    'is_charging': battery.get('is_charging'),
                    'charging_power': battery.get('charging_power'),
                    'remaining_time': battery.get('remaining_time'),
                    'range': battery.get('range'),
                    'temperature': temp_c,
                    'odometer': data.get('odometer')
                })
        
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
    
    def get_latest_trips(self, limit=10):
        """Get the latest trips data"""
        df = self.get_trips_df()
        if not df.empty:
            return df.sort_values('timestamp', ascending=False).head(limit)
        return df
    
    def get_battery_history(self, days=7):
        """Get battery history for the last 'days' days"""
        df = self.get_battery_df()
        if not df.empty:
            # Ensure timestamp is datetime (handle mixed formats)
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
            return df[df['timestamp'] >= cutoff].sort_values('timestamp')
        return df