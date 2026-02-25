"""Load pyvisionic CSV data into a local PostgreSQL database.

Usage:
    # Set connection string (or use defaults)
    export POSTGRES_URL="postgresql://user:pass@localhost:5432/pyvisionic"

    # Dry run — show what would be created
    python tools/csv_to_postgres.py --dry-run

    # Import all CSVs
    python tools/csv_to_postgres.py

    # Import specific table
    python tools/csv_to_postgres.py --table battery_status

Requires: pip install psycopg2-binary pandas sqlalchemy
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# CSV file -> table name mapping
TABLES = {
    "battery_status": {
        "file": "battery_status.csv",
        "parse_dates": ["timestamp"],
    },
    "trips": {
        "file": "trips.csv",
        "parse_dates": ["timestamp", "date"],
    },
    "charging_sessions": {
        "file": "charging_sessions.csv",
        "parse_dates": ["start_time", "end_time"],
    },
    "locations": {
        "file": "locations.csv",
        "parse_dates": ["timestamp", "last_updated"],
    },
}


def get_engine(url):
    """Create SQLAlchemy engine from connection URL."""
    return create_engine(url)


def load_csv(table_config):
    """Read a CSV file into a pandas DataFrame."""
    filepath = DATA_DIR / table_config["file"]
    if not filepath.exists():
        print(f"  WARNING: {filepath} not found, skipping")
        return None

    df = pd.read_csv(
        filepath,
        parse_dates=table_config.get("parse_dates", []),
    )
    return df


def import_table(engine, table_name, table_config, replace=True):
    """Import a single CSV into PostgreSQL."""
    df = load_csv(table_config)
    if df is None:
        return 0

    if_exists = "replace" if replace else "append"
    df.to_sql(table_name, engine, if_exists=if_exists, index=False)
    return len(df)


def main():
    parser = argparse.ArgumentParser(description="Load CSV data into PostgreSQL")
    parser.add_argument(
        "--url",
        default=os.environ.get(
            "POSTGRES_URL", "postgresql://pyvisionic:pyvisionic@localhost:5432/pyvisionic"
        ),
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--table",
        choices=list(TABLES.keys()),
        help="Import only this table (default: all)",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing tables instead of replacing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without connecting to DB",
    )
    args = parser.parse_args()

    tables_to_import = {args.table: TABLES[args.table]} if args.table else TABLES

    if args.dry_run:
        print("Dry run — no database connection\n")
        for name, config in tables_to_import.items():
            df = load_csv(config)
            if df is not None:
                print(f"Table: {name}")
                print(f"  File: {DATA_DIR / config['file']}")
                print(f"  Rows: {len(df)}")
                print(f"  Columns: {', '.join(df.columns)}")
                print(f"  Dtypes:\n{df.dtypes.to_string()}\n")
        return

    engine = get_engine(args.url)

    # Verify connection
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"Connected to {args.url}\n")
    except Exception as exc:
        print(f"ERROR: Cannot connect to {args.url}: {exc}")
        sys.exit(1)

    for name, config in tables_to_import.items():
        print(f"Importing {name}...")
        count = import_table(engine, name, config, replace=not args.append)
        if count:
            print(f"  {count} rows loaded into '{name}'")

    print("\nDone. Verify with:")
    print("  psql -d pyvisionic -c '\\dt'")


if __name__ == "__main__":
    main()
