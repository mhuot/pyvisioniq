"""
oracle_schema.py
DDL definitions for Oracle Autonomous Database tables.
Uses standard Oracle types for compatibility with Microsoft Fabric Data Gateway.
"""

TRIPS_TABLE = """
CREATE TABLE trips (
    timestamp       TIMESTAMP,
    trip_date       TIMESTAMP,
    distance        NUMBER(10,2),
    duration        NUMBER(10,1),
    average_speed   NUMBER(10,2),
    max_speed       NUMBER(10,2),
    idle_time       NUMBER(10,1),
    trips_count     NUMBER(5),
    total_consumed  NUMBER(12,2),
    regenerated_energy    NUMBER(12,2),
    accessories_consumed  NUMBER(12,2),
    climate_consumed      NUMBER(12,2),
    drivetrain_consumed   NUMBER(12,2),
    battery_care_consumed NUMBER(12,2),
    odometer_start  NUMBER(12,1),
    end_latitude    NUMBER(12,8),
    end_longitude   NUMBER(12,8),
    end_temperature NUMBER(6,2),
    CONSTRAINT trips_uk UNIQUE (trip_date, distance, odometer_start)
)
"""

BATTERY_STATUS_TABLE = """
CREATE TABLE battery_status (
    timestamp       TIMESTAMP,
    battery_level   NUMBER(5,1),
    is_charging     VARCHAR2(10),
    charging_power  NUMBER(8,2),
    remaining_time  NUMBER(10,1),
    battery_range   NUMBER(10,1),
    temperature     NUMBER(6,2),
    odometer        NUMBER(12,1),
    meteo_temp      NUMBER(6,2),
    vehicle_temp    NUMBER(6,2),
    is_cached       VARCHAR2(10),
    CONSTRAINT battery_status_uk UNIQUE (timestamp)
)
"""

LOCATIONS_TABLE = """
CREATE TABLE locations (
    timestamp       TIMESTAMP,
    latitude        NUMBER(12,8),
    longitude       NUMBER(12,8),
    last_updated    TIMESTAMP,
    CONSTRAINT locations_uk UNIQUE (timestamp)
)
"""

CHARGING_SESSIONS_TABLE = """
CREATE TABLE charging_sessions (
    session_id      VARCHAR2(100) PRIMARY KEY,
    start_time      TIMESTAMP,
    end_time        TIMESTAMP,
    duration_minutes NUMBER(10,1),
    start_battery   NUMBER(5,1),
    end_battery     NUMBER(5,1),
    energy_added    NUMBER(10,2),
    avg_power       NUMBER(10,2),
    max_power       NUMBER(10,2),
    location_lat    NUMBER(12,8),
    location_lon    NUMBER(12,8),
    is_complete     VARCHAR2(10)
)
"""

ALL_TABLES = {
    "trips": TRIPS_TABLE,
    "battery_status": BATTERY_STATUS_TABLE,
    "locations": LOCATIONS_TABLE,
    "charging_sessions": CHARGING_SESSIONS_TABLE,
}

# Column mapping from CSV column names to Oracle column names
# (only needed where they differ to avoid Oracle reserved words)
CSV_TO_ORACLE_COLUMN_MAP = {
    "trips": {
        "date": "trip_date",
        "range": "battery_range",
    },
    "battery_status": {
        "range": "battery_range",
    },
    "locations": {},
    "charging_sessions": {},
}

ORACLE_TO_CSV_COLUMN_MAP = {
    "trips": {
        "trip_date": "date",
        "battery_range": "range",
    },
    "battery_status": {
        "battery_range": "range",
    },
    "locations": {},
    "charging_sessions": {},
}
