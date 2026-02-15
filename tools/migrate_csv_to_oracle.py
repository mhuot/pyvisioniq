#!/usr/bin/env python3
"""
migrate_csv_to_oracle.py
Migrates existing CSV data to Oracle Autonomous Database.
Reads data/*.csv files and bulk-inserts into Oracle tables using MERGE
for deduplication. Supports --dry-run for preview without writing.

Usage:
    source venv/bin/activate
    python tools/migrate_csv_to_oracle.py [--dry-run] [--batch-size 500]
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

import oracledb

from src.storage.oracle_schema import ALL_TABLES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_connection():
    """Create a direct Oracle connection for migration."""
    oracle_user = os.getenv("ORACLE_USER")
    oracle_password = os.getenv("ORACLE_PASSWORD")
    oracle_dsn = os.getenv("ORACLE_DSN")
    wallet_location = os.getenv("ORACLE_WALLET_LOCATION")
    wallet_password = os.getenv("ORACLE_WALLET_PASSWORD")

    if not all([oracle_user, oracle_password, oracle_dsn]):
        logger.error("ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN must be set")
        sys.exit(1)

    connect_params = {
        "user": oracle_user,
        "password": oracle_password,
        "dsn": oracle_dsn,
    }

    if wallet_location:
        connect_params["config_dir"] = wallet_location
        connect_params["wallet_location"] = wallet_location
        if wallet_password:
            connect_params["wallet_password"] = wallet_password

    return oracledb.connect(**connect_params)


def ensure_tables(connection):
    """Create tables if they don't exist."""
    cursor = connection.cursor()
    for table_name, ddl in ALL_TABLES.items():
        cursor.execute(
            "SELECT 1 FROM user_tables WHERE table_name = :1",
            [table_name.upper()],
        )
        if cursor.fetchone() is None:
            cursor.execute(ddl)
            connection.commit()
            logger.info("Created table: %s", table_name)
        else:
            logger.info("Table already exists: %s", table_name)


def parse_timestamp(value):
    """Parse a timestamp value to datetime or None."""
    if pd.isna(value) or value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return pd.to_datetime(value).to_pydatetime()
    except Exception:
        return None


def to_float(value):
    """Safely convert to float or None."""
    if pd.isna(value) or value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def to_int(value):
    """Safely convert to int or None."""
    if pd.isna(value) or value is None:
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def migrate_trips(connection, data_dir, batch_size, dry_run):
    """Migrate trips.csv to Oracle."""
    trips_file = data_dir / "trips.csv"
    if not trips_file.exists():
        logger.warning("trips.csv not found, skipping")
        return 0

    dataframe = pd.read_csv(trips_file)
    logger.info("Read %d trips from CSV", len(dataframe))

    if dry_run:
        logger.info("[DRY RUN] Would migrate %d trips", len(dataframe))
        return len(dataframe)

    merge_sql = """
        MERGE INTO trips t
        USING (
            SELECT :trip_date AS trip_date,
                   :distance AS distance,
                   :odometer_start AS odometer_start,
                   :timestamp AS timestamp,
                   :duration AS duration,
                   :average_speed AS average_speed,
                   :max_speed AS max_speed,
                   :idle_time AS idle_time,
                   :trips_count AS trips_count,
                   :total_consumed AS total_consumed,
                   :regenerated_energy AS regenerated_energy,
                   :accessories_consumed AS accessories_consumed,
                   :climate_consumed AS climate_consumed,
                   :drivetrain_consumed AS drivetrain_consumed,
                   :battery_care_consumed AS battery_care_consumed,
                   :end_latitude AS end_latitude,
                   :end_longitude AS end_longitude,
                   :end_temperature AS end_temperature
            FROM dual
        ) s
        ON (t.trip_date = s.trip_date
            AND t.distance = s.distance
            AND (t.odometer_start = s.odometer_start
                 OR (t.odometer_start IS NULL AND s.odometer_start IS NULL)))
        WHEN NOT MATCHED THEN INSERT (
            timestamp, trip_date, distance, duration,
            average_speed, max_speed, idle_time, trips_count,
            total_consumed, regenerated_energy,
            accessories_consumed, climate_consumed,
            drivetrain_consumed, battery_care_consumed,
            odometer_start, end_latitude, end_longitude, end_temperature
        ) VALUES (
            s.timestamp, s.trip_date, s.distance, s.duration,
            s.average_speed, s.max_speed, s.idle_time, s.trips_count,
            s.total_consumed, s.regenerated_energy,
            s.accessories_consumed, s.climate_consumed,
            s.drivetrain_consumed, s.battery_care_consumed,
            s.odometer_start, s.end_latitude, s.end_longitude,
            s.end_temperature
        )
    """

    cursor = connection.cursor()
    inserted_count = 0

    for start_idx in range(0, len(dataframe), batch_size):
        batch = dataframe.iloc[start_idx : start_idx + batch_size]
        for _, row in batch.iterrows():
            try:
                params = {
                    "timestamp": parse_timestamp(row.get("timestamp")),
                    "trip_date": parse_timestamp(row.get("date")),
                    "distance": to_float(row.get("distance")),
                    "duration": to_float(row.get("duration")),
                    "average_speed": to_float(row.get("average_speed")),
                    "max_speed": to_float(row.get("max_speed")),
                    "idle_time": to_float(row.get("idle_time")),
                    "trips_count": to_int(row.get("trips_count")),
                    "total_consumed": to_float(row.get("total_consumed")),
                    "regenerated_energy": to_float(row.get("regenerated_energy")),
                    "accessories_consumed": to_float(row.get("accessories_consumed")),
                    "climate_consumed": to_float(row.get("climate_consumed")),
                    "drivetrain_consumed": to_float(row.get("drivetrain_consumed")),
                    "battery_care_consumed": to_float(row.get("battery_care_consumed")),
                    "odometer_start": to_float(row.get("odometer_start")),
                    "end_latitude": to_float(row.get("end_latitude")),
                    "end_longitude": to_float(row.get("end_longitude")),
                    "end_temperature": to_float(row.get("end_temperature")),
                }
                cursor.execute(merge_sql, params)
                if cursor.rowcount > 0:
                    inserted_count += 1
            except oracledb.DatabaseError as exc:
                logger.warning("Error inserting trip row: %s", exc)

        connection.commit()
        logger.info(
            "Trips: processed %d / %d rows",
            min(start_idx + batch_size, len(dataframe)),
            len(dataframe),
        )

    logger.info("Trips: inserted %d new rows", inserted_count)
    return inserted_count


