"""Persist named trip plans, so a plan outlives the process that created it.

The readiness checker originally read a single hand-edited tools/trip_plan.json.
That works for one trip maintained by hand, but the dashboard needs several
named plans it can create and retire, and the collector needs somewhere to
record what it last told anyone so it does not repeat itself every cycle.

Plans live in data/, which is gitignored in full. That is deliberate rather
than incidental: a plan names where someone will be and when, and this is a
public repository. Nothing here may be written anywhere tracked.
"""

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

PLANS_FILE = "trip_plans.json"
LOCK_FILE = ".plans.lock"

# Fields a plan must carry, with the defaults applied when a caller omits them.
# charger_kw is energy reaching the pack, not the figure the plug reports; on
# 120V L1 those differ by about 30% and planning on the wall figure once
# produced a "no fast charge needed" verdict for a trip that needed one.
DEFAULTS = {
    "pack_kwh": 74.0,
    "charger_kw": 0.95,
    "dcfc_kw": 100.0,
    "arrival_buffer_pct": 10.0,
    "charge_limit_pct": 100.0,
    "max_reading_age_hours": 3.0,
    "planned_driving": [],
}

REQUIRED = ("trip_name", "departure", "trip_soc_pct")


class PlanValidationError(ValueError):
    """A submitted plan is missing something or carries an unusable value."""


def validate_plan(payload):
    """Return a normalised plan dict, or raise PlanValidationError.

    Submitted plans come from the web, so every field is checked here rather
    than trusted downstream where a bad value would surface as an arithmetic
    error inside the planner.
    """
    if not isinstance(payload, dict):
        raise PlanValidationError("plan must be an object")

    missing = [field for field in REQUIRED if not payload.get(field)]
    if missing:
        raise PlanValidationError(f"missing required field(s): {', '.join(missing)}")

    plan = {**DEFAULTS, **{k: v for k, v in payload.items() if v is not None}}

    try:
        datetime.strptime(plan["departure"], "%Y-%m-%d %H:%M")
    except (TypeError, ValueError) as error:
        raise PlanValidationError(
            f"departure must look like 'YYYY-MM-DD HH:MM': {plan['departure']!r}"
        ) from error

    for field in ("pack_kwh", "charger_kw", "dcfc_kw", "trip_soc_pct"):
        try:
            plan[field] = float(plan[field])
        except (TypeError, ValueError) as error:
            raise PlanValidationError(f"{field} must be a number") from error
        if plan[field] <= 0:
            raise PlanValidationError(f"{field} must be greater than zero")

    for field in ("arrival_buffer_pct", "charge_limit_pct", "max_reading_age_hours"):
        try:
            plan[field] = float(plan[field])
        except (TypeError, ValueError) as error:
            raise PlanValidationError(f"{field} must be a number") from error
        if plan[field] < 0:
            raise PlanValidationError(f"{field} must not be negative")

    if not 0 < plan["charge_limit_pct"] <= 100:
        raise PlanValidationError("charge_limit_pct must be between 1 and 100")

    plan["planned_driving"] = _validate_driving(plan["planned_driving"])
    return plan


def _validate_driving(events):
    """Check the itinerary: each event needs a parseable date and its energy."""
    if not isinstance(events, list):
        raise PlanValidationError("planned_driving must be a list")

    cleaned = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise PlanValidationError(f"planned_driving[{index}] must be an object")
        try:
            datetime.strptime(event["date"], "%Y-%m-%d")
        except (KeyError, TypeError, ValueError) as error:
            raise PlanValidationError(
                f"planned_driving[{index}].date must look like 'YYYY-MM-DD'"
            ) from error
        try:
            kwh = float(event["kwh"])
        except (KeyError, TypeError, ValueError) as error:
            raise PlanValidationError(f"planned_driving[{index}].kwh must be a number") from error
        if kwh < 0:
            raise PlanValidationError(f"planned_driving[{index}].kwh must not be negative")

        entry = {"date": event["date"], "kwh": kwh, "label": event.get("label") or event["date"]}
        if event.get("miles") is not None:
            entry["miles"] = float(event["miles"])
        cleaned.append(entry)

    return sorted(cleaned, key=lambda e: e["date"])


