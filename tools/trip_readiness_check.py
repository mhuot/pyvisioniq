import argparse
import json
import os
import subprocess  # nosec B404 - used only for the pinned NATS CLI publish
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The planning logic lives in the web app's shared core so the CLI, the
# collector and the dashboard cannot drift into three different answers.
from src.utils.trip_planner import (  # noqa: E402  pylint: disable=wrong-import-position
    assess_plan,
)

DEFAULT_PLAN = os.path.join(os.path.dirname(__file__), "trip_plan.json")

NATS_BIN = os.environ.get("NATS_BIN", "/usr/local/bin/nats")


def load_plan(path):
    """Read the trip plan JSON describing the departure and planned driving.

    The real plan is gitignored because it describes where someone will be and
    when, so a fresh checkout has only the example alongside it.
    """
    if not os.path.exists(path):
        example = os.path.join(os.path.dirname(path), "trip_plan.example.json")
        hint = f" Copy {example} to {path} and edit it." if os.path.exists(example) else ""
        raise FileNotFoundError(f"No trip plan at {path}.{hint}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def assess(plan, repo_root, now=None):
    """Assess a plan against a repository checkout's data directory."""
    return assess_plan(plan, os.path.join(repo_root, "data"), now)


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
            f"(~{result['dcfc_minutes']:.0f} min of DC fast charging)"
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

    try:
        plan = load_plan(args.plan)
    except FileNotFoundError as missing:
        print(missing, file=sys.stderr)
        return 2
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
