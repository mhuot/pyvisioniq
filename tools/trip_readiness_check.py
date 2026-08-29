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
from src.utils.trip_planner import (  # noqa: E402  pylint: disable=wrong-import-position
    assess_plan,
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
    body = summarize_assessment(result, plan)

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