class PlanStore:
    """Read and write the plan file, serialising writes across processes."""

    def __init__(self, data_dir="data"):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / PLANS_FILE

    @contextmanager
    def _write_lock(self):
        """Serialise writes the way CSVStorage does, for the same reason.

        The collector records status transitions while web workers create and
        delete plans. Both are read-modify-write over one file, so without a
        lock a concurrent pair silently loses one of the two changes.
        """
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.data_dir / LOCK_FILE, "w", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self):
        """Load the plan list, tolerating an absent or corrupt file."""
        if not self.path.exists():
            return []
        try:
            with open(self.path, encoding="utf-8") as handle:
                plans = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []
        return plans if isinstance(plans, list) else []

    def _write(self, plans):
        """Replace the plan file atomically, so a crash cannot truncate it."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(plans, handle, indent=2)
        os.replace(temp, self.path)

    def list_plans(self):
        """Every stored plan, soonest departure first."""
        return sorted(self._read(), key=lambda plan: plan.get("departure", ""))

    def get(self, plan_id):
        """One plan by id, or None."""
        return next((plan for plan in self._read() if plan.get("id") == plan_id), None)

    def upcoming(self, now=None):
        """Plans whose departure is still ahead, soonest first."""
        now = now or datetime.now()
        ahead = []
        for plan in self.list_plans():
            try:
                if datetime.strptime(plan["departure"], "%Y-%m-%d %H:%M") > now:
                    ahead.append(plan)
            except (KeyError, ValueError):
                continue
        return ahead

    def save(self, payload):
        """Create or update a plan, returning what was stored."""
        plan = validate_plan(payload)
        with self._write_lock():
            plans = self._read()
            existing = next((p for p in plans if p.get("id") == payload.get("id")), None)
            if existing:
                # Preserve the alerting state across an edit: changing the
                # departure time should not replay a notification already sent.
                plan = {**existing, **plan, "id": existing["id"]}
                plans = [plan if p.get("id") == existing["id"] else p for p in plans]
            else:
                plan["id"] = uuid.uuid4().hex[:12]
                plan["created_at"] = datetime.now().isoformat(timespec="seconds")
                plan.setdefault("last_status", None)
                plan.setdefault("last_notified_at", None)
                plans.append(plan)
            self._write(plans)
        return plan

    def delete(self, plan_id):
        """Remove a plan. Returns whether it was there."""
        with self._write_lock():
            plans = self._read()
            remaining = [plan for plan in plans if plan.get("id") != plan_id]
            if len(remaining) == len(plans):
                return False
            self._write(remaining)
        return True

    def record_status(self, plan_id, status, notified=False, now=None):
        """Note the status a plan was last seen in, and whether we announced it.

        Kept beside the plan rather than in memory so alerting survives a
        collector restart: on a fresh process an in-memory record would look
        like a first sighting and re-announce a status the user already has.
        """
        now = now or datetime.now()
        with self._write_lock():
            plans = self._read()
            for plan in plans:
                if plan.get("id") != plan_id:
                    continue
                plan["last_status"] = status
                if notified:
                    plan["last_notified_at"] = now.isoformat(timespec="seconds")
                self._write(plans)
                return plan
        return None

    def import_legacy(self, legacy_path):
        """Adopt the hand-edited single-plan file, once.

        Returns the imported plan, or None when there was nothing to import or
        a plan of that name is already stored.
        """
        legacy = Path(legacy_path)
        if not legacy.exists() or self._read():
            return None
        try:
            with open(legacy, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        payload.pop("_notes", None)
        try:
            return self.save(payload)
        except PlanValidationError:
            return None
