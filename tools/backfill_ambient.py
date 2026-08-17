#!/usr/bin/env python3
"""Fill missing ambient temperature in battery_status.csv from Open-Meteo history.

Ambient temperature is not in the vehicle payload -- raw_data.airTemp is the
HVAC dial setting -- so the temperature column is sourced from Open-Meteo at
collection time. Rows collected while the weather lookup failed, and rows
rebuilt by tools/backfill_from_cache.py, therefore have no ambient reading.

Open-Meteo serves the same measurement retrospectively, so those gaps are
recoverable rather than permanent. Each row is placed at the car's actual
position by joining the nearest location fix, which matters whenever the car
was hours from home: a Duluth reading is not a Twin Cities reading.

Rows are grouped onto a 0.1-degree grid (~11 km, well inside the scale on
which ambient temperature varies) so a year of gaps costs a handful of API
calls rather than one per row.

Usage:
    python tools/backfill_ambient.py [--preview] [--tolerance-hours 6]
"""

import argparse
import fcntl
import sys
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DATA_DIR = Path("data")
GRID_DECIMALS = 1
# Open-Meteo's reanalysis archive lags real time by about five days; the
# forecast endpoint carries the recent past instead, up to 92 days back.
ARCHIVE_LAG_DAYS = 6
FORECAST_PAST_DAYS_MAX = 92


@contextmanager
def write_lock(data_dir):
    """Take the same sidecar lock csv_store uses, so a collecting run waits."""
    with open(data_dir / ".store.lock", "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_timestamps(path, column="timestamp"):
    """Load a CSV with its timestamp column parsed, tolerating mixed formats."""
    frame = pd.read_csv(path)
    frame[column] = pd.to_datetime(frame[column], errors="coerce", format="mixed")
    return frame


def locate_rows(gaps, locations, tolerance_hours):
    """Attach the nearest location fix to each gap row."""
    fixes = (
        locations.dropna(subset=["timestamp", "latitude", "longitude"])
        .sort_values("timestamp")[["timestamp", "latitude", "longitude"]]
        .reset_index(drop=True)
    )
    if fixes.empty:
        return gaps.assign(latitude=pd.NA, longitude=pd.NA)
    return pd.merge_asof(
        gaps.sort_values("timestamp"),
        fixes,
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(hours=tolerance_hours),
    )


def fetch_hourly(latitude, longitude, start, end, timezone="America/Chicago"):
    """Return hourly ambient temperature (Celsius) spanning start..end.

    Splits the request between the reanalysis archive and the forecast
    endpoint's past_days window, which is what covers the last few days the
    archive has not yet finalised.
    """
    frames = []
    cutoff = pd.Timestamp.now(tz=timezone).tz_localize(None).normalize() - pd.Timedelta(
        days=ARCHIVE_LAG_DAYS
    )
    common = {
        "latitude": round(latitude, 4),
        "longitude": round(longitude, 4),
        "hourly": "temperature_2m",
        "temperature_unit": "celsius",
        "timezone": timezone,
    }

    if start <= cutoff:
        frames.append(
            _request(
                ARCHIVE_URL,
                {
                    **common,
                    "start_date": start.strftime("%Y-%m-%d"),
                    "end_date": min(end, cutoff).strftime("%Y-%m-%d"),
                },
            )
        )

    if end > cutoff:
        span_days = (pd.Timestamp.now().normalize() - min(start, cutoff)).days + 1
        frames.append(
            _request(
                FORECAST_URL,
                {
                    **common,
                    "past_days": min(max(span_days, 1), FORECAST_PAST_DAYS_MAX),
                    "forecast_days": 1,
                },
            )
        )

    if not frames:
        return pd.DataFrame(columns=["timestamp", "ambient"])
    combined = pd.concat(frames, ignore_index=True).dropna(subset=["ambient"])
    return combined.drop_duplicates("timestamp").sort_values("timestamp")


def _request(url, params):
    """Fetch one Open-Meteo response as a timestamp/ambient frame."""
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    hourly = response.json().get("hourly", {})
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(hourly.get("time", [])),
            "ambient": hourly.get("temperature_2m", []),
        }
    )


def recover(battery, locations, tolerance_hours):
    """Return (index -> ambient) for every gap row Open-Meteo can answer."""
    missing = battery["temperature"].isna() & battery["timestamp"].notna()
    gaps = battery.loc[missing, ["timestamp"]].copy()
    gaps["row"] = gaps.index
    if gaps.empty:
        return {}

    located = locate_rows(gaps, locations, tolerance_hours).dropna(subset=["latitude"])
    if located.empty:
        return {}

    located["cell"] = list(
        zip(
            located["latitude"].round(GRID_DECIMALS),
            located["longitude"].round(GRID_DECIMALS),
        )
    )

    recovered = {}
    for (latitude, longitude), group in located.groupby("cell"):
        span_start = group["timestamp"].min().normalize()
        span_end = group["timestamp"].max().normalize()
        try:
            hourly = fetch_hourly(latitude, longitude, span_start, span_end)
        except (requests.RequestException, ValueError) as error:
            print(f"  {latitude},{longitude}: lookup failed ({error})", file=sys.stderr)
            continue
        if hourly.empty:
            continue
        matched = pd.merge_asof(
            group.sort_values("timestamp"),
            hourly,
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta(hours=1),
        ).dropna(subset=["ambient"])
        print(f"  {latitude:>6.1f},{longitude:>7.1f}  {len(matched):>3}/{len(group)} rows")
        recovered.update(dict(zip(matched["row"], matched["ambient"])))
    return recovered


def main():
    """Recover ambient temperature for rows that have none."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="report without writing")
    parser.add_argument(
        "--tolerance-hours",
        type=float,
        default=6.0,
        help="how far from a row to accept a location fix (default: 6)",
    )
    args = parser.parse_args()

    battery_path = DATA_DIR / "battery_status.csv"
    battery = read_timestamps(battery_path)
    locations = read_timestamps(DATA_DIR / "locations.csv")

    print(f"{battery['temperature'].isna().sum()} rows without ambient temperature")
    recovered = recover(battery, locations, args.tolerance_hours)
    if not recovered:
        print("nothing to recover")
        return

    values = pd.Series(recovered).round(1)
    print(f"\nrecovered {len(values)} rows, {values.min()}C to {values.max()}C")

    if args.preview:
        print("preview only, nothing written")
        return

    # Re-read under the lock: the collector may have appended rows since the
    # frame above was loaded, and those must not be dropped by writing back a
    # stale copy. Positional indices stay valid because rows are only appended.
    with write_lock(DATA_DIR):
        fresh = pd.read_csv(battery_path)
        for column in ("temperature", "meteo_temp"):
            target = fresh[column].isna()
            fill = values[values.index.isin(fresh.index[target])]
            fresh.loc[fill.index, column] = fill.values
        fresh.to_csv(battery_path, index=False)
    print(f"written to {battery_path}")


if __name__ == "__main__":
    main()
