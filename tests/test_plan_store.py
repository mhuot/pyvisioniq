"""Tests for the trip plan store.

Plans are read-modify-written by two kinds of process at once -- the collector
recording status transitions, web workers creating and deleting -- so the lock
is load-bearing rather than decorative, and is exercised here with real
concurrent processes rather than by inspection.
"""

import json
import multiprocessing
from datetime import datetime

import pytest

from src.storage.plan_store import PlanStore, PlanValidationError, validate_plan

VALID = {
    "trip_name": "Test trip",
    "departure": "2026-09-05 07:00",
    "trip_soc_pct": 60,
}


@pytest.fixture(name="store")
def _store(tmp_path):
    return PlanStore(str(tmp_path / "data"))


def test_save_assigns_an_id_and_defaults(store):
    plan = store.save(VALID)
    assert plan["id"]
    assert plan["charger_kw"] == 0.95  # pack-side, not the plug's 1.35
    assert plan["charge_limit_pct"] == 100.0
    assert store.get(plan["id"])["trip_name"] == "Test trip"


def test_editing_preserves_id_and_alert_state(store):
    plan = store.save(VALID)
    store.record_status(plan["id"], "off_track", notified=True)

    edited = store.save({**VALID, "id": plan["id"], "departure": "2026-09-06 07:00"})
    assert edited["id"] == plan["id"]
    assert edited["departure"] == "2026-09-06 07:00"
    # Re-timing a trip must not replay an alert the user already received.
    assert edited["last_status"] == "off_track"
    assert edited["last_notified_at"] is not None


def test_delete_reports_whether_it_existed(store):
    plan = store.save(VALID)
    assert store.delete(plan["id"]) is True
    assert store.delete(plan["id"]) is False
    assert store.list_plans() == []


def test_upcoming_excludes_departed_trips(store):
    store.save({**VALID, "trip_name": "Past", "departure": "2020-01-01 07:00"})
    future = store.save({**VALID, "trip_name": "Future", "departure": "2099-01-01 07:00"})
    assert [p["id"] for p in store.upcoming()] == [future["id"]]


def test_corrupt_file_reads_as_empty_rather_than_raising(store):
    store.save(VALID)
    store.path.write_text("{ this is not json")
    assert store.list_plans() == []


def test_legacy_plan_is_adopted_once(store, tmp_path):
    legacy = tmp_path / "trip_plan.json"
    legacy.write_text(
        json.dumps({**VALID, "trip_name": "Legacy", "_notes": {"charger_kw": "ignore me"}})
    )
    imported = store.import_legacy(str(legacy))
    assert imported["trip_name"] == "Legacy"
    assert "_notes" not in imported
    # Second call must not duplicate it.
    assert store.import_legacy(str(legacy)) is None
    assert len(store.list_plans()) == 1


@pytest.mark.parametrize(
    "payload,message",
    [
        ({}, "missing required field"),
        ({**VALID, "departure": "next tuesday"}, "YYYY-MM-DD HH:MM"),
        ({**VALID, "trip_soc_pct": -5}, "greater than zero"),
        ({**VALID, "charge_limit_pct": 150}, "between 1 and 100"),
        ({**VALID, "planned_driving": "nope"}, "must be a list"),
        ({**VALID, "planned_driving": [{"kwh": 8}]}, "date must look like"),
        ({**VALID, "planned_driving": [{"date": "2026-09-01"}]}, "kwh must be a number"),
    ],
)
def test_invalid_plans_are_rejected_with_a_reason(payload, message):
    with pytest.raises(PlanValidationError, match=message):
        validate_plan(payload)


def test_itinerary_is_sorted_and_labelled():
    plan = validate_plan(
        {
            **VALID,
            "planned_driving": [
                {"date": "2026-09-03", "kwh": 8.0},
                {"date": "2026-09-01", "kwh": 43.0, "label": "Long trip"},
            ],
        }
    )
    assert [e["date"] for e in plan["planned_driving"]] == ["2026-09-01", "2026-09-03"]
    assert plan["planned_driving"][0]["label"] == "Long trip"
    # An unlabelled day falls back to its date rather than rendering as None.
    assert plan["planned_driving"][1]["label"] == "2026-09-03"


def _append_plan(args):
    """Save one plan in a separate process, for the concurrency test."""
    data_dir, index = args
    PlanStore(data_dir).save({**VALID, "trip_name": f"Trip {index}"})


def test_concurrent_writers_do_not_lose_plans(tmp_path):
    """Without the lock, read-modify-write races drop plans silently."""
    data_dir = str(tmp_path / "data")
    PlanStore(data_dir).save({**VALID, "trip_name": "Seed"})

    with multiprocessing.Pool(8) as pool:
        pool.map(_append_plan, [(data_dir, i) for i in range(8)])

    names = {plan["trip_name"] for plan in PlanStore(data_dir).list_plans()}
    assert names == {"Seed"} | {f"Trip {i}" for i in range(8)}


def test_record_status_survives_a_new_store_instance(tmp_path):
    """A collector restart must not re-announce a status already sent."""
    data_dir = str(tmp_path / "data")
    plan = PlanStore(data_dir).save(VALID)
    PlanStore(data_dir).record_status(plan["id"], "tight", notified=True, now=datetime(2026, 9, 1))
    reloaded = PlanStore(data_dir).get(plan["id"])
    assert reloaded["last_status"] == "tight"
    assert reloaded["last_notified_at"] == "2026-09-01T00:00:00"
