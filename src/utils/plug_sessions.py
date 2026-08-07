"""Derive and refine charging sessions from smart-plug power samples.

The L1 charger plugs into a Home Assistant-monitored smart plug, which gives
wall-side power over time: exact plug-in/unplug times and metered energy the
hourly vehicle poller cannot see. Sessions derived here take (or are created
with) ha_* identities, which the dashboard treats as authoritative.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

CHARGING_THRESHOLD_WATTS = 100.0
# Old HA exports carry hourly statistics; recent data is minute-level. Allow
# bridging one missing hourly sample without splitting a session.
MAX_GAP_MINUTES = 70.0
MATCH_PAD = pd.Timedelta(minutes=15)
PLUG_LOG_PATH = Path("data/plug_power.csv")


def _local_timezone():
    return ZoneInfo(os.getenv("TZ", "America/Chicago"))


def load_ha_export(path):
    """Parse a Home Assistant history export (entity_id, state, last_changed)."""
    frame = pd.read_csv(path)
    frame = frame[frame["entity_id"].astype(str).str.endswith("_power")]
    samples = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame["last_changed"], utc=True, errors="coerce"),
            "watts": pd.to_numeric(frame["state"], errors="coerce"),
        }
    ).dropna()
    samples["timestamp"] = (
        samples["timestamp"].dt.tz_convert(_local_timezone()).dt.tz_localize(None)
    )
    return samples.sort_values("timestamp").reset_index(drop=True)


def load_plug_log(path=PLUG_LOG_PATH):
    """Parse the webhook-fed plug log (timestamp, watts) in naive local time."""
    if not Path(path).exists():
        return pd.DataFrame(columns=["timestamp", "watts"])
    frame = pd.read_csv(path)
    samples = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame["timestamp"], format="mixed", errors="coerce"),
            "watts": pd.to_numeric(frame["watts"], errors="coerce"),
        }
    ).dropna()
    return samples.sort_values("timestamp").reset_index(drop=True)


def detect_charging_spans(samples, threshold_watts=CHARGING_THRESHOLD_WATTS):
    """Group above-threshold samples into charging spans with integrated energy.

    Returns a list of dicts: start, end, kwh, avg_kw, max_kw, ongoing.
    Energy integrates each sample's power over the interval to the next sample
    (capped at MAX_GAP_MINUTES to survive the hourly-statistics era).
    """
    charging = samples[samples["watts"] >= threshold_watts].reset_index(drop=True)
    if charging.empty:
        return []

    last_sample_time = samples["timestamp"].iloc[-1]
    spans = []
    current = None

    for row in charging.itertuples():
        if current is None:
            current = {"start": row.timestamp, "rows": [(row.timestamp, row.watts)]}
        else:
            gap_minutes = (row.timestamp - current["rows"][-1][0]).total_seconds() / 60
            if gap_minutes > MAX_GAP_MINUTES:
                spans.append(current)
                current = {"start": row.timestamp, "rows": [(row.timestamp, row.watts)]}
            else:
                current["rows"].append((row.timestamp, row.watts))
    if current is not None:
        spans.append(current)

    all_times = samples["timestamp"].reset_index(drop=True)

    results = []
    for span in spans:
        rows = span["rows"]
        # Each sample represents the interval up to the next one; the last
        # sample of a span covers one local cadence interval (1h in the HA
        # statistics era, ~1min for live data)
        if len(rows) >= 2:
            gaps = [(t_b - t_a).total_seconds() for (t_a, _), (t_b, _) in zip(rows, rows[1:])]
            gaps.sort()
            cadence_hours = gaps[len(gaps) // 2] / 3600
        else:
            following = all_times[all_times > rows[-1][0]]
            cadence_hours = (
                (following.iloc[0] - rows[-1][0]).total_seconds() / 3600
                if not following.empty
                else 1.0
            )
        cadence_hours = min(cadence_hours, MAX_GAP_MINUTES / 60)

        # Integrate power over inter-sample intervals
        kwh = 0.0
        for (t_a, w_a), (t_b, _) in zip(rows, rows[1:]):
            dt_hours = min((t_b - t_a).total_seconds() / 3600, MAX_GAP_MINUTES / 60)
            kwh += (w_a / 1000) * dt_hours
        kwh += (rows[-1][1] / 1000) * cadence_hours
        end = rows[-1][0] + pd.Timedelta(hours=cadence_hours)
        duration_hours = (end - span["start"]).total_seconds() / 3600
        max_kw = max(w for _, w in rows) / 1000
        avg_kw = kwh / duration_hours if duration_hours > 0 else max_kw
        if kwh < 0.1:
            continue
        # Ongoing only if the span's last sample is the newest sample we have:
        # any later sample was below threshold (else it would be in the span),
        # which proves charging stopped
        ongoing = rows[-1][0] >= last_sample_time
        results.append(
            {
                "start": span["start"],
                "end": end,
                "kwh": round(kwh, 2),
                "avg_kw": round(avg_kw, 2),
                "max_kw": round(max_kw, 2),
                "ongoing": ongoing,
            }
        )
    return results


def refine_sessions(spans, sessions_path, write=False, make_backup=True):
    """Correct or insert charging session records from plug spans.

    Matched sessions take the plug's exact times and metered wall energy and
    adopt an ha_* identity; unmatched spans insert as new sessions. Existing
    ha_* rows for the same span start are updated in place, making repeated
    refinement (including of ongoing sessions) idempotent.

    Returns (updated_count, inserted_count); writes only when write=True.
    """
    sessions_path = Path(sessions_path)
    if not sessions_path.exists() or not spans:
        return 0, 0

    sessions = pd.read_csv(sessions_path)
    sessions["_start"] = pd.to_datetime(sessions["start_time"], format="mixed", errors="coerce")
    sessions["_end"] = pd.to_datetime(sessions["end_time"], format="mixed", errors="coerce")
    # Freshly opened tracker sessions have no end_time yet; treat their start
    # as the end so overlap matching can still see them
    sessions["_end_filled"] = sessions["_end"].fillna(sessions["_start"])

    updated = inserted = 0
    dropped_rows = set()
    new_rows = []
    for span in spans:
        ha_id = f"ha_{span['start'].strftime('%Y%m%d_%H%M%S')}"
        values = {
            "start_time": span["start"].strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": span["end"].strftime("%Y-%m-%d %H:%M:%S"),
            "duration_minutes": round((span["end"] - span["start"]).total_seconds() / 60, 1),
            "energy_added": span["kwh"],
            "avg_power": span["avg_kw"],
            "max_power": span["max_kw"],
            "is_complete": not span["ongoing"],
            "network": "Home",
        }

        # Poll-derived tracker sessions this span covers; the plug data is
        # authoritative for them
        subsumable = sessions.index[
            (sessions["_start"] <= span["end"] + MATCH_PAD)
            & (sessions["_end_filled"] >= span["start"] - MATCH_PAD)
            & (sessions["session_id"].astype(str).str.startswith("charge_"))
            & (~sessions.index.isin(dropped_rows))
        ]

        existing = sessions.index[sessions["session_id"] == ha_id]
        if len(existing) > 0:
            idx = existing[0]
            # Tracker sessions that appeared after this ha_ row was created
            # (e.g. opened once the poller finally saw the charge) still get
            # subsumed on later refinements
            dropped_rows.update(subsumable)
            changed = (
                str(sessions.loc[idx, "end_time"]) != values["end_time"]
                or bool(sessions.loc[idx, "is_complete"]) != values["is_complete"]
                or pd.isna(sessions.loc[idx].get("network"))
                or not str(sessions.loc[idx].get("network", "")).strip()
            )
            if changed:
                for key, val in values.items():
                    sessions.loc[idx, key] = val
                updated += 1
            continue

        if len(subsumable) > 0:
            idx = subsumable[0]
            sessions.loc[idx, "session_id"] = ha_id
            for key, val in values.items():
                sessions.loc[idx, key] = val
            # The tracked SOC values may only cover part of the plug span
            # (especially when it subsumes several sessions); let the frontend
            # derive them from battery readings across the full span instead
            sessions.loc[idx, "start_battery"] = None
            sessions.loc[idx, "end_battery"] = None
            updated += 1
            # A plug span covering several poll-derived sessions subsumes them
            dropped_rows.update(subsumable[1:])
        else:
            new_rows.append(
                {
                    "session_id": ha_id,
                    "start_battery": None,
                    "end_battery": None,
                    "location_lat": None,
                    "location_lon": None,
                    **values,
                }
            )
            inserted += 1

    if write and (updated or inserted or dropped_rows):
        if dropped_rows:
            sessions = sessions.drop(index=list(dropped_rows))
        sessions = sessions.drop(columns=["_start", "_end", "_end_filled"])
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
            "Plug refinement: %d sessions corrected, %d inserted, %d subsumed",
            updated,
            inserted,
            len(dropped_rows),
        )
    return updated, inserted


def append_plug_sample(watts, timestamp=None, path=PLUG_LOG_PATH):
    """Append one webhook-delivered plug sample to the plug log."""
    path = Path(path)
    is_new = not path.exists()
    path.parent.mkdir(exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as handle:
        if is_new:
            handle.write("timestamp,watts\n")
        handle.write(f"{stamp},{watts}\n")