def migrate_battery_status(connection, data_dir, batch_size, dry_run):
    """Migrate battery_status.csv to Oracle."""
    battery_file = data_dir / "battery_status.csv"
    if not battery_file.exists():
        logger.warning("battery_status.csv not found, skipping")
        return 0

    dataframe = pd.read_csv(battery_file)
    logger.info("Read %d battery records from CSV", len(dataframe))

    if dry_run:
        logger.info("[DRY RUN] Would migrate %d battery records", len(dataframe))
        return len(dataframe)

    insert_sql = """
        INSERT INTO battery_status (
            timestamp, battery_level, is_charging, charging_power,
            remaining_time, battery_range, temperature, odometer,
            meteo_temp, vehicle_temp, is_cached
        ) VALUES (
            :timestamp, :battery_level, :is_charging, :charging_power,
            :remaining_time, :battery_range, :temperature, :odometer,
            :meteo_temp, :vehicle_temp, :is_cached
        )
    """

    cursor = connection.cursor()
    inserted_count = 0
    duplicate_count = 0

    for start_idx in range(0, len(dataframe), batch_size):
        batch = dataframe.iloc[start_idx : start_idx + batch_size]
        for _, row in batch.iterrows():
            try:
                params = {
                    "timestamp": parse_timestamp(row.get("timestamp")),
                    "battery_level": to_float(row.get("battery_level")),
                    "is_charging": str(row.get("is_charging", False)),
                    "charging_power": to_float(row.get("charging_power")),
                    "remaining_time": to_float(row.get("remaining_time")),
                    "battery_range": to_float(row.get("range")),
                    "temperature": to_float(row.get("temperature")),
                    "odometer": to_float(row.get("odometer")),
                    "meteo_temp": to_float(row.get("meteo_temp")),
                    "vehicle_temp": to_float(row.get("vehicle_temp")),
                    "is_cached": str(row.get("is_cached", False)),
                }
                cursor.execute(insert_sql, params)
                inserted_count += 1
            except oracledb.IntegrityError:
                duplicate_count += 1
            except oracledb.DatabaseError as exc:
                logger.warning("Error inserting battery row: %s", exc)

        connection.commit()
        logger.info(
            "Battery: processed %d / %d rows",
            min(start_idx + batch_size, len(dataframe)),
            len(dataframe),
        )

    logger.info(
        "Battery: inserted %d new rows, %d duplicates skipped",
        inserted_count,
        duplicate_count,
    )
    return inserted_count


def migrate_locations(connection, data_dir, batch_size, dry_run):
    """Migrate locations.csv to Oracle."""
    location_file = data_dir / "locations.csv"
    if not location_file.exists():
        logger.warning("locations.csv not found, skipping")
        return 0

    dataframe = pd.read_csv(location_file)
    logger.info("Read %d location records from CSV", len(dataframe))

    if dry_run:
        logger.info("[DRY RUN] Would migrate %d location records", len(dataframe))
        return len(dataframe)

    insert_sql = """
        INSERT INTO locations (
            timestamp, latitude, longitude, last_updated
        ) VALUES (
            :timestamp, :latitude, :longitude, :last_updated
        )
    """

    cursor = connection.cursor()
    inserted_count = 0
    duplicate_count = 0

    for start_idx in range(0, len(dataframe), batch_size):
        batch = dataframe.iloc[start_idx : start_idx + batch_size]
        for _, row in batch.iterrows():
            try:
                params = {
                    "timestamp": parse_timestamp(row.get("timestamp")),
                    "latitude": to_float(row.get("latitude")),
                    "longitude": to_float(row.get("longitude")),
                    "last_updated": parse_timestamp(row.get("last_updated")),
                }
                cursor.execute(insert_sql, params)
                inserted_count += 1
            except oracledb.IntegrityError:
                duplicate_count += 1
            except oracledb.DatabaseError as exc:
                logger.warning("Error inserting location row: %s", exc)

        connection.commit()
        logger.info(
            "Locations: processed %d / %d rows",
            min(start_idx + batch_size, len(dataframe)),
            len(dataframe),
        )

    logger.info(
        "Locations: inserted %d new rows, %d duplicates skipped",
        inserted_count,
        duplicate_count,
    )
    return inserted_count


def migrate_charging_sessions(connection, data_dir, batch_size, dry_run):
    """Migrate charging_sessions.csv to Oracle."""
    sessions_file = data_dir / "charging_sessions.csv"
    if not sessions_file.exists():
        logger.warning("charging_sessions.csv not found, skipping")
        return 0

    dataframe = pd.read_csv(sessions_file)
    logger.info("Read %d charging sessions from CSV", len(dataframe))

    if dry_run:
        logger.info("[DRY RUN] Would migrate %d charging sessions", len(dataframe))
        return len(dataframe)

    merge_sql = """
        MERGE INTO charging_sessions t
        USING (
            SELECT :session_id AS session_id FROM dual
        ) s
        ON (t.session_id = s.session_id)
        WHEN NOT MATCHED THEN INSERT (
            session_id, start_time, end_time, duration_minutes,
            start_battery, end_battery, energy_added,
            avg_power, max_power, location_lat, location_lon, is_complete
        ) VALUES (
            :session_id, :start_time, :end_time, :duration_minutes,
            :start_battery, :end_battery, :energy_added,
            :avg_power, :max_power, :location_lat, :location_lon,
            :is_complete
        )
    """

    cursor = connection.cursor()
    inserted_count = 0

    for start_idx in range(0, len(dataframe), batch_size):
        batch = dataframe.iloc[start_idx : start_idx + batch_size]
        for _, row in batch.iterrows():
            try:
                params = {
                    "session_id": str(row.get("session_id")),
                    "start_time": parse_timestamp(row.get("start_time")),
                    "end_time": parse_timestamp(row.get("end_time")),
                    "duration_minutes": to_float(row.get("duration_minutes")),
                    "start_battery": to_float(row.get("start_battery")),
                    "end_battery": to_float(row.get("end_battery")),
                    "energy_added": to_float(row.get("energy_added")),
                    "avg_power": to_float(row.get("avg_power")),
                    "max_power": to_float(row.get("max_power")),
                    "location_lat": to_float(row.get("location_lat")),
                    "location_lon": to_float(row.get("location_lon")),
                    "is_complete": str(row.get("is_complete", False)),
                }
                cursor.execute(merge_sql, params)
                if cursor.rowcount > 0:
                    inserted_count += 1
            except oracledb.DatabaseError as exc:
                logger.warning("Error inserting charging session: %s", exc)

        connection.commit()
        logger.info(
            "Charging sessions: processed %d / %d rows",
            min(start_idx + batch_size, len(dataframe)),
            len(dataframe),
        )

    logger.info("Charging sessions: inserted %d new rows", inserted_count)
    return inserted_count


def main():
    """Run the CSV-to-Oracle migration."""
    parser = argparse.ArgumentParser(
        description="Migrate PyVisionIQ CSV data to Oracle Autonomous Database"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without writing to Oracle",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of rows per batch (default: 500)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Path to data directory (default: data)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        logger.error("Data directory not found: %s", data_dir)
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("PyVisionIQ CSV to Oracle Migration")
    logger.info("=" * 60)
    logger.info("Data directory: %s", data_dir)
    logger.info("Batch size: %d", args.batch_size)
    if args.dry_run:
        logger.info("MODE: DRY RUN (no writes)")
    logger.info("")

    connection = get_connection()
    logger.info("Connected to Oracle")

    if not args.dry_run:
        ensure_tables(connection)

    total_inserted = 0
    total_inserted += migrate_trips(connection, data_dir, args.batch_size, args.dry_run)
    total_inserted += migrate_battery_status(
        connection, data_dir, args.batch_size, args.dry_run
    )
    total_inserted += migrate_locations(
        connection, data_dir, args.batch_size, args.dry_run
    )
    total_inserted += migrate_charging_sessions(
        connection, data_dir, args.batch_size, args.dry_run
    )

    logger.info("")
    logger.info("=" * 60)
    if args.dry_run:
        logger.info(
            "DRY RUN complete. %d total rows would be migrated.", total_inserted
        )
    else:
        logger.info("Migration complete. %d total new rows inserted.", total_inserted)
    logger.info("=" * 60)

    connection.close()


if __name__ == "__main__":
    main()
