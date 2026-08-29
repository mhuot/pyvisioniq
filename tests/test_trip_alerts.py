"""Tests for collector-driven trip alerting.

The cron checker this replaces ran four times a day and announced every
off-track run. The collector runs as often as every 15 minutes, so repeating a
standing condition would turn a useful warning into noise the user learns to
ignore. Only transitions are announced, plus one reminder a day.
"""

from datetime import datetime, timedelta

import pytest

import data_collector as collector_module
from src.storage.plan_store import PlanStore
from src.utils.trip_planner import assess_trip

PLAN = {
    "trip_name": "Test trip",
    "departure": "2099-01-02 07:00",
    "trip_soc_pct": 60,
}


@pytest.fixture(name="collector")
def _collector(tmp_path, monkeypatch):
    """A collector with only the pieces alerting touches."""
    instance = collector_module.DataCollector.__new__(collector_module.DataCollector)
    instance.plan_store = PlanStore(str(tmp_path / "data"))
    monkeypatch.setattr(collector_module, "TRIP_ALERTS_ENABLED", True)
    return instance


@pytest.fixture(name="sent")
def _sent(monkeypatch):
    """Capture what would have been published."""
    captured = []

    def fake_publish(subjects, title, body, status, server=None):
        captured.append({"title": title, "body": body, "status": status})
        return True

    monkeypatch.setattr(collector_module, "publish", fake_publish)
    return captured


def _result(status):
    """An assessment with the requested status, built from real arithmetic."""
    # Duty cycle drives the verdict; vary the charger to land on each status.
    charger_kw = {"on_track": 20.0, "tight": 1.35, "off_track": 0.4}[status]
    result = assess_trip(
        now=datetime(2026, 8, 11, 8, 0),
        departure=datetime(2026, 8, 13, 7, 0),
        soc_pct=30,
        pack_kwh=74.0,
        charger_kw=charger_kw,
        trip_kwh=40.0,
        arrival_buffer_pct=10,
        daily_kwh=8.0,
        is_charging=False,
        charging_source="vehicle",
        reading_at=datetime(2026, 8, 11, 7, 30),
    )
    assert result["status"] == status, f"fixture drifted: got {result['status']}"
    return result


def test_a_standing_problem_is_announced_once(collector, sent):
    """Ten cycles of the same off-track status must not be ten alerts."""
    plan = collector.plan_store.save(PLAN)
    for _ in range(10):
        plan = collector.plan_store.get(plan["id"])
        collector._notify_on_change(plan, _result("off_track"))

    assert len(sent) == 1
    assert sent[0]["status"] == "off_track"


def test_each_transition_is_announced(collector, sent):
    plan = collector.plan_store.save(PLAN)
    for status in ("off_track", "off_track", "tight", "tight", "on_track"):
        plan = collector.plan_store.get(plan["id"])
        collector._notify_on_change(plan, _result(status))

    # Three alerts: into off_track, down to tight, and the recovery.
    assert [entry["status"] for entry in sent] == ["off_track", "tight", "on_track"]


def test_first_sight_of_a_healthy_trip_is_silent(collector, sent):
    """Nothing has gone wrong, so there is nothing to say."""
    plan = collector.plan_store.save(PLAN)
    collector._notify_on_change(plan, _result("on_track"))

    assert sent == []
    # But the status is recorded, so a later slip still reads as a change.
    assert collector.plan_store.get(plan["id"])["last_status"] == "on_track"
    collector._notify_on_change(collector.plan_store.get(plan["id"]), _result("off_track"))
    assert len(sent) == 1


def test_a_lasting_problem_is_repeated_once_a_day(collector, sent):
    """Silence forever would let an off-track trip be forgotten."""
    plan = collector.plan_store.save(PLAN)
    collector._notify_on_change(plan, _result("off_track"))
    assert len(sent) == 1

    # Still off track an hour later: no second alert.
    collector._notify_on_change(collector.plan_store.get(plan["id"]), _result("off_track"))
    assert len(sent) == 1

    # Backdate the last notification beyond the reminder window.
    stale = (datetime.now() - timedelta(hours=25)).isoformat(timespec="seconds")
    collector.plan_store.record_status(plan["id"], "off_track")
    plans = collector.plan_store._read()
    plans[0]["last_notified_at"] = stale
    collector.plan_store._write(plans)

    collector._notify_on_change(collector.plan_store.get(plan["id"]), _result("off_track"))
    assert len(sent) == 2


def test_a_collector_that_cannot_alert_leaves_the_status_alone(collector, sent, monkeypatch):
    """Not the alerting owner means not touching the record either.

    In the default Docker deployment the collector cannot reach NATS -- the
    server binds loopback on the host and the CLI is not in the image. If it
    recorded the status anyway, the host-side watcher would see no transition
    and the user would never hear about an off-track trip.
    """
    monkeypatch.setattr(collector_module, "TRIP_ALERTS_ENABLED", False)
    plan = collector.plan_store.save(PLAN)
    collector._notify_on_change(plan, _result("off_track"))

    assert sent == []
    assert collector.plan_store.get(plan["id"])["last_status"] is None


def test_a_failed_publish_is_retried_rather_than_swallowed(collector, monkeypatch):
    """A status is only recorded once its announcement actually went out."""
    attempts = []

    def failing_publish(subjects, title, body, status, server=None):
        attempts.append(status)
        return False

    monkeypatch.setattr(collector_module, "publish", failing_publish)
    plan = collector.plan_store.save(PLAN)

    collector._notify_on_change(plan, _result("off_track"))
    assert collector.plan_store.get(plan["id"])["last_status"] is None

    # Still pending, so the next pass tries again instead of going quiet.
    collector._notify_on_change(collector.plan_store.get(plan["id"]), _result("off_track"))
    assert attempts == ["off_track", "off_track"]
