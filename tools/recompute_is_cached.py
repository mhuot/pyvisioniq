#!/usr/bin/env python3
"""Recompute historical cache freshness flags.

This script replays cached Hyundai responses stored under the cache directory
and applies the current freshness heuristic to determine whether the backend
returned new vehicle data. The resulting `is_cached` flag is written back to
both the cache JSON files and the battery status CSV history.

Run from the project root:
    python3 tools/recompute_is_cached.py
"""

import argparse
import json
import os
import sys
from collections import defaultdict, deque
from datetime import datetime
from hashlib import md5
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

CACHE_EXCLUDE_PREFIXES = ("error_",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache"),
        help="Directory containing cached API responses",
    )
    parser.add_argument(
        "--battery-file",
        type=Path,
        default=Path("data") / "battery_status.csv",
        help="Path to battery status CSV file",
    )
    parser.add_argument(
        "--write-cache",
        action="store_true",
        help="Persist updated freshness flags back into cache JSON files",
    )
    return parser.parse_args()


def parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # Accept timestamps that use a space between date/time
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def parse_remote_timestamp(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            if candidate.isdigit() and len(candidate) == 14:
                try:
                    return datetime.strptime(candidate, "%Y%m%d%H%M%S")
                except ValueError:
                    return None
    return None


def extract_remote_timestamp(data: Dict) -> Optional[datetime]:
    if not isinstance(data, dict):
        return None

    candidates = [data.get("api_last_updated")]

    raw_data = data.get("raw_data")
    if isinstance(raw_data, dict):
        vehicle_status = raw_data.get("vehicleStatus")
        if isinstance(vehicle_status, dict):
            candidates.append(vehicle_status.get("dateTime"))
            ev_status = vehicle_status.get("evStatus")
            if isinstance(ev_status, dict):
                candidates.append(ev_status.get("lastUpdatedAt"))

    for candidate in candidates:
        ts = parse_remote_timestamp(candidate)
        if ts:
            return ts
    return None


def raw_data_signature(data: Dict) -> Optional[str]:
    try:
        raw = data.get("raw_data", {}) if isinstance(data, dict) else {}
        serialized = json.dumps(raw, sort_keys=True, default=str)
        return md5(serialized.encode("utf-8")).hexdigest()
    except Exception:
        return None


def is_remote_data_fresh(new_data: Dict, previous_data: Optional[Dict]) -> bool:
    if not isinstance(new_data, dict):
        return False

    new_ts = extract_remote_timestamp(new_data)
    prev_ts = extract_remote_timestamp(previous_data) if previous_data else None

    if new_ts and prev_ts:
        if new_ts > prev_ts:
            return True
        if new_ts < prev_ts:
            return True  # treat clock skew as fresh
        fresh = False
    elif new_ts and not prev_ts:
        fresh = True
    elif not new_ts and prev_ts:
        fresh = False
    else:
        fresh = True

    if not fresh:
        new_sig = raw_data_signature(new_data)
        prev_sig = raw_data_signature(previous_data) if previous_data else None
        if new_sig and prev_sig and new_sig != prev_sig:
            fresh = True
    return fresh


def iter_cache_files(cache_dir: Path) -> List[Path]:
    paths: List[Path] = []
    for path in cache_dir.glob("*.json"):
        if path.name.startswith(CACHE_EXCLUDE_PREFIXES):
            continue
        stem = path.stem
        if len(stem) == 32 and all(c in "0123456789abcdef" for c in stem.lower()):
            paths.append(path)
    for path in cache_dir.glob("history_*.json"):
        if path.name.startswith(CACHE_EXCLUDE_PREFIXES):
            continue
        paths.append(path)
    return paths


def load_cache_entries(cache_dir: Path):
    entries = []
    for path in iter_cache_files(cache_dir):
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"Warning: failed to read {path}: {exc}")
            continue
        timestamp = parse_timestamp(data.get("timestamp"))
        if not timestamp:
            continue
        entries.append((timestamp, path, data))
    entries.sort(key=lambda item: item[0])
    return entries


def update_cache_files(entries, *, write_cache: bool):
    previous_data: Optional[Dict] = None
    freshness_by_timestamp: Dict[str, List[bool]] = defaultdict(list)

    for timestamp, path, data in entries:
        is_fresh = is_remote_data_fresh(data, previous_data)
        data["hyundai_data_fresh"] = is_fresh
        data["is_cached"] = not is_fresh

        if write_cache and os.access(path, os.W_OK):
            try:
                with open(path, "w") as f:
                    json.dump(data, f, indent=2, default=str)
            except PermissionError:
                print(f"Skipping cache write (permission denied): {path}")

        ts_key = timestamp.isoformat()
        freshness_by_timestamp[ts_key].append(not is_fresh)
        previous_data = data

    return {k: deque(v) for k, v in freshness_by_timestamp.items()}


def apply_freshness_to_csv(csv_path: Path, freshness_map: Dict[str, List[bool]]) -> int:
    if not csv_path.exists():
        print(f"Battery CSV not found at {csv_path}")
        return 0

    df = pd.read_csv(csv_path)
    updated_rows = 0

    def pop_flag(ts_str: str) -> Optional[bool]:
        ts = parse_timestamp(ts_str)
        if not ts:
            return None
        key = ts.isoformat()
        flags = freshness_map.get(key)
        if flags:
            return flags.popleft()
        return None

    def parse_existing(value) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return bool(value)

    is_cached_values: List[bool] = []
    for _, row in df.iterrows():
        flag = pop_flag(str(row["timestamp"]))
        if flag is None:
            is_cached_values.append(parse_existing(row.get("is_cached", False)))
            continue
        updated_rows += 1
        is_cached_values.append(flag)

    if updated_rows == 0:
        print("No matching timestamps found; CSV unchanged")
        return 0

    df["is_cached"] = [bool(val) for val in is_cached_values]
    df.to_csv(csv_path, index=False)
    return updated_rows


def main():
    args = parse_args()
    entries = load_cache_entries(args.cache_dir)
    if not entries:
        print("No cache entries found")
        return 1

    print(f"Replaying {len(entries)} cache entries...")
    freshness_map = update_cache_files(entries, write_cache=args.write_cache)
    updated_count = apply_freshness_to_csv(args.battery_file, freshness_map)
    print(f"Updated {updated_count} battery history rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
