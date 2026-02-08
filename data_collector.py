#!/usr/bin/env python3
"""
Data collector for PyVisionic
Collects vehicle data 30 times per day (every 48 minutes)
"""
import os
import sys
import time
import json
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta
from pathlib import Path
from src.api.client import CachedVehicleClient
from src.api.rate_limiter import get_rate_limiter
from src.storage.csv_store import CSVStorage

sys.path.append(str(Path(__file__).parent))

# Load environment first
load_dotenv()

# Import debug utilities
sys.path.append(str(Path(__file__).parent))
from src.utils.debug import setup_debug_logging, DebugLogger

# Set up logging based on debug mode
DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
log_level = setup_debug_logging(DEBUG_MODE)

logger = logging.getLogger(__name__)
debug_logger = DebugLogger(__name__)

if DEBUG_MODE:
    logger.info("Running in DEBUG mode - verbose logging enabled")

class DataCollector:
    """DataCollector class for collecting vehicle data from the API
    and storing it in CSV files. It manages API call limits,
    schedules data collection, and handles data storage.
    Attributes:
        client (CachedVehicleClient): API client for vehicle data.
        storage (CSVStorage): Storage manager for CSV files.
        daily_limit (int): Daily API call limit.
        collection_interval_minutes (int): Minutes between collections.
        calls_today (int): Number of API calls made today.
        last_reset (datetime.date): Date of the last reset.
        last_call_time (datetime): Time of the last API call.
    Methods:
        load_call_history(): Load API call history from file.
        save_call_history(): Save API call history to file.
        reset_daily_counter(): Reset the daily API call counter.
        can_make_api_call(): Check if we can make an API call without exceeding limits.
        collect_data(): Collect vehicle data from API and store it in CSV files.
        calculate_next_collection_time(): Calculate optimal collection times based on last call and interval.
        run_forever(): Run the collector continuously, collecting data at scheduled times.
        run_once(): Run a single collection (for testing).
    """
    def __init__(self):
        """Initialize DataCollector with API client and storage manager"""
        self.client = CachedVehicleClient()
        self.storage = CSVStorage()
        self.daily_limit = int(os.getenv('API_DAILY_LIMIT', "30"))  # Default to 30 calls per day
        if self.daily_limit <= 0:
            logger.error("Invalid daily limit: %d", self.daily_limit)
            raise ValueError("Daily limit must be a positive integer")
        self.collection_interval_minutes = (24 * 60) // self.daily_limit  # Minutes between collections

        # Use the shared rate limiter for call tracking
        self.rate_limiter = get_rate_limiter(daily_limit=self.daily_limit)

        # Legacy attributes kept for backward compat with run_forever logging
        self.calls_today = self.rate_limiter.calls_today
        self.last_call_time = self.rate_limiter.last_call_time
    
    def can_make_api_call(self):
        """Check if we can make an API call without exceeding limits"""
        return self.rate_limiter.can_make_call()

    def _sync_from_tracker(self):
        """Sync local attributes from the shared rate limiter."""
        self.calls_today = self.rate_limiter.calls_today
        self.last_call_time = self.rate_limiter.last_call_time
    
    def collect_data(self):
        """Collect vehicle data from API"""
        if not self.can_make_api_call():
            logger.warning(
                "Daily API limit reached (%d/%d), %d remaining",
                self.rate_limiter.calls_today,
                self.rate_limiter.daily_limit,
                self.rate_limiter.remaining_calls,
            )
            return False

        try:
            logger.info("Collecting vehicle data...")

            # get_vehicle_data now records the call via the shared rate limiter
            data = self.client.get_vehicle_data(source="data_collector")

            if data:
                # Store in CSV files
                self.storage.store_vehicle_data(data)

                # Sync local counters from tracker
                self._sync_from_tracker()

                logger.info(
                    "Data collected successfully (call %d/%d, %d remaining)",
                    self.rate_limiter.calls_today,
                    self.rate_limiter.daily_limit,
                    self.rate_limiter.remaining_calls,
                )
                # Temperature from API is in Fahrenheit
                temp_f = data.get('raw_data', {}).get('airTemp', {}).get('value')
                battery_info = data.get('battery', {})

                logger.info("Battery: %s%%, Range: %skm, Temp: %s°F",
                          battery_info.get('level', 'N/A'),
                          battery_info.get('range', 'N/A'),
                          temp_f if temp_f else 'N/A')
                return True
            else:
                logger.error("Failed to collect data - no data returned")
                return False

        except Exception as e:
            error_msg = str(e).lower()

            # Check for rate limit errors
            if any(phrase in error_msg for phrase in [
                'rate limit', 'too many requests', 'quota exceeded',
                'throttled', '429', 'limit exceeded'
            ]):
                self.rate_limiter.record_rate_limit_hit(
                    source="data_collector",
                    error_message=str(e),
                )
                logger.warning("Rate limit exceeded, backoff now %.1fx", self.rate_limiter.backoff_multiplier)
                return False
            else:
                logger.error("Error collecting data: %s", e)
                return False
    
    def calculate_next_collection_time(self):
        """Calculate optimal collection times based on last call and interval"""
        now = datetime.now()
        self._sync_from_tracker()

        # If we have a last call time, calculate next time based on interval
        if self.last_call_time:
            # Apply rate limit backoff from the shared tracker
            adjusted_interval = self.rate_limiter.adjusted_interval_minutes

            next_time = self.last_call_time + timedelta(minutes=adjusted_interval)

            # If next time is in the future, use it
            if next_time > now:
                if self.rate_limiter.backoff_multiplier > 1.0:
                    logger.info(
                        "Next collection delayed by %.1fx due to rate limits (interval: %.0f min)",
                        self.rate_limiter.backoff_multiplier, adjusted_interval,
                    )
                return next_time
        
        # Otherwise, use the scheduled times for today
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Calculate collection times for today (evenly distributed)
        collection_times = []
        for i in range(self.daily_limit):
            collection_time = today_start + timedelta(minutes=self.collection_interval_minutes * i)
            if collection_time > now:
                collection_times.append(collection_time)
        
        # If no more collections today, start fresh tomorrow
        if not collection_times:
            tomorrow_start = today_start + timedelta(days=1)
            return tomorrow_start
        
        return collection_times[0]
    
    def run_forever(self):
        """Run the collector continuously"""
        logger.info("Starting PyVisionic data collector...")
        logger.info("Will collect data %d times per day", self.daily_limit)
        
        # Calculate when we should next collect before starting
        next_collection = self.calculate_next_collection_time()
        now = datetime.now()
        wait_seconds = (next_collection - now).total_seconds()
        
        if self.last_call_time:
            logger.info("Last collection was at %s", self.last_call_time.strftime('%Y-%m-%d %H:%M:%S'))
        
        logger.info("We have collected %d times today", self.calls_today)
        logger.info(
            "Next collection at %s (in %.1f minutes)",
            next_collection.strftime('%Y-%m-%d %H:%M:%S'),
            wait_seconds / 60
        )
        
        # If we need to wait, don't collect immediately
        if wait_seconds > 60:  # If more than 1 minute until next collection
            logger.info("Waiting for next scheduled collection time...")
            time.sleep(wait_seconds)
        
        while True:
            try:
                # Collect data
                self.collect_data()
                
                # Calculate next collection time
                next_collection = self.calculate_next_collection_time()
                wait_seconds = (next_collection - datetime.now()).total_seconds()
                
                logger.info(
                    "Next collection at %s (in %.1f minutes)",
                    next_collection.strftime('%Y-%m-%d %H:%M:%S'),
                    wait_seconds / 60
                )
                
                # Wait until next collection time
                time.sleep(max(60, wait_seconds))  # At least 1 minute wait
                
            except KeyboardInterrupt:
                logger.info("Stopping data collector...")
                break
            except Exception as e:
                logger.error("Unexpected error: %s", e)
                time.sleep(300)  # Wait 5 minutes on error
    
    def run_once(self):
        """Run a single collection (for testing)"""
        return self.collect_data()

if __name__ == "__main__":
    collector = DataCollector()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # Run once for testing
        Success = collector.run_once()
        sys.exit(0 if Success else 1)
    else:
        # Run continuously
        collector.run_forever()