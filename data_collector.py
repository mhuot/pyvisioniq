#!/usr/bin/env python3
"""
Data collector for PyVisionic
Collects vehicle data 30 times per day (every 48 minutes)
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.api.client import CachedVehicleClient
from src.storage.csv_store import CSVStorage
from src.utils.ha_mqtt import (
    HAPublisher,
    device_from_vehicle,
    location_from_vehicle,
    values_from_vehicle,
)
from src.utils.plug_sessions import detect_charging_spans, load_plug_log, refine_sessions

sys.path.append(str(Path(__file__).parent))

# Load environment first
load_dotenv()

# Import debug utilities
sys.path.append(str(Path(__file__).parent))
from src.utils.debug import DebugLogger, setup_debug_logging

# Set up logging based on debug mode
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
log_level = setup_debug_logging(DEBUG_MODE)

logger = logging.getLogger(__name__)
debug_logger = DebugLogger(__name__)

if DEBUG_MODE:
    logger.info("Running in DEBUG mode - verbose logging enabled")

# Adaptive polling: spend the daily call budget where the action is.
# Modeled against 14 months of history this averages ~22 calls/day vs 24
# for fixed-hourly while resolving charge/trip boundaries much tighter.
ADAPTIVE_INTERVALS_MINUTES = {
    "dcfc": 15,  # DC fast charge in progress
    "ac_charge_start": 20,  # first minutes of an AC charge (pins the start time)
    "ac_charge_steady": 90,  # steady L1/L2 (SOC moves ~2%/hr, nothing to see)
    "post_trip": 20,  # just parked - a DC fast charge may be starting
    "idle_day": 60,
    "idle_night": 150,
}
DCFC_POWER_THRESHOLD_KW = 20
AC_CHARGE_START_WINDOW_MINUTES = 45
POST_TRIP_WINDOW_MINUTES = 30
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6
MINIMUM_INTERVAL_MINUTES = 10


def adaptive_interval_minutes(is_charging, charging_power, charging_since, last_trip_end, now):
    """Pick the next polling interval from the last observed vehicle state.

    Returns (interval_minutes, reason) so scheduling decisions can be reported.
    """
    if is_charging:
        power = charging_power or 0
        if power >= DCFC_POWER_THRESHOLD_KW:
            return ADAPTIVE_INTERVALS_MINUTES["dcfc"], "dcfc"
        elapsed_minutes = (now - charging_since).total_seconds() / 60 if charging_since else 0
        if elapsed_minutes < AC_CHARGE_START_WINDOW_MINUTES:
            return ADAPTIVE_INTERVALS_MINUTES["ac_charge_start"], "ac_charge_start"
        return ADAPTIVE_INTERVALS_MINUTES["ac_charge_steady"], "ac_charge_steady"

    if last_trip_end is not None:
        minutes_since_trip = (now - last_trip_end).total_seconds() / 60
        if 0 <= minutes_since_trip <= POST_TRIP_WINDOW_MINUTES:
            return ADAPTIVE_INTERVALS_MINUTES["post_trip"], "post_trip"

    if now.hour >= NIGHT_START_HOUR or now.hour < NIGHT_END_HOUR:
        return ADAPTIVE_INTERVALS_MINUTES["idle_night"], "idle_night"
    return ADAPTIVE_INTERVALS_MINUTES["idle_day"], "idle_day"


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
        self.daily_limit = int(os.getenv("API_DAILY_LIMIT", "30"))  # Default to 30 calls per day
        if self.daily_limit <= 0:
            logger.error("Invalid daily limit: %d", self.daily_limit)
            raise ValueError("Daily limit must be a positive integer")
        self.collection_interval_minutes = (
            24 * 60
        ) // self.daily_limit  # Minutes between collections
        self.calls_today = 0
        self.last_reset = datetime.now().date()
        self.last_call_time = None
        self._rate_limit_backoff = 1.0

        # Adaptive polling state
        self.adaptive_enabled = os.getenv("ADAPTIVE_POLLING", "false").lower() == "true"
        # Home Assistant bridge: publish each poll's state over MQTT so HA
        # never needs its own Bluelink integration against the same budget.
        self.ha_publisher = HAPublisher()
        self.last_battery_state = {}
        self.charging_since = None
        self.last_trip_end = None
        if self.adaptive_enabled:
            logger.info("Adaptive polling enabled")

        # Load call history
        self.call_history_file = Path("data/api_call_history.json")
        self.polling_log_file = Path("data/polling_log.csv")
        self.load_call_history()

    def load_call_history(self):
        """Load API call history from file"""
        if self.call_history_file.exists():
            try:
                with open(self.call_history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
                    last_reset_str = history.get("last_reset", str(datetime.now().date()))
                    self.last_reset = datetime.fromisoformat(last_reset_str).date()
                    self.calls_today = history.get("calls_today", 0)

                    # Load last call time
                    last_call_str = history.get("last_call")
                    if last_call_str:
                        self.last_call_time = datetime.fromisoformat(last_call_str)

                    # Restore adaptive polling state
                    charging_since_str = history.get("charging_since")
                    if charging_since_str:
                        self.charging_since = datetime.fromisoformat(charging_since_str)

                    # Reset if it's a new day
                    if self.last_reset < datetime.now().date():
                        self.reset_daily_counter()
            except (IOError, json.JSONDecodeError) as e:
                logger.error("Error loading call history: %s", e)
                self.reset_daily_counter()

    def save_call_history(self):
        """Save API call history to file"""
        try:
            self.call_history_file.parent.mkdir(exist_ok=True)
            with open(self.call_history_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "last_reset": str(self.last_reset),
                        "calls_today": self.calls_today,
                        "last_call": datetime.now().isoformat(),
                        "charging_since": (
                            self.charging_since.isoformat() if self.charging_since else None
                        ),
                    },
                    f,
                    indent=2,
                )
        except (IOError, json.JSONDecodeError) as e:
            logger.error("Error saving call history: %s", e)

    def reset_daily_counter(self):
        """Reset the daily API call counter"""
        self.calls_today = 0
        self.last_reset = datetime.now().date()
        self.last_call_time = None
        self._rate_limit_backoff = 1.0  # Reset backoff on daily reset
        self.save_call_history()
        logger.info("Daily API call counter reset")

    def _extend_next_collection_interval(self):
        """Extend the next collection interval due to rate limiting"""
        if hasattr(self, "_rate_limit_backoff"):
            self._rate_limit_backoff = min(self._rate_limit_backoff * 1.5, 4.0)  # Cap at 4x
        else:
            self._rate_limit_backoff = 1.5  # Start with 50% longer interval

        logger.info(
            f"Extended collection interval by {self._rate_limit_backoff:.1f}x due to rate limits"
        )

    def can_make_api_call(self):
        """Check if we can make an API call without exceeding limits"""
        # Check if it's a new day
        if self.last_reset < datetime.now().date():
            self.reset_daily_counter()

        return self.calls_today < self.daily_limit

    def _publish_to_ha(self, data):
        """Push raw state plus derived metrics to Home Assistant, best-effort.

        Derived values are what justify bridging instead of running kia_uvo:
        measured charging efficiency, the season-matched range estimate, and
        the trip-readiness verdict, none of which the car reports.
        """
        if not self.ha_publisher.enabled:
            return
        try:
            values = values_from_vehicle(data, forces_today=self.client._forces_today())
        except Exception:  # pylint: disable=broad-except
            values = values_from_vehicle(data)
        attributes = {}
        try:
            values.update(self._derived_metrics(attributes))
        except Exception as derived_error:  # pylint: disable=broad-except
            logger.debug("Derived metrics unavailable: %s", derived_error)
        state, location_attrs = location_from_vehicle(data)
        if state:
            values["location"] = state
            attributes["location"] = location_attrs
        self.ha_publisher.publish(values, attributes, device_from_vehicle(data))

    def _derived_metrics(self, attributes):
        """Compute the analytics-side sensors; every piece is optional."""
        import pandas as pd  # pylint: disable=import-outside-toplevel

        derived = {}
        pack_kwh = float(os.getenv("BATTERY_USABLE_KWH", "74.0"))

        # Energy-weighted wall-to-battery efficiency over the last 30 days.
        try:
            from src.utils.charge_efficiency import (  # pylint: disable=import-outside-toplevel
                compute_efficiency_points,
                summarize,
            )
            from src.utils.plug_sessions import (  # pylint: disable=import-outside-toplevel
                load_ha_export,
                load_plug_log,
            )

            frames = [load_plug_log()]
            export = Path("data/ha_plug_history.csv")
            if export.exists():
                frames.append(load_ha_export(export))
            ac = pd.concat(frames, ignore_index=True).drop_duplicates("timestamp")
            battery = pd.read_csv("data/battery_status.csv")
            battery["timestamp"] = pd.to_datetime(
                battery["timestamp"], format="mixed", errors="coerce"
            )
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=30)
            points = compute_efficiency_points(
                battery[battery["timestamp"] >= cutoff], ac, pack_kwh
            )
            summary = summarize(points)
            derived["charge_efficiency"] = summary.get("efficiency_pct")
        except Exception as metric_error:  # pylint: disable=broad-except
            logger.debug("charge_efficiency unavailable: %s", metric_error)

        # Season-matched full-charge range from this month's driving.
        try:
            trips = pd.read_csv("data/trips.csv")
            trips["date"] = pd.to_datetime(trips["date"], errors="coerce")
            month = trips[
                (trips["date"].dt.month == datetime.now().month)
                & (trips["distance"] > 0)
                & (trips["total_consumed"] > 0)
            ]
            if len(month) >= 5:
                mi_per_kwh = month["distance"].sum() / (month["total_consumed"].sum() / 1000)
                derived["est_range_full"] = round(pack_kwh * mi_per_kwh)
        except Exception as metric_error:  # pylint: disable=broad-except
            logger.debug("est_range_full unavailable: %s", metric_error)

        # Trip readiness, when a plan exists and its departure is ahead.
        try:
            import sys  # pylint: disable=import-outside-toplevel

            sys.path.insert(0, str(Path(__file__).parent / "tools"))
            from trip_readiness_check import (  # pylint: disable=import-outside-toplevel
                assess,
                load_plan,
            )

            plan = load_plan(str(Path(__file__).parent / "tools" / "trip_plan.json"))
            departure = datetime.strptime(plan["departure"], "%Y-%m-%d %H:%M")
            if departure > datetime.now():
                result = assess(plan, str(Path(__file__).parent))
                derived["trip_readiness"] = result["status"]
                attributes["trip_readiness"] = {
                    "trip": plan.get("trip_name"),
                    "departure": plan.get("departure"),
                    "duty_cycle_pct": result.get("duty_cycle_pct"),
                    "projected_arrival_pct": result.get("shortfall_kwh"),
                    "plugged_hours_needed": result.get("plugged_hours_needed"),
                }
            else:
                derived["trip_readiness"] = "idle"
        except SystemExit:
            derived["trip_readiness"] = "no_plan"
        except Exception as metric_error:  # pylint: disable=broad-except
            logger.debug("trip_readiness unavailable: %s", metric_error)

        return derived

    def _should_force(self):
        """Decide whether this poll should wake the car.

        Cached server fetches are free of wakeups and the car pushes fresh
        data after every drive and charge, so cached is the default. A force
        is requested only when it buys something cached cannot:

        * the previous fetch found the server copy stale, so only the car
          itself has current data;
        * a DC fast charge is in progress, where 15-minute resolution on a
          rapidly changing SOC is the whole point of adaptive polling.

        The client may still demote the request if the 12V is low or the
        daily force budget is spent.
        """
        last = getattr(self, "last_data", None)
        if not last:
            return False
        if last.get("hyundai_data_fresh") is False:
            return True
        battery = last.get("battery") or {}
        if battery.get("is_charging") and (battery.get("charging_power") or 0) >= 20:
            return True
        return False

    def collect_data(self):
        """Collect vehicle data from API"""
        if not self.can_make_api_call():
            logger.warning("Daily API limit reached (%d/%d)", self.calls_today, self.daily_limit)
            return False

        try:
            logger.info("Collecting vehicle data...")

            # Get fresh data and let it cache
            data = self.client.get_vehicle_data(force=self._should_force())

            if data:
                self.last_data = data
                # Store in CSV files
                self.storage.store_vehicle_data(data)

                # Increment call counter and update last call time
                self.calls_today += 1
                self.last_call_time = datetime.now()
                self._update_adaptive_state(data)
                self.save_call_history()
                self._refine_sessions_from_plug()
                self._publish_to_ha(data)

                logger.info(
                    "Data collected successfully (call %d/%d, %s)",
                    self.calls_today,
                    self.daily_limit,
                    data.get("call_class", "unknown"),
                )
                # Temperature from API is in Fahrenheit
                temp_f = data.get("raw_data", {}).get("airTemp", {}).get("value")
                battery_info = data.get("battery", {})

                logger.info(
                    "Battery: %s%%, Range: %skm, Temp: %s°F",
                    battery_info.get("level", "N/A"),
                    battery_info.get("range", "N/A"),
                    temp_f if temp_f else "N/A",
                )
                return True
            else:
                logger.error("Failed to collect data - no data returned")
                return False

        except Exception as e:
            error_msg = str(e).lower()

            # Check for rate limit errors
            if any(
                phrase in error_msg
                for phrase in [
                    "rate limit",
                    "too many requests",
                    "quota exceeded",
                    "throttled",
                    "429",
                    "limit exceeded",
                ]
            ):
                logger.warning("Rate limit exceeded, will extend next collection interval")
                # Extend the next collection by 50% to avoid further rate limits
                self._extend_next_collection_interval()
                return False
            else:
                logger.error("Error collecting data: %s", e)
                return False

    def _refine_sessions_from_plug(self):
        """Refine charging sessions from webhook-fed smart-plug samples"""
        try:
            samples = load_plug_log()
            if samples.empty:
                return
            spans = detect_charging_spans(samples)
            refine_sessions(
                spans, Path("data/charging_sessions.csv"), write=True, make_backup=False
            )
        except Exception as e:
            logger.error("Plug session refinement failed: %s", e)

    def _update_adaptive_state(self, data):
        """Track charging/trip state used to pick the next adaptive interval"""
        battery = data.get("battery", {}) or {}
        self.last_battery_state = battery

        if battery.get("is_charging"):
            if self.charging_since is None:
                self.charging_since = datetime.now()
        else:
            self.charging_since = None

        trips = data.get("trips") or []
        if trips:
            latest = trips[0]  # client sorts trips newest first
            try:
                trip_start = datetime.fromisoformat(str(latest.get("date")))
                duration_minutes = latest.get("duration") or 0
                self.last_trip_end = trip_start + timedelta(minutes=duration_minutes)
            except (ValueError, TypeError):
                pass

    def _adaptive_interval_with_budget(self, now):
        """Adaptive interval, floored so the remaining daily budget cannot be exceeded.

        Returns (interval_minutes, reason).
        """
        interval, reason = adaptive_interval_minutes(
            self.last_battery_state.get("is_charging", False),
            self.last_battery_state.get("charging_power"),
            self.charging_since,
            self.last_trip_end,
            now,
        )

        # Budget clamp: once the even-spread pace of the remaining calls is worse
        # than hourly, the budget is genuinely tight - ration the rest of the day.
        # Below that threshold the policy may burst freely; can_make_api_call()
        # remains the hard stop either way.
        remaining_calls = self.daily_limit - self.calls_today
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        minutes_left_today = (midnight - now).total_seconds() / 60
        if remaining_calls <= 0:
            return minutes_left_today + 1, "budget_exhausted"  # next call after the daily reset
        budget_floor = minutes_left_today / remaining_calls
        if budget_floor > ADAPTIVE_INTERVALS_MINUTES["idle_day"] and budget_floor > interval:
            return max(budget_floor, MINIMUM_INTERVAL_MINUTES), "budget_clamp"

        return max(interval, MINIMUM_INTERVAL_MINUTES), reason

    def _record_polling_decision(self, reason, interval_minutes, backoff_multiplier):
        """Append a scheduling decision to data/polling_log.csv for reporting"""
        try:
            is_new_file = not self.polling_log_file.exists()
            self.polling_log_file.parent.mkdir(exist_ok=True)
            with open(self.polling_log_file, "a", encoding="utf-8") as f:
                if is_new_file:
                    f.write(
                        "timestamp,mode,reason,interval_minutes,"
                        "calls_today,daily_limit,backoff\n"
                    )
                f.write(
                    f"{datetime.now().isoformat()},"
                    f"{'adaptive' if self.adaptive_enabled else 'fixed'},"
                    f"{reason},{round(interval_minutes, 1)},"
                    f"{self.calls_today},{self.daily_limit},{backoff_multiplier}\n"
                )
        except IOError as e:
            logger.error("Error writing polling log: %s", e)

        logger.info(
            "Next poll in %.0f min (reason=%s, calls %d/%d)",
            interval_minutes,
            reason,
            self.calls_today,
            self.daily_limit,
        )

    def calculate_next_collection_time(self):
        """Calculate optimal collection times based on last call and interval"""
        now = datetime.now()

        # If we have a last call time, calculate next time based on interval
        if self.last_call_time:
            # Apply rate limit backoff if active
            backoff_multiplier = getattr(self, "_rate_limit_backoff", 1.0)
            if self.adaptive_enabled:
                base_interval, reason = self._adaptive_interval_with_budget(now)
            else:
                base_interval, reason = self.collection_interval_minutes, "fixed"
            adjusted_interval = base_interval * backoff_multiplier

            next_time = self.last_call_time + timedelta(minutes=adjusted_interval)

            # If next time is in the future, use it
            if next_time > now:
                if backoff_multiplier > 1.0:
                    logger.info(
                        f"Next collection delayed by {backoff_multiplier:.1f}x due to rate limits"
                    )
                self._record_polling_decision(reason, adjusted_interval, backoff_multiplier)
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
            logger.info(
                "Last collection was at %s",
                self.last_call_time.strftime("%Y-%m-%d %H:%M:%S"),
            )

        logger.info("We have collected %d times today", self.calls_today)
        logger.info(
            "Next collection at %s (in %.1f minutes)",
            next_collection.strftime("%Y-%m-%d %H:%M:%S"),
            wait_seconds / 60,
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
                    next_collection.strftime("%Y-%m-%d %H:%M:%S"),
                    wait_seconds / 60,
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
        success = collector.run_once()
        sys.exit(0 if success else 1)
    else:
        # Run continuously
        collector.run_forever()
