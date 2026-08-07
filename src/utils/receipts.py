"""Upsert normalized charging-network receipts into charging_sessions.csv.

Receipt records are parsed from network receipt emails (by an agent or the
import tool) into a normalized form:

    {"network": "EV Connect", "location": "GM - Dahl Chevrolet (Winona MN)",
     "start": "2026-07-24 12:19:43", "end": "2026-07-24 12:27:41",
     "kwh": 6.38, "max_kw": 49.6, "cost_usd": 3.43, "external_id": "q3swp1kq"}

Receipts overlapping a tracked session correct it in place and take over its
identity as rc_<external_id>; the rest insert as new complete sessions.
Existing rc_* ids are skipped, making repeated imports idempotent.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

MATCH_PAD = pd.Timedelta(minutes=15)


def sanitize_id(value):
    """Make an external id safe for use in a session_id."""
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value))[:40]


def upsert_receipts(receipts, sessions_path, write=True, make_backup=True):
    """Apply receipt records to the sessions CSV.

    Returns (updated, inserted, skipped, messages) where messages is a list of
    human-readable lines describing each action.
    """
    sessions_path = Path(sessions_path)
    if not sessions_path.exists():
        return 0, 0, 0, [f"{sessions_path} not found"]

    sessions = pd.read_csv(sessions_path)
    sessions["_start"] = pd.to_datetime(sessions["start_time"], format="mixed", errors="coerce")
    sessions["_end"] = pd.to_datetime(sessions["end_time"], format="mixed", errors="coerce")

    updated = inserted = skipped = 0
    messages = []
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
            messages.append(f"skipping {rc_id}: no end time or duration")
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
            messages.append(
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
            messages.append(
                f"inserted  -> {rc_id}: {values['start_time']}  {kwh} kWh  {receipt.get('network')}"
            )

    if write and (updated or inserted):
        sessions = sessions.drop(columns=["_start", "_end"])
        if new_rows:
            sessions = pd.concat([sessions, pd.DataFrame(new_rows)], ignore_index=True)
        sessions["_sort"] = pd.to_datetime(sessions["start_time"], format="mixed", errors="coerce")
        sessions = sessions.sort_values("_sort").drop(columns=["_sort"])
        if make_backup:
            backup = sessions_path.with_suffix(
                f".csv.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            sessions_path.rename(backup)
        sessions.to_csv(sessions_path, index=False)
        logger.info(
            "Receipt import: %d corrected, %d inserted, %d skipped", updated, inserted, skipped
        )

    return updated, inserted, skipped, messages
