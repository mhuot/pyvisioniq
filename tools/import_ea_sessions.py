#!/usr/bin/env python3
"""
One-time import of an Electrify America session log into charging_sessions.csv.

The EA log provides authoritative session data the hourly poller cannot see:
exact start/end times, dispenser-metered energy, real peak power, and end SoC.
Matched sessions (time overlap) are corrected in place; unmatched EA sessions
are inserted as complete sessions with ea_<id> session ids.

Usage:
    python tools/import_ea_sessions.py path/to/ea_log.csv            # dry run
    python tools/import_ea_sessions.py path/to/ea_log.csv --write    # apply
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SESSIONS_CSV = Path("data/charging_sessions.csv")
USABLE_KWH = 74.0
DCFC_EFFICIENCY = 0.93  # rough dispenser-to-battery efficiency for SoC estimates
MATCH_PAD = pd.Timedelta(minutes=10)


def load_ea_log(path):
    """Parse the EA export into normalized session records."""
    ea = pd.read_csv(path)
    ea["start"] = pd.to_datetime(ea["Date"] + " " + ea["Start Time"], format="%Y-%m-%d %I:%M:%S %p")
    ea["end"] = pd.to_datetime(ea["Date"] + " " + ea["End Time"], format="%Y-%m-%d %I:%M:%S %p")
    ea["kwh"] = pd.to_numeric(ea["Energy Delivered (kWh)"], errors="coerce")
    ea["max_kw"] = pd.to_numeric(
        ea["Max Speed"].astype(str).str.replace(" kW", "", regex=False), errors="coerce"
    )
    ea["end_soc"] = pd.to_numeric(
        ea["End SoC"].astype(str).str.replace("%", "", regex=False), errors="coerce"
    )
    ea = ea.dropna(subset=["start", "end", "kwh"])
    ea["ea_session_id"] = (
        pd.to_numeric(ea["Session ID"], errors="coerce").astype("Int64").astype(str)
    )
    return ea.sort_values("start")


def estimate_start_soc(end_soc, kwh):
    """Estimate the starting SoC from delivered energy and pack capacity."""
    if pd.isna(end_soc):
        return None
    gained_pct = (kwh * DCFC_EFFICIENCY / USABLE_KWH) * 100
    return max(0, round(end_soc - gained_pct))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ea_csv", help="Path to the Electrify America session log CSV")
    parser.add_argument("--write", action="store_true", help="Apply changes (default: dry run)")
    args = parser.parse_args()

    if not SESSIONS_CSV.exists():
        print(f"{SESSIONS_CSV} not found; run from the project root")
        return 1

    ea = load_ea_log(args.ea_csv)
    sessions = pd.read_csv(SESSIONS_CSV)
    sessions["_start"] = pd.to_datetime(sessions["start_time"], format="mixed", errors="coerce")
    sessions["_end"] = pd.to_datetime(sessions["end_time"], format="mixed", errors="coerce")

    existing_ea_ids = set(
        sessions["session_id"].astype(str)[sessions["session_id"].astype(str).str.startswith("ea_")]
    )

    updated, inserted, skipped = [], [], []
    backfilled = 0
    claimed_rows = set()
    for ea_row in ea.itertuples():
        ea_id = f"ea_{ea_row.ea_session_id}"
        location_name = str(ea_row.Location) if pd.notna(ea_row.Location) else ""
        if ea_id in existing_ea_ids or (sessions["session_id"] == ea_id).any():
            # Backfill network metadata on rows imported before those columns existed
            idx = sessions.index[sessions["session_id"] == ea_id]
            existing_network = sessions.loc[idx[0]].get("network", "") if len(idx) > 0 else None
            if len(idx) > 0 and (pd.isna(existing_network) or not str(existing_network).strip()):
                sessions.loc[idx[0], "network"] = "Electrify America"
                sessions.loc[idx[0], "location_name"] = location_name
                backfilled += 1
            skipped.append(ea_id)
            continue

        duration_minutes = round((ea_row.end - ea_row.start).total_seconds() / 60, 1)
        avg_power = round(ea_row.kwh / (duration_minutes / 60), 2) if duration_minutes else 0
        start_soc = estimate_start_soc(ea_row.end_soc, ea_row.kwh)

        overlap = sessions[
            (sessions["_start"] <= ea_row.end + MATCH_PAD)
            & (sessions["_end"] >= ea_row.start - MATCH_PAD)
            & (~sessions.index.isin(claimed_rows))
        ]
        if len(overlap) > 0:
            idx = overlap.index[0]
            claimed_rows.add(idx)
            old_start = sessions.loc[idx, "start_time"]
            # Take over the row's identity: ea_* marks the record as carrying
            # authoritative metered values rather than poll-derived estimates
            sessions.loc[idx, "session_id"] = ea_id
            sessions.loc[idx, "start_time"] = ea_row.start.strftime("%Y-%m-%d %H:%M:%S")
            sessions.loc[idx, "end_time"] = ea_row.end.strftime("%Y-%m-%d %H:%M:%S")
            sessions.loc[idx, "duration_minutes"] = duration_minutes
            if start_soc is not None:
                sessions.loc[idx, "start_battery"] = start_soc
            if not pd.isna(ea_row.end_soc):
                sessions.loc[idx, "end_battery"] = ea_row.end_soc
            sessions.loc[idx, "energy_added"] = round(ea_row.kwh, 2)
            sessions.loc[idx, "avg_power"] = avg_power
            sessions.loc[idx, "max_power"] = ea_row.max_kw
            sessions.loc[idx, "is_complete"] = True
            sessions.loc[idx, "network"] = "Electrify America"
            sessions.loc[idx, "location_name"] = location_name
            updated.append(
                (str(sessions.loc[idx, "session_id"]), str(old_start)[:16], str(ea_row.start)[:16])
            )
        else:
            inserted.append(
                {
                    "session_id": ea_id,
                    "start_time": ea_row.start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end_time": ea_row.end.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration_minutes": duration_minutes,
                    "start_battery": start_soc,
                    "end_battery": ea_row.end_soc,
                    "energy_added": round(ea_row.kwh, 2),
                    "avg_power": avg_power,
                    "max_power": ea_row.max_kw,
                    "location_lat": None,
                    "location_lon": None,
                    "is_complete": True,
                    "network": "Electrify America",
                    "location_name": location_name,
                }
            )

    print(f"EA sessions parsed: {len(ea)}")
    print(f"matched and corrected: {len(updated)}")
    for session_id, old, new in updated:
        print(f"  {session_id}: start {old} -> {new}")
    print(f"inserted as new: {len(inserted)}")
    for record in inserted:
        print(
            f"  {record['session_id']}: {record['start_time'][:16]}  {record['energy_added']} kWh  {record['max_power']} kW"
        )
    if skipped:
        print(f"already imported (skipped): {len(skipped)}")
    if backfilled:
        print(f"network metadata backfilled: {backfilled}")

    if not args.write:
        print("\nDry run - re-run with --write to apply")
        return 0
    if not (updated or inserted or backfilled):
        print("\nNothing to write")
        return 0

    sessions = sessions.drop(columns=["_start", "_end"])
    if inserted:
        sessions = pd.concat([sessions, pd.DataFrame(inserted)], ignore_index=True)
    sessions["_sort"] = pd.to_datetime(sessions["start_time"], format="mixed", errors="coerce")
    sessions = sessions.sort_values("_sort").drop(columns=["_sort"])

    backup = SESSIONS_CSV.with_suffix(f".csv.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    SESSIONS_CSV.rename(backup)
    sessions.to_csv(SESSIONS_CSV, index=False)
    print(f"\nWrote {SESSIONS_CSV} (backup at {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
