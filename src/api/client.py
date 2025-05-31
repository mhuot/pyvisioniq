import os
import json
import hashlib
import logging
import time
import random
from datetime import datetime, timedelta
from pathlib import Path
from hyundai_kia_connect_api import VehicleManager, Vehicle
from dotenv import load_dotenv

load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)

class APIError(Exception):
    """Custom exception for API errors with user-friendly messages"""
    def __init__(self, message, error_type="general", original_error=None):
        self.message = message
        self.error_type = error_type
        self.original_error = original_error
        super().__init__(message)

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
        
        # Cache validity: How long to consider cached data "fresh" before making a new API call
        # Calculate based on API daily limit (e.g., 30 calls/day = ~48 minutes between calls)
        daily_limit = int(os.getenv("API_DAILY_LIMIT", "30"))
        cache_validity_minutes = (24 * 60 / daily_limit) * 0.95  # 95% of interval for safety margin
        self.cache_validity = timedelta(minutes=cache_validity_minutes)
        logger.info(f"Cache validity set to {cache_validity_minutes:.1f} minutes based on {daily_limit} daily API calls")
        
        # Cache retention: How long to keep historical cache files for debugging/analysis
        # This is separate from cache validity - we keep files longer for historical reference
        cache_retention_hours = float(os.getenv("CACHE_DURATION_HOURS", "48"))  # Default 48 hours
        self.cache_retention = timedelta(hours=cache_retention_hours)
        logger.info(f"Cache retention set to {cache_retention_hours} hours")
        
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
        return datetime.now() - modified_time < self.cache_validity
    
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
        """Remove historical cache files older than retention period"""
        try:
            cutoff_time = datetime.now() - self.cache_retention
            removed_count = 0
            for file_path in self.cache_dir.glob("history_*.json"):
                if file_path.stat().st_mtime < cutoff_time.timestamp():
                    file_path.unlink()
                    removed_count += 1
            if removed_count > 0:
                logger.info(f"Removed {removed_count} cache files older than {self.cache_retention}")
        except Exception as e:
            logger.error(f"Error cleaning up cache files: {e}")
    
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
    
    def force_cache_update(self):
        """Force an API call and cache the result, bypassing normal cache checks"""
        cache_key = self._get_cache_key("full_data")
        
        if not self.manager:
            print("Error: VehicleManager not initialized")
            return None
            
        print(f"Forcing cache update from API...")
        print(f"Region: {self.region}, Brand: {self.brand}, Vehicle ID: {self.vehicle_id}")
        
        if not self.check_and_refresh_token():
            return None
        
        try:
            # Update and get vehicle data
            success = self._update_vehicle_with_retry()
            if success:
                vehicle = None
                try:
                    vehicle = self.manager.get_vehicle(self.vehicle_id)
                except:
                    # Try first vehicle
                    vehicles = self.manager.vehicles
                    if vehicles:
                        vehicle = vehicles[0]
                
                if vehicle:
                    # Process and save to cache
                    data = self._process_vehicle_data(vehicle)
                    if data:
                        self._save_to_cache(cache_key, data)
                        print("Cache updated successfully!")
                        return data
            
            print("Failed to update cache")
            return None
            
        except Exception as e:
            print(f"Error during force cache update: {e}")
            return None
    
    def _process_vehicle_data(self, vehicle):
        """Process vehicle object into standardized data format"""
        try:
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
            
            # Get charging power from raw data
            charging_power = None
            vehicle_data = getattr(vehicle, 'data', {})
            ev_status = vehicle_data.get('vehicleStatus', {}).get('evStatus', {})
            if ev_status:
                charging_power = ev_status.get('batteryStndChrgPower')
                if charging_power is None:
                    # Try the attribute if available
                    charging_power = getattr(vehicle, 'ev_charging_current', None)
            
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
                "api_last_updated": getattr(vehicle, 'last_updated_at', None),
                "vehicle_id": self.vehicle_id,
                "odometer": odometer_km,
                "battery": {
                    "level": getattr(vehicle, 'ev_battery_percentage', None),
                    "is_charging": getattr(vehicle, 'ev_battery_is_charging', False),
                    "charging_power": charging_power,
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
            
            return data
            
        except Exception as e:
            logger.error(f"Error processing vehicle data: {e}")
            import traceback
            traceback.print_exc()
            return None
    
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
            
            # Check if vehicle_id is set
            if not self.vehicle_id:
                logger.error("Vehicle ID is not set! Check BLUELINKVID environment variable")
                return None
            
            # Attempt to update with retry logic for rate limits
            success = self._update_vehicle_with_retry()
            if not success:
                logger.warning("Failed to update vehicle data after retries")
                return self._get_last_successful_cache()
            
            # Get the vehicle after successful update
            logger.info(f"Getting vehicle with ID: {self.vehicle_id}")
            vehicle = None
            
            # Try to get vehicle by ID first
            try:
                vehicle = self.manager.get_vehicle(self.vehicle_id)
            except Exception as e:
                logger.warning(f"Could not get vehicle by ID {self.vehicle_id}: {e}")
            
            # If that fails, try to get the first available vehicle
            if not vehicle:
                try:
                    vehicles = self.manager.vehicles
                    if vehicles:
                        vehicle = vehicles[0]
                        logger.info(f"Using first available vehicle: {vehicle.id if hasattr(vehicle, 'id') else 'unknown'}")
                        # Update the vehicle_id for future use
                        if hasattr(vehicle, 'id'):
                            self.vehicle_id = vehicle.id
                    else:
                        logger.error("No vehicles found in account")
                except Exception as e:
                    logger.error(f"Could not get vehicles list: {e}")
            
            if not vehicle:
                logger.error("No vehicle data available")
                return self._get_last_successful_cache()
            
            print(f"Vehicle data retrieved")
            
            # Process the vehicle data
            data = self._process_vehicle_data(vehicle)
            
            if data:
                self._save_to_cache(cache_key, data)
                return data
            else:
                logger.error("Failed to process vehicle data")
                return self._get_last_successful_cache()
            
        except Exception as e:
            print(f"Error fetching vehicle data: {type(e).__name__}: {e}")
            
            # Check if this is a vehicleStatus KeyError
            if isinstance(e, KeyError) and str(e) == "'vehicleStatus'":
                logger.info("vehicleStatus not available in API response, using last successful cache")
                # Don't save error file for this known issue
                return self._get_last_successful_cache()
            
            # Save error for debugging (non-maintenance window errors)
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
    
    def _classify_error(self, error):
        """Classify error and return user-friendly message"""
        error_msg = str(error).lower()
        error_type = type(error).__name__
        
        # Rate limit errors
        if any(phrase in error_msg for phrase in [
            'rate limit', 'too many requests', 'quota exceeded', 
            'throttled', '429', 'limit exceeded', 'quota'
        ]):
            return APIError(
                "API rate limit reached. The Hyundai/Kia API allows only 30 calls per day. Please try again later.",
                "rate_limit",
                error
            )
        
        # Authentication errors
        if any(phrase in error_msg for phrase in [
            'unauthorized', '401', 'authentication', 'invalid credentials',
            'login failed', 'token expired', 'forbidden', '403'
        ]):
            return APIError(
                "Authentication failed. Please check your Bluelink credentials in the environment variables.",
                "auth",
                error
            )
        
        # Vehicle not found
        if any(phrase in error_msg for phrase in [
            'vehicle not found', 'no vehicles', 'invalid vehicle'
        ]):
            return APIError(
                "Vehicle not found. Please verify your vehicle ID is correct.",
                "vehicle_not_found",
                error
            )
        
        # Network/Connection errors
        if any(phrase in error_msg for phrase in [
            'connection', 'timeout', 'network', 'unreachable', 
            'ssl', 'certificate', 'handshake'
        ]):
            return APIError(
                "Network error connecting to Hyundai/Kia servers. Please check your internet connection and try again.",
                "network",
                error
            )
        
        # Service unavailable
        if any(phrase in error_msg for phrase in [
            'service unavailable', '503', 'maintenance', 'temporarily unavailable',
            '500', 'server error', '502', 'bad gateway'
        ]):
            return APIError(
                "Hyundai/Kia servers are temporarily unavailable. This is usually temporary - please try again in a few minutes.",
                "service_unavailable",
                error
            )
        
        # Vehicle communication errors
        if any(phrase in error_msg for phrase in [
            'vehicle offline', 'cannot reach vehicle', 'vehicle communication',
            'remote command failed', 'vehicle not responding'
        ]):
            return APIError(
                "Cannot communicate with vehicle. Make sure your vehicle is in an area with cellular coverage and try again.",
                "vehicle_offline",
                error
            )
        
        # Default error
        return APIError(
            f"An unexpected error occurred: {error_type}: {str(error)}",
            "unknown",
            error
        )
    
    def _update_vehicle_with_retry(self, max_retries=3):
        """Update vehicle data with exponential backoff for rate limits"""
        last_error = None
        last_action = None
        for attempt in range(max_retries):
            print(f"Attempt {attempt + 1} of {max_retries} to update vehicle data...")
            try:
                # Add jitter to prevent thundering herd
                if attempt > 0:
                    base_delay = 2 ** attempt  # Exponential backoff: 2, 4, 8 seconds
                    jitter = random.uniform(0.5, 1.5)  # Add randomness
                    delay = base_delay * jitter
                    logger.info(f"Rate limit retry {attempt + 1}/{max_retries}, waiting {delay:.1f}s")
                    time.sleep(delay)
                
                # Force refresh vehicle state to get fresh data from the vehicle
                if self.vehicle_id:
                    last_action = f"Forcing refresh of state for vehicle {self.vehicle_id}"
                    self.manager.force_refresh_vehicle_state(self.vehicle_id)
                    logger.info(f"Vehicle {self.vehicle_id} state refreshed successfully")
                else:
                    # Fallback to refresh all vehicles if no specific ID
                    last_action = "Forcing refresh of state for all vehicles"
                    self.manager.force_refresh_all_vehicles_states()
                    logger.info("All vehicles states refreshed successfully")
                return True
                
            except Exception as e:
                last_error = self._classify_error(e)
                print(f"Error during vehicle update: {last_error.message} (attempt {attempt + 1}/{max_retries})")
                print(f"Last action: {last_action}")
                
                # Check if it's a KeyError for vehicleStatus
                if isinstance(e, KeyError) and str(e) == "'vehicleStatus'":
                    logger.info("vehicleStatus not available in API response, attempting cached state")
                    try:
                        # Try cached state instead
                        if self.vehicle_id:
                            self.manager.update_vehicle_with_cached_state(self.vehicle_id)
                            logger.info(f"Successfully retrieved cached state for vehicle {self.vehicle_id}")
                        else:
                            self.manager.update_all_vehicles_with_cached_state()
                            logger.info("Successfully retrieved cached state for all vehicles")
                        return True
                    except Exception as cached_error:
                        logger.error(f"Cached state fallback also failed: {cached_error}")
                        # Continue with original error handling
                
                # Only retry for rate limit errors
                if last_error.error_type == "rate_limit":
                    logger.warning(f"Rate limit hit (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt == max_retries - 1:
                        logger.error("Max retries reached for rate limit")
                        raise last_error
                    continue
                else:
                    # Non-rate-limit error, don't retry
                    logger.error(f"Non-retryable error: {last_error.message}")
                    raise last_error
        
        # If we get here, we exhausted retries for rate limit
        if last_error:
            raise last_error
        return False
    
    def _get_last_successful_cache(self):
        """Get the most recent successful cache file as fallback"""
        try:
            # Look for recent cache files
            cache_files = list(self.cache_dir.glob("*.json"))
            cache_files = [f for f in cache_files if not f.name.startswith("error_")]
            
            if not cache_files:
                logger.warning("No cache files found for fallback")
                return None
                
            # Get the most recent cache file
            latest_cache = max(cache_files, key=lambda f: f.stat().st_mtime)
            
            # Check if it's within retention period
            file_age = datetime.now() - datetime.fromtimestamp(latest_cache.stat().st_mtime)
            if file_age > self.cache_retention:
                logger.warning(f"Latest cache file is {file_age} old, exceeds retention period")
                return None
            
            logger.info(f"Using fallback cache from {file_age} ago")
            with open(latest_cache, 'r') as f:
                return json.load(f)
                
        except Exception as e:
            logger.error(f"Error loading fallback cache: {e}")
            return None
    
    def _extract_trips(self, vehicle):
        trips = []
        
        # Get current location and temperature for the most recent trip
        current_location = {
            "latitude": getattr(vehicle, 'location_latitude', None),
            "longitude": getattr(vehicle, 'location_longitude', None)
        }
        
        # Get current temperature (in Fahrenheit)
        current_temp = None
        if hasattr(vehicle, 'data'):
            # Temperature is at the top level: data.airTemp.value
            air_temp_data = vehicle.data.get('airTemp', {})
            air_temp = air_temp_data.get('value')
            if air_temp is not None:
                # Handle "LO" or other string values
                if isinstance(air_temp, str) and air_temp.upper() == 'LO':
                    current_temp = None
                else:
                    try:
                        # Keep temperature in Fahrenheit as requested
                        current_temp = float(air_temp) if isinstance(air_temp, str) else air_temp
                    except (ValueError, TypeError):
                        logger.warning(f"Could not convert temperature value: {air_temp}")
        
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
                
                trip_data = {
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
                    "odometer_start": trip.get('odometer', {}).get('value'),
                    "end_latitude": None,
                    "end_longitude": None,
                    "end_temperature": None
                }
                trips.append(trip_data)
        
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
                            "odometer_start": None,
                            "end_latitude": None,
                            "end_longitude": None,
                            "end_temperature": None
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
                        "odometer_start": None,
                        "end_latitude": None,
                        "end_longitude": None,
                        "end_temperature": None
                    })
        
        # Add current location and temperature to the most recent trip (first in list)
        if trips and current_location['latitude'] is not None:
            # Find the most recent trip by date
            sorted_trips = sorted(trips, key=lambda x: x['date'] if x['date'] else '', reverse=True)
            if sorted_trips:
                most_recent = sorted_trips[0]
                # Find this trip in the original list and update it
                for i, trip in enumerate(trips):
                    if trip['date'] == most_recent['date']:
                        trips[i]['end_latitude'] = current_location['latitude']
                        trips[i]['end_longitude'] = current_location['longitude']
                        trips[i]['end_temperature'] = current_temp
                        logger.info(f"Added location ({current_location['latitude']}, {current_location['longitude']}) and temp {current_temp}°C to trip from {trip['date']}")
                        break
        
        return trips