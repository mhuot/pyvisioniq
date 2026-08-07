#!/usr/bin/env python3
"""
Import parsed charging receipts into charging_sessions.csv.

Input is a JSON array of normalized receipt records, typically produced by an
agent parsing charging-network receipt emails:

    [{"network": "EV Connect", "location": "GM - Dahl Chevrolet (Winona MN)",
      "start": "2026-07-24 12:19:43", "end": "2026-07-24 12:27:41",
      "kwh": 6.38, "max_kw": 49.6, "cost_usd": 3.43, "external_id": "q3swp1kq"}]

Receipts overlapping a tracked session correct it in place and take over its
identity as rc_<external_id>; the rest insert as new complete sessions.
Existing rc_* ids are skipped, so re-imports are idempotent.

Usage:
    python tools/import_receipts.py receipts.json            # dry run
    python tools/import_receipts.py receipts.json --write    # apply
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SESSIONS_CSV = Path("data/charging_sessions.csv")
USABLE_KWH = 74.0
DCFC_EFFICIENCY = 0.93
MATCH_PAD = pd.Timedelta(minutes=15)


def sanitize_id(value):
    """Make an external id safe for use in a session_id."""
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value))[:40]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts_json", help="Path to the JSON array of receipt records")
    parser.add_argument("--write", action="store_true", help="Apply changes (default: dry run)")
    args = parser.parse_args()

    if not SESSIONS_CSV.exists():
        print(f"{SESSIONS_CSV} not found; run from the project root")
        return 1

    with open(args.receipts_json, "r", encoding="utf-8") as handle:
        receipts = json.load(handle)

    sessions = pd.read_csv(SESSIONS_CSV)
    sessions["_start"] = pd.to_datetime(sessions["start_time"], format="mixed", errors="coerce")
    sessions["_end"] = pd.to_datetime(sessions["end_time"], format="mixed", errors="coerce")

    updated, inserted, skipped = 0, 0, 0
    new_rows = []
    claimed = set()
    for receipt in receipts:
        rc_id = f"rc_{sanitize_id(receipt['external_id'])}"
        if (sessions["session_id"] == rc_id).any():
            skipped += 1
            continue

        start = pd.to_datetime(receipt["start"])
        end = pd.to_datetime(receipt["end"]) if receipt.get("end") else None
        if end is None and receipt.get("duration_minutes"):
            end = start + pd.Timedelta(minutes=float(receipt["duration_minutes"]))
        if end is None:
            print(f"skipping {rc_id}: no end time or duration")
            skipped += 1
            continue

        kwh = float(receipt["kwh"])
        duration_minutes = round((end - start).total_seconds() / 60, 1)
        values = {
            "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_minutes": duration_minutes,
            "energy_added": round(kwh, 2),
            "avg_power": (round(kwh / (duration_minutes / 60), 2) if duration_minutes else None),
            "max_power": receipt.get("max_kw"),
            "is_complete": True,
            "network": receipt.get("network", ""),
            "location_name": receipt.get("location", ""),
            "cost_usd": receipt.get("cost_usd"),
        }

        overlap = sessions.index[
            (sessions["_start"] <= end + MATCH_PAD)
            & (sessions["_end"] >= start - MATCH_PAD)
            & (~sessions["session_id"].astype(str).str.match(r"^(ea|rc)_"))
            & (~sessions.index.isin(claimed))
        ]
        if len(overlap) > 0:
            idx = overlap[0]
            claimed.add(idx)
            sessions.loc[idx, "session_id"] = rc_id
            for key, val in values.items():
                sessions.loc[idx, key] = val
            updated += 1
            print(
                f"corrected -> {rc_id}: {values['start_time']}  {kwh} kWh  {receipt.get('network')}"
            )
        else:
            new_rows.append(
                {
                    "session_id": rc_id,
                    "start_battery": None,
                    "end_battery": None,
                    "location_lat": None,
                    "location_lon": None,
                    **values,
                }
            )
            inserted += 1
            print(
                f"inserted  -> {rc_id}: {values['start_time']}  {kwh} kWh  {receipt.get('network')}"
            )

    print(f"\ncorrected: {updated}, inserted: {inserted}, skipped: {skipped}")
    if not args.write:
        print("Dry run - re-run with --write to apply")
        return 0
    if not (updated or inserted):
        print("Nothing to write")
        return 0

    sessions = sessions.drop(columns=["_start", "_end"])
    if new_rows:
        sessions = pd.concat([sessions, pd.DataFrame(new_rows)], ignore_index=True)
    sessions["_sort"] = pd.to_datetime(sessions["start_time"], format="mixed", errors="coerce")
    sessions = sessions.sort_values("_sort").drop(columns=["_sort"])

    backup = SESSIONS_CSV.with_suffix(f".csv.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    SESSIONS_CSV.rename(backup)
    sessions.to_csv(SESSIONS_CSV, index=False)
    print(f"Wrote {SESSIONS_CSV} (backup at {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
