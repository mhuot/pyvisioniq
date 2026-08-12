#!/usr/bin/env python3
"""Assess whether the vehicle will reach a target state of charge before a trip.

Models the rolling feasibility of reaching a departure SOC target given the
current battery level, remaining planned driving, and the throughput of a
Level 1 (~1.35 kW) home charger. Emits a status of on_track / tight /
off_track and can publish the result to NATS.

Designed to run several times a day in the lead-up to a trip. Rather than
comparing against fixed daily checkpoints, it recomputes the charging duty
cycle still required, which stays valid whatever time of day it runs.
"""

import argparse
import json
import os
import subprocess  # nosec B404 - used only for the pinned NATS CLI publish
import sys
from datetime import datetime

import pandas as pd

DEFAULT_PLAN = os.path.join(os.path.dirname(__file__), "trip_plan.json")
BATTERY_CSV = "data/battery_status.csv"
PLUG_CSV = "data/plug_power.csv"

# A day realistically offers 13-18 h plugged in (55-75% duty) based on the
# logged home sessions. Beyond that the plan depends on charging essentially
# every hour the car is parked, which is where it starts to fail.
DUTY_ON_TRACK = 0.55
DUTY_TIGHT = 0.75

NATS_BIN = os.environ.get("NATS_BIN", "/usr/local/bin/nats")

# The smart plug reports every 5 minutes; the vehicle is polled every 90-150.
# Whether the car is plugged in is therefore far better answered by the plug,
# and a stale vehicle reading has already produced a "NOT plugged in" warning
# for a car that had been charging for half an hour.
PLUG_FRESH = pd.Timedelta(minutes=15)
PLUG_CHARGING_WATTS = 100.0


