import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The planning logic lives in the web app's shared core so the CLI, the
# collector and the dashboard cannot drift into three different answers.
from src.utils.notify import (  # noqa: E402  pylint: disable=wrong-import-position
    DEFAULT_SERVER,
    DEFAULT_SUBJECT,
    publish,
)
from src.storage.plan_store import PlanStore  # noqa: E402  pylint: disable=C0413
from src.utils.trip_planner import (  # noqa: E402  pylint: disable=wrong-import-position
    assess_plan,
    notify_on_change,
    summarize_assessment,
)

DEFAULT_PLAN = os.path.join(os.path.dirname(__file__), "trip_plan.json")


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


def watch(repo_root, subjects, server):
    """Assess every stored plan and announce the ones whose status changed.

    This is the alerting owner. The collector re-assesses plans far more often,
    but it runs in a container that cannot reach NATS -- the server binds
    loopback on the host and the CLI is not in the image -- so publishing lives
    here, on the host, where both are available.

    Safe to run often: notify_on_change publishes transitions, not standing
    conditions, and records nothing it failed to send.
    """
    store = PlanStore(os.path.join(repo_root, "data"))
    upcoming = store.upcoming()
    if not upcoming:
        print("no upcoming trips")
        return 0

    def publisher(title, body, status):
        return publish(subjects, title, body, status, server)

    announced = 0
    for plan in upcoming:
        try:
            result = assess_plan(plan, os.path.join(repo_root, "data"))
        except (OSError, ValueError, KeyError) as error:
            print(f"could not assess {plan.get('trip_name')!r}: {error}", file=sys.stderr)
            continue

        sent = notify_on_change(plan, result, store, publisher)
        announced += 1 if sent else 0
        print(f"{plan['trip_name']}: {result['status']}" + (" (announced)" if sent else ""))

    return 0 if announced == 0 else 1


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
        default=DEFAULT_SUBJECT,
        help="comma-separated NATS subjects to publish to",
    )
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--json", action="store_true", help="emit raw JSON")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="assess every stored plan and announce status changes (for cron)",
    )
    args = parser.parse_args()

    subjects = [s.strip() for s in args.subject.split(",") if s.strip()]
    if args.watch:
        return watch(args.repo_root, subjects, args.server)

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
    body = summarize_assessment(result, plan)

    print(json.dumps(result, indent=2) if args.json else body)

    should = args.notify == "always" or (
        args.notify == "on-problem" and result["status"] != "on_track"
    )
    if should:
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
