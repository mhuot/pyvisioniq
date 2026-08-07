#!/usr/bin/env python3
"""
Import smart-plug power history into charging session records.

Accepts either a Home Assistant history export (entity_id,state,last_changed
with UTC timestamps) or the webhook-fed data/plug_power.csv (timestamp,watts
in local time). Detects charging spans from wall power, integrates energy,
and corrects or inserts session records with ha_* identities.

Usage:
    python tools/import_plug_history.py ~/history.csv            # dry run
    python tools/import_plug_history.py ~/history.csv --write    # apply
    python tools/import_plug_history.py --plug-log --write       # refine from data/plug_power.csv
"""

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.utils.plug_sessions import (
    detect_charging_spans,
    load_ha_export,
    load_plug_log,
    refine_sessions,
)

SESSIONS_CSV = Path("data/charging_sessions.csv")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ha_csv", nargs="?", help="Path to a Home Assistant history export")
    parser.add_argument(
        "--plug-log", action="store_true", help="Use the webhook-fed data/plug_power.csv instead"
    )
    parser.add_argument("--write", action="store_true", help="Apply changes (default: dry run)")
    args = parser.parse_args()

    if args.plug_log:
        samples = load_plug_log()
    elif args.ha_csv:
        samples = load_ha_export(args.ha_csv)
    else:
        parser.error("provide a Home Assistant export path or --plug-log")

    print(f"samples: {len(samples)}")
    if samples.empty:
        return 0
    print(f"range: {samples['timestamp'].min()} -> {samples['timestamp'].max()}")

    spans = detect_charging_spans(samples)
    print(f"charging spans detected: {len(spans)}")
    for span in spans:
        flag = " (ongoing)" if span["ongoing"] else ""
        print(
            f"  {span['start']} -> {span['end']}  {span['kwh']} kWh"
            f"  avg {span['avg_kw']} kW  max {span['max_kw']} kW{flag}"
        )

    updated, inserted = refine_sessions(spans, SESSIONS_CSV, write=args.write)
    if args.write:
        print(f"\nApplied: {updated} sessions corrected, {inserted} inserted")
    else:
        print(f"\nWould correct {updated} and insert {inserted} - re-run with --write to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
