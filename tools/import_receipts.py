#!/usr/bin/env python3
"""
Import parsed charging receipts into charging_sessions.csv.

Input is a JSON array of normalized receipt records; see
src/utils/receipts.py for the format and merge semantics.

Usage:
    python tools/import_receipts.py receipts.json            # dry run
    python tools/import_receipts.py receipts.json --write    # apply
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.utils.receipts import upsert_receipts

SESSIONS_CSV = Path("data/charging_sessions.csv")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts_json", help="Path to the JSON array of receipt records")
    parser.add_argument("--write", action="store_true", help="Apply changes (default: dry run)")
    args = parser.parse_args()

    with open(args.receipts_json, "r", encoding="utf-8") as handle:
        receipts = json.load(handle)

    updated, inserted, skipped, messages = upsert_receipts(receipts, SESSIONS_CSV, write=args.write)
    for line in messages:
        print(line)
    print(f"\ncorrected: {updated}, inserted: {inserted}, skipped: {skipped}")
    if not args.write:
        print("Dry run - re-run with --write to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