def load_plan(path):
    """Read the trip plan JSON describing the departure and planned driving.

    The real plan is gitignored because it describes where someone will be and
    when, so a fresh checkout has only the example alongside it.
    """
    if not os.path.exists(path):
        example = os.path.join(os.path.dirname(path), "trip_plan.example.json")
        hint = f" Copy {example} to {path} and edit it." if os.path.exists(example) else ""
        raise SystemExit(f"No trip plan at {path}.{hint}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def plug_charging_state(repo_root, now):
    """Charging state from the smart plug, or None when it cannot be trusted.

    Returns None if the webhook feed is missing, unreadable or older than
    PLUG_FRESH, so the caller falls back to the vehicle's own flag rather than
    acting on a stale plug reading.
    """
    path = os.path.join(repo_root, PLUG_CSV)
    if not os.path.exists(path):
        return None
    try:
        frame = pd.read_csv(path)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        frame["watts"] = pd.to_numeric(frame["watts"], errors="coerce")
    except (OSError, ValueError, KeyError):
        return None

    frame = frame.dropna(subset=["timestamp", "watts"]).sort_values("timestamp")
    if frame.empty:
        return None

    row = frame.iloc[-1]
    age = now - row["timestamp"].to_pydatetime()
    if age > PLUG_FRESH or age < pd.Timedelta(minutes=-5):
        return None

    return {
        "is_charging": bool(row["watts"] >= PLUG_CHARGING_WATTS),
        "watts": float(row["watts"]),
        "age_minutes": round(age.total_seconds() / 60, 1),
    }


def latest_battery(repo_root):
    """Return (timestamp, soc_percent, is_charging) from the newest reading."""
    frame = pd.read_csv(os.path.join(repo_root, BATTERY_CSV))
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", format="mixed")
    frame = frame.dropna(subset=["timestamp", "battery_level"]).sort_values("timestamp")
    if frame.empty:
        raise ValueError("no usable rows in battery_status.csv")
    row = frame.iloc[-1]
    return row["timestamp"], float(row["battery_level"]), bool(row["is_charging"])


def energy_consumed_on(repo_root, day, pack_kwh):
    """Energy drawn from the pack on a calendar day, from SOC deltas.

    Derived from the battery readings themselves rather than from trip logs or
    the odometer. Both of those lag: after a round trip the SOC had already
    fallen 52 points while trips.csv still showed only the outbound leg and the
    odometer had not moved for two polls. Prorating planned energy against
    mileage therefore counted a trip as half-pending on top of a battery that
    had already paid for all of it.

    The SOC reading cannot lag itself, so this is accurate the moment the poll
    lands, including partway through a trip.
    """
    frame = pd.read_csv(os.path.join(repo_root, BATTERY_CSV))
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", format="mixed")
    frame = frame.dropna(subset=["timestamp", "battery_level"])
    rows = frame[frame["timestamp"].dt.date == day].sort_values("timestamp")
    if len(rows) < 2:
        return 0.0

    # Sum the drops only. Rises are charging, and netting them off would let an
    # afternoon on the charger mask the morning's driving.
    deltas = rows["battery_level"].astype(float).diff()
    drop_points = -deltas[deltas < 0].sum()
    return float(drop_points) / 100.0 * pack_kwh


def remaining_load(plan, repo_root, now):
    """Sum planned energy still ahead of us, discounting what today already used.

    Energy actually consumed today is charged against today's planned events in
    order, so a trip that has happened stops counting even if the trip log has
    not caught up yet.
    """
    total = 0.0
    detail = []
    consumed = energy_consumed_on(repo_root, now.date(), plan["pack_kwh"])
    budget = consumed

    for event in plan["planned_driving"]:
        day = datetime.strptime(event["date"], "%Y-%m-%d").date()
        if day < now.date():
            continue

        if day == now.date() and budget > 0:
            used = min(budget, event["kwh"])
            budget -= used
            left = event["kwh"] - used
            if left <= 0.05 * event["kwh"]:
                detail.append(f"{event['label']}: done ({consumed:.1f} kWh used today)")
                continue
            total += left
            detail.append(
                f"{event['label']}: {left:.1f} kWh left "
                f"({used:.1f} of {event['kwh']:.1f} kWh used)"
            )
            continue

        total += event["kwh"]
        detail.append(f"{event['label']}: {event['kwh']:.1f} kWh pending")

    return total, detail


def assess(plan, repo_root, now=None):
    """Compute the charging duty cycle still required before departure."""
    stamp, soc, charging = latest_battery(repo_root)
    now = now or datetime.now()

    # Prefer the smart plug for plugged-in state: it is minutes old rather than
    # up to two and a half hours. SOC still comes from the vehicle, which is the
    # only thing that knows it.
    plug = plug_charging_state(repo_root, now)
    charging_source = "vehicle"
    if plug is not None:
        charging = plug["is_charging"]
        charging_source = "smart plug"

    pack = plan["pack_kwh"]
    charger_kw = plan["charger_kw"]
    depart = datetime.strptime(plan["departure"], "%Y-%m-%d %H:%M")
    target_soc = plan["trip_soc_pct"] + plan["arrival_buffer_pct"]

    current_kwh = soc / 100.0 * pack
    target_kwh = target_soc / 100.0 * pack
    pending_kwh, detail = remaining_load(plan, repo_root, now)

    hours_left = (depart - now).total_seconds() / 3600.0
    needed_kwh = target_kwh + pending_kwh - current_kwh
    needed_hours = max(0.0, needed_kwh / charger_kw)
    duty = needed_hours / hours_left if hours_left > 0 else float("inf")

    if duty <= DUTY_ON_TRACK:
        status = "on_track"
    elif duty <= DUTY_TIGHT:
        status = "tight"
    else:
        status = "off_track"

    # How short we would be if charging continued at the realistic 55% duty.
    realistic_kwh = hours_left * DUTY_ON_TRACK * charger_kw
    shortfall_kwh = max(0.0, needed_kwh - realistic_kwh)

    age_hours = (now - stamp.to_pydatetime()).total_seconds() / 3600

    return {
        "status": status,
        "checked_at": now.strftime("%Y-%m-%d %H:%M"),
        "reading_at": str(stamp),
        "reading_age_hours": round(age_hours, 1),
        "stale_reading": age_hours > plan.get("max_reading_age_hours", 3.0),
        "soc_pct": soc,
        "is_charging": charging,
        "charging_source": charging_source,
        "plug_watts": plug["watts"] if plug else None,
        "plug_age_minutes": plug["age_minutes"] if plug else None,
        "hours_to_departure": round(hours_left, 1),
        "pending_drive_kwh": round(pending_kwh, 1),
        "charge_needed_kwh": round(needed_kwh, 1),
        "plugged_hours_needed": round(needed_hours, 1),
        "duty_cycle_pct": round(duty * 100, 0),
        "shortfall_kwh": round(shortfall_kwh, 1),
        "dcfc_minutes_equiv": round(shortfall_kwh / plan["dcfc_kw"] * 60, 0),
        "detail": detail,
    }


def _charging_note(result):
    """Describe charging state, naming the plug when that is what reported it."""
    if not result["is_charging"]:
        return " (NOT plugged in)"
    if result["charging_source"] == "smart plug":
        return f" (charging, {result['plug_watts']:.0f} W at the plug)"
    return " (charging)"


def summarize(result, plan):
    """Render a short human-readable verdict for notification bodies."""
    head = {
        "on_track": "On track",
        "tight": "Tight",
        "off_track": "OFF TRACK",
    }[result["status"]]
    lines = []
    if result["stale_reading"]:
        lines.append(
            f"WARNING: battery reading is {result['reading_age_hours']:.1f} h old "
            "- collector may be down, treat the numbers below as unreliable"
        )
    lines += [
        f"{head} for {plan['trip_name']} - {result['soc_pct']:.0f}% now"
        f"{_charging_note(result)}",
        f"Need {result['charge_needed_kwh']:.1f} kWh in {result['hours_to_departure']:.0f} h "
        f"= {result['plugged_hours_needed']:.0f} h plugged ({result['duty_cycle_pct']:.0f}% duty)",
    ]
    if result["status"] != "on_track":
        lines.append(
            f"Short ~{result['shortfall_kwh']:.1f} kWh at a realistic charge rate "
            f"(~{result['dcfc_minutes_equiv']:.0f} min of DC fast charging)"
        )
    lines.extend(result["detail"])
    return "\n".join(lines)


def publish(subjects, title, body, status, server):
    """Publish a notification to each NATS subject using the local CLI.

    The fleet notifier forwards a subject to humans when it ends in .alert or
    .notify, or matches its NOTIFY_SUBJECTS prefixes. It renders the payload as
    "title\\nmessage", so both keys are set here. Deliberately not published to
    fleet.<host>.cmd: that is a fixed action registry, and an unrecognised
    action is escalated rather than executed.
    """
    payload = json.dumps(
        {
            "title": title,
            "message": body,
            "body": body,
            "type": "warning" if status != "on_track" else "info",
            "source": "trip_readiness_check",
        }
    )
    for subject in subjects:
        subprocess.run(  # nosec B603 - fixed argv, no shell, values are not user input
            [NATS_BIN, "-s", server, "pub", subject, payload],
            check=True,
            capture_output=True,
            timeout=20,
        )


def main():
    """Entry point: assess readiness and optionally notify."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default=DEFAULT_PLAN)
    parser.add_argument("--repo-root", default=os.path.dirname(os.path.dirname(__file__)))
    parser.add_argument(
        "--notify",
        choices=["never", "on-problem", "always"],
        default="never",
        help="when to publish to NATS (default: never, i.e. dry run)",
    )
    parser.add_argument(
        "--subject",
        # One subject only. fleet.digest and any *.notify subject are both
        # forwarded by fleet-notifier to the same Telegram chat, so publishing
        # to both delivers every alert twice rather than reaching two places.
        default="fleet.karwyn.notify",
        help="comma-separated NATS subjects to publish to",
    )
    parser.add_argument("--server", default="nats://localhost:4222")
    parser.add_argument("--json", action="store_true", help="emit raw JSON")
    args = parser.parse_args()

    plan = load_plan(args.plan)
    depart = datetime.strptime(plan["departure"], "%Y-%m-%d %H:%M")
    if datetime.now() > depart:
        print(f"departure ({plan['departure']}) has passed; nothing to check")
        return 0

    result = assess(plan, args.repo_root)
    body = summarize(result, plan)

    print(json.dumps(result, indent=2) if args.json else body)

    should = args.notify == "always" or (
        args.notify == "on-problem" and result["status"] != "on_track"
    )
    if should:
        subjects = [s.strip() for s in args.subject.split(",") if s.strip()]
        publish(
            subjects,
            f"EV trip readiness: {result['status'].replace('_', ' ')}",
            body,
            result["status"],
            args.server,
        )
        print(f"\n[published to {', '.join(subjects)}]", file=sys.stderr)

    return 0 if result["status"] == "on_track" else 1


if __name__ == "__main__":
    sys.exit(main())
