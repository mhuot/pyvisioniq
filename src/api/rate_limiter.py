"""
Centralized API rate limit tracking for all components (data collector, web app, API client).

Provides:
- Shared daily call counter persisted to disk
- Rate limit event logging with timestamps
- Backoff state that survives process restarts
- Pre-call limit checking to prevent exceeding quotas
- File-level locking for safe multi-process access
"""

import json
import logging
import fcntl
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Singleton instance
_instance = None


def get_rate_limiter(daily_limit=None, data_dir="data"):
    """Get or create the singleton RateLimitTracker instance."""
    global _instance
    if _instance is None:
        _instance = RateLimitTracker(daily_limit=daily_limit, data_dir=data_dir)
    return _instance


class RateLimitTracker:
    """Tracks API call usage and rate limit events across all components.

    State is persisted to data/api_call_history.json so it survives restarts
    and is shared between the data collector process and the Flask web app.
    """

    def __init__(self, daily_limit=None, data_dir="data"):
        import os
        self.daily_limit = daily_limit or int(os.getenv("API_DAILY_LIMIT", "30"))
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        self.history_file = self.data_dir / "api_call_history.json"
        self.rate_limit_log_file = self.data_dir / "rate_limit_events.json"

        # In-memory state (loaded from disk)
        self.calls_today = 0
        self.last_reset = datetime.now().date()
        self.last_call_time = None
        self.backoff_multiplier = 1.0
        self.call_sources = []  # recent call sources for debugging

        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_make_call(self):
        """Check whether an API call is allowed right now.

        Returns True if we are under the daily limit.
        Automatically resets the counter at midnight.
        """
        self._maybe_reset_day()
        return self.calls_today < self.daily_limit

    @property
    def remaining_calls(self):
        """Number of API calls remaining today."""
        self._maybe_reset_day()
        return max(0, self.daily_limit - self.calls_today)

    @property
    def collection_interval_minutes(self):
        """Minutes between collections based on daily limit."""
        return (24 * 60) / self.daily_limit

    @property
    def adjusted_interval_minutes(self):
        """Collection interval adjusted by current backoff multiplier."""
        return self.collection_interval_minutes * self.backoff_multiplier

    def record_call(self, source="unknown"):
        """Record that an API call was made.

        Args:
            source: Identifier for what triggered the call
                    (e.g. 'data_collector', 'web_refresh', 'web_force_update')
        """
        self._maybe_reset_day()
        self.calls_today += 1
        self.last_call_time = datetime.now()

        # Keep last 50 call sources for debugging
        self.call_sources.append({
            "time": self.last_call_time.isoformat(),
            "source": source,
            "call_number": self.calls_today,
        })
        if len(self.call_sources) > 50:
            self.call_sources = self.call_sources[-50:]

        self._save_state()
        logger.info(
            "API call recorded [source=%s] (%d/%d today)",
            source, self.calls_today, self.daily_limit,
        )

    def record_rate_limit_hit(self, source="unknown", error_message=""):
        """Record that a rate limit error was received from the API.

        Extends the backoff multiplier and logs the event.
        """
        self.backoff_multiplier = min(self.backoff_multiplier * 1.5, 4.0)
        self._save_state()

        event = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "error_message": str(error_message)[:500],
            "calls_at_time": self.calls_today,
            "daily_limit": self.daily_limit,
            "backoff_multiplier": self.backoff_multiplier,
        }
        self._append_rate_limit_event(event)
        logger.warning(
            "Rate limit hit [source=%s] calls=%d/%d backoff=%.1fx: %s",
            source, self.calls_today, self.daily_limit,
            self.backoff_multiplier, str(error_message)[:200],
        )

    def get_status(self):
        """Return a dict with current rate limit status for API/dashboard use."""
        self._maybe_reset_day()
        now = datetime.now()

        # Calculate next collection time
        next_collection = None
        if self.last_call_time:
            next_collection = self.last_call_time + timedelta(
                minutes=self.adjusted_interval_minutes
            )
            if next_collection <= now:
                next_collection = now + timedelta(seconds=30)

        # Time until daily reset
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        minutes_until_reset = (tomorrow - now).total_seconds() / 60

        return {
            "calls_today": self.calls_today,
            "daily_limit": self.daily_limit,
            "remaining_calls": self.remaining_calls,
            "last_call": self.last_call_time.isoformat() if self.last_call_time else None,
            "next_collection": next_collection.isoformat() if next_collection else None,
            "collection_interval_minutes": round(self.collection_interval_minutes, 1),
            "backoff_multiplier": self.backoff_multiplier,
            "adjusted_interval_minutes": round(self.adjusted_interval_minutes, 1),
            "minutes_until_reset": round(minutes_until_reset, 1),
            "is_rate_limited": self.backoff_multiplier > 1.0,
            "recent_calls": self.call_sources[-10:],
            "recent_rate_limit_events": self._get_recent_rate_limit_events(5),
        }

    def reset_backoff(self):
        """Manually reset the backoff multiplier (e.g. after successful call)."""
        if self.backoff_multiplier > 1.0:
            logger.info(
                "Backoff reset from %.1fx to 1.0x after successful call",
                self.backoff_multiplier,
            )
            self.backoff_multiplier = 1.0
            self._save_state()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self):
        """Load persisted state from disk with file locking."""
        if not self.history_file.exists():
            return

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)

            last_reset_str = data.get("last_reset", str(datetime.now().date()))
            self.last_reset = datetime.fromisoformat(last_reset_str).date()
            self.calls_today = data.get("calls_today", 0)
            self.backoff_multiplier = data.get("backoff_multiplier", 1.0)
            self.call_sources = data.get("call_sources", [])

            last_call_str = data.get("last_call")
            if last_call_str:
                self.last_call_time = datetime.fromisoformat(last_call_str)

            # Reset if it's a new day
            self._maybe_reset_day()

        except (IOError, json.JSONDecodeError, ValueError) as exc:
            logger.error("Error loading rate limit state: %s", exc)
            self._maybe_reset_day()

    def _save_state(self):
        """Persist current state to disk with file locking."""
        try:
            self.data_dir.mkdir(exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    json.dump(
                        {
                            "last_reset": str(self.last_reset),
                            "calls_today": self.calls_today,
                            "last_call": self.last_call_time.isoformat()
                                if self.last_call_time else None,
                            "backoff_multiplier": self.backoff_multiplier,
                            "call_sources": self.call_sources,
                        },
                        f,
                        indent=2,
                    )
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except IOError as exc:
            logger.error("Error saving rate limit state: %s", exc)

    def _maybe_reset_day(self):
        """Reset counters if the date has rolled over."""
        today = datetime.now().date()
        if self.last_reset < today:
            logger.info(
                "New day detected — resetting API call counter "
                "(previous: %d calls on %s)",
                self.calls_today, self.last_reset,
            )
            self.calls_today = 0
            self.last_reset = today
            self.last_call_time = None
            self.backoff_multiplier = 1.0
            self.call_sources = []
            self._save_state()

    # ------------------------------------------------------------------
    # Rate limit event log
    # ------------------------------------------------------------------

    def _append_rate_limit_event(self, event):
        """Append a rate limit event to the log file."""
        events = self._load_rate_limit_events()
        events.append(event)

        # Keep last 200 events
        if len(events) > 200:
            events = events[-200:]

        try:
            with open(self.rate_limit_log_file, "w", encoding="utf-8") as f:
                json.dump(events, f, indent=2)
        except IOError as exc:
            logger.error("Error writing rate limit event log: %s", exc)

    def _load_rate_limit_events(self):
        """Load rate limit event log from disk."""
        if not self.rate_limit_log_file.exists():
            return []
        try:
            with open(self.rate_limit_log_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            return []

    def _get_recent_rate_limit_events(self, count=5):
        """Get the N most recent rate limit events."""
        events = self._load_rate_limit_events()
        return events[-count:] if events else []
