import os
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from hyundai_kia_connect_api import VehicleManager, Vehicle
from dotenv import load_dotenv

load_dotenv()

class CachedVehicleClient:
    def __init__(self, cache_dir="cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
        self.username = os.getenv("BLUELINKUSER")
        self.password = os.getenv("BLUELINKPASS")
        self.pin = os.getenv("BLUELINKPIN")
        self.region = int(os.getenv("BLUELINKREGION", "3"))
        self.brand = int(os.getenv("BLUELINKBRAND", "2"))
        self.vehicle_id = os.getenv("BLUELINKVID")
        
        self.cache_enabled = os.getenv("API_CACHE_ENABLED", "true").lower() == "true"
        # Cache for 24 hours by default to respect 30 calls/day limit
        cache_hours = int(os.getenv("CACHE_DURATION_HOURS", "24"))
        self.cache_duration = timedelta(hours=cache_hours)
        
        self._setup_api()
    
    def _setup_api(self):
        # Check if credentials are provided
        if not all([self.username, self.password, self.pin]):
            print("Warning: Missing API credentials. API calls will not work.")
            self.manager = None
            return
            
        # The VehicleManager expects integers directly
        # From the API: REGIONS = {1: 'Europe', 2: 'Canada', 3: 'USA', 4: 'China', 5: 'Australia'}
        # From the API: BRANDS = {1: 'Kia', 2: 'Hyundai', 3: 'Genesis'}
        
        try:
            # Try to work around SSL issues
            if os.getenv("DISABLE_SSL_VERIFY", "false").lower() == "true":
                print("WARNING: SSL verification will be disabled")
                from .ssl_patch import patch_hyundai_ssl
                patch_hyundai_ssl()
            
            self.manager = VehicleManager(
                region=self.region,  # Use integer directly
                brand=self.brand,    # Use integer directly
                username=self.username,
                password=self.password,
                pin=self.pin
            )
        except Exception as e:
            print(f"Warning: Failed to initialize VehicleManager: {e}")
            self.manager = None
    
    def _get_cache_key(self, method_name):
        # For demo purposes, use a simple key
        if not self.vehicle_id:
            return "sample_data"
        key_string = f"{self.vehicle_id}_{method_name}"
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key):
        return self.cache_dir / f"{cache_key}.json"
    
    def _is_cache_valid(self, cache_path):
        if not cache_path.exists():
            return False
        
        modified_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
        return datetime.now() - modified_time < self.cache_duration
    
    def _get_cache_age(self, cache_key):
        cache_path = self._get_cache_path(cache_key)
        if cache_path.exists():
            modified_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
            age = datetime.now() - modified_time
            hours = int(age.total_seconds() / 3600)
            minutes = int((age.total_seconds() % 3600) / 60)
            return f"{hours}h {minutes}m"
        return "N/A"
    
    def _load_from_cache(self, cache_key):
        cache_path = self._get_cache_path(cache_key)
        if self.cache_enabled and self._is_cache_valid(cache_path):
            with open(cache_path, 'r') as f:
                return json.load(f)
        return None
    
    def _save_to_cache(self, cache_key, data):
        cache_path = self._get_cache_path(cache_key)
        with open(cache_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        
        # Also save timestamped copy for historical records
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        history_path = self.cache_dir / f"history_{timestamp}_{cache_key}.json"
        with open(history_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        
        # Clean up old historical files
        self._cleanup_old_cache_files()
    
    def _cleanup_old_cache_files(self):
        """Remove cache files older than cache duration"""
        try:
            cutoff_time = datetime.now() - self.cache_duration
            for file_path in self.cache_dir.glob("history_*.json"):
                if file_path.stat().st_mtime < cutoff_time.timestamp():
                    file_path.unlink()
                    print(f"Removed old cache file: {file_path.name}")
        except Exception as e:
            print(f"Error cleaning up cache files: {e}")
    
    def check_and_refresh_token(self):
        if not self.manager:
            return False
        try:
            print("Attempting to refresh token...")
            self.manager.check_and_refresh_token()
            print("Token refreshed successfully")
        except Exception as e:
            print(f"Error refreshing token: {type(e).__name__}: {e}")
            
            # Save token error for debugging
            error_data = {
                "timestamp": datetime.now().isoformat(),
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_stage": "token_refresh",
                "region": self.region,
                "brand": self.brand
            }
            
            error_path = self.cache_dir / f"error_token_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(error_path, 'w') as f:
                json.dump(error_data, f, indent=2)
            
            return False
        return True
    
    def get_vehicle_data(self):
        cache_key = self._get_cache_key("full_data")
        cached_data = self._load_from_cache(cache_key)
        
        if cached_data:
            print(f"Using cached vehicle data (age: {self._get_cache_age(cache_key)})")
            return cached_data
        
        if not self.manager:
            print("Error: VehicleManager not initialized")
            return None
            
        print(f"Fetching fresh vehicle data from API...")
        print(f"Region: {self.region}, Brand: {self.brand}, Vehicle ID: {self.vehicle_id}")
        
        if not self.check_and_refresh_token():
            return None
        
        try:
            print("Updating vehicle data...")
            self.manager.update_vehicle_with_cached_state(self.vehicle_id)
            vehicle = self.manager.get_vehicle(self.vehicle_id)
            
            if not vehicle:
                print("Vehicle not found")
                return None
            
            print(f"Vehicle data retrieved")
            
            # Get temperature from vehicle data
            air_temp = None
            if hasattr(vehicle, 'air_temperature'):
                air_temp = vehicle.air_temperature
            elif hasattr(vehicle, 'data') and 'air_temperature' in vehicle.data:
                air_temp = vehicle.data['air_temperature']
            
            # Extract range from raw data if available
            ev_range = None
            vehicle_data = getattr(vehicle, 'data', {})
            ev_status = vehicle_data.get('vehicleStatus', {}).get('evStatus', {})
            drv_distance = ev_status.get('drvDistance', [])
            if drv_distance and len(drv_distance) > 0:
                range_data = drv_distance[0].get('rangeByFuel', {}).get('totalAvailableRange', {})
                ev_range = range_data.get('value')
                # Unit 3 appears to be miles, convert to km
                if ev_range and range_data.get('unit') == 3:
                    ev_range = round(ev_range * 1.60934)  # Convert miles to km
            
            # Fallback to vehicle attributes if not found in raw data
            if ev_range is None:
                ev_range = getattr(vehicle, 'ev_range_with_ac', getattr(vehicle, 'ev_battery_remain', None))
            
            # Get odometer value - it's in miles for US region
            odometer_value = getattr(vehicle, 'odometer', None)
            
            # Always convert from miles to km for US region (region 3)
            if odometer_value is not None and self.region == 3:
                odometer_km = round(odometer_value * 1.60934)
                logger.info(f"Converting odometer from {odometer_value} miles to {odometer_km} km")
            else:
                odometer_km = odometer_value
            
            data = {
                "timestamp": datetime.now().isoformat(),
                "vehicle_id": self.vehicle_id,
                "odometer": odometer_km,
                "battery": {
                    "level": getattr(vehicle, 'ev_battery_percentage', None),
                    "is_charging": getattr(vehicle, 'ev_battery_is_charging', False),
                    "remaining_time": None,  # Not in the new API
                    "range": ev_range
                },
                "location": {
                    "latitude": getattr(vehicle, 'location_latitude', None),
                    "longitude": getattr(vehicle, 'location_longitude', None),
                    "last_updated": str(getattr(vehicle, 'location_last_updated_at', None))
                },
                "trips": self._extract_trips(vehicle),
                "raw_data": {
                    "airTemp": {"value": air_temp},
                    **getattr(vehicle, 'data', {})
                }
            }
            
            self._save_to_cache(cache_key, data)
            return data
            
        except Exception as e:
            print(f"Error fetching vehicle data: {type(e).__name__}: {e}")
            
            # Save error for debugging
            error_data = {
                "timestamp": datetime.now().isoformat(),
                "error_type": type(e).__name__,
                "error_message": str(e),
                "region": self.region,
                "brand": self.brand,
                "vehicle_id": self.vehicle_id
            }
            
            error_path = self.cache_dir / f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(error_path, 'w') as f:
                json.dump(error_data, f, indent=2)
            
            return None
    
    def _extract_trips(self, vehicle):
        trips = []
        
        # First check for detailed evTripDetails in raw data
        if hasattr(vehicle, 'data') and 'evTripDetails' in vehicle.data:
            ev_trip_details = vehicle.data.get('evTripDetails', {})
            trip_details = ev_trip_details.get('tripdetails', [])
            
            for trip in trip_details:
                # Convert units: speeds from mph to km/h, duration from seconds to minutes
                avg_speed_mph = trip.get('avgspeed', {}).get('value', 0)
                max_speed_mph = trip.get('maxspeed', {}).get('value', 0)
                duration_sec = trip.get('duration', {}).get('value', 0)
                driving_time_sec = trip.get('mileagetime', {}).get('value', 0)
                
                trips.append({
                    "date": trip.get('startdate'),
                    "distance": trip.get('distance'),  # Already in km for US region
                    "duration": round(duration_sec / 60) if duration_sec else None,  # Convert to minutes
                    "average_speed": round(avg_speed_mph * 1.60934) if avg_speed_mph else None,  # Convert to km/h
                    "max_speed": round(max_speed_mph * 1.60934) if max_speed_mph else None,  # Convert to km/h
                    "idle_time": round((duration_sec - driving_time_sec) / 60) if duration_sec and driving_time_sec else None,
                    "trips_count": 1,
                    "total_consumed": trip.get('totalused'),
                    "regenerated_energy": trip.get('regen'),
                    "accessories_consumed": trip.get('accessories'),
                    "climate_consumed": trip.get('climate'),
                    "drivetrain_consumed": trip.get('drivetrain'),
                    "battery_care_consumed": trip.get('batterycare'),
                    "odometer_start": trip.get('odometer', {}).get('value')
                })
        
        # Fallback to daily_stats if no detailed trips
        elif hasattr(vehicle, 'daily_stats'):
            daily_stats = vehicle.daily_stats
            if daily_stats:
                for stat in daily_stats:
                    # Handle DailyDrivingStats objects
                    if hasattr(stat, 'date'):
                        trips.append({
                            "date": str(stat.date) if hasattr(stat, 'date') else None,
                            "distance": stat.distance if hasattr(stat, 'distance') else None,
                            "duration": None,
                            "average_speed": None,
                            "max_speed": None,
                            "idle_time": None,
                            "trips_count": 1,
                            "total_consumed": stat.total_consumed if hasattr(stat, 'total_consumed') else None,
                            "regenerated_energy": stat.regenerated_energy if hasattr(stat, 'regenerated_energy') else None,
                            "accessories_consumed": None,
                            "climate_consumed": None,
                            "drivetrain_consumed": None,
                            "battery_care_consumed": None,
                            "odometer_start": None
                        })
        
        # Also check in vehicle.data
        elif hasattr(vehicle, 'data') and 'daily_stats' in vehicle.data:
            daily_stats = vehicle.data['daily_stats']
            if isinstance(daily_stats, list):
                for stat in daily_stats:
                    trips.append({
                        "date": stat.get("date"),
                        "distance": stat.get("distance"),
                        "duration": None,
                        "average_speed": None,
                        "max_speed": None,
                        "idle_time": None,
                        "trips_count": 1,
                        "total_consumed": stat.get("total_consumed"),
                        "regenerated_energy": stat.get("regenerated_energy"),
                        "accessories_consumed": None,
                        "climate_consumed": None,
                        "drivetrain_consumed": None,
                        "battery_care_consumed": None,
                        "odometer_start": None
                    })
        
        return trips