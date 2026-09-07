import json
import os
from datetime import datetime, date
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row


def connect(prefix="LOCAL", autocommit=False):
    return psycopg.connect(
        host=os.environ[f"{prefix}_HOST"], dbname=os.environ.get(f"{prefix}_DB", "artisa"),
        user=os.environ.get(f"{prefix}_USER", "artisa"), password=os.environ[f"{prefix}_PASSWORD"],
        connect_timeout=4, autocommit=autocommit, row_factory=dict_row,
        options="-c statement_timeout=10000 -c timezone=UTC",
    )


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=json_default, allow_nan=False)


def initialize_local():
    with connect() as db:
        db.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        db.execute("""
            CREATE TABLE IF NOT EXISTS gps (
              event_id TEXT PRIMARY KEY, source_seq BIGINT NOT NULL UNIQUE,
              vehicle TEXT NOT NULL, recorded_at TIMESTAMPTZ NOT NULL,
              geom GEOMETRY(POINT,4326) NOT NULL, speed_kph DOUBLE PRECISION,
              ingested_at TIMESTAMPTZ NOT NULL DEFAULT now());
            CREATE INDEX IF NOT EXISTS gps_vehicle_time ON gps(vehicle,recorded_at);
            CREATE INDEX IF NOT EXISTS gps_geom ON gps USING GIST(geom);
            CREATE TABLE IF NOT EXISTS etl_checkpoint (
              id INT PRIMARY KEY DEFAULT 1, cursor BIGINT NOT NULL DEFAULT 0,
              accepted INT NOT NULL DEFAULT 0, rejected INT NOT NULL DEFAULT 0,
              duplicates INT NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
            INSERT INTO etl_checkpoint(id) VALUES(1) ON CONFLICT DO NOTHING;
            CREATE TABLE IF NOT EXISTS rejected_events (
              source_seq BIGINT PRIMARY KEY, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS roads (
              id TEXT PRIMARY KEY, start_node TEXT NOT NULL, end_node TEXT NOT NULL,
              name TEXT NOT NULL, geom GEOMETRY(LINESTRING,4326) NOT NULL);
            CREATE INDEX IF NOT EXISTS roads_geom ON roads USING GIST(geom);
            CREATE TABLE IF NOT EXISTS route_results (
              vehicle TEXT PRIMARY KEY, input_version TEXT NOT NULL,
              payload JSONB NOT NULL, computed_at TIMESTAMPTZ NOT NULL DEFAULT now());
            CREATE TABLE IF NOT EXISTS result_changes (
              seq BIGSERIAL PRIMARY KEY, vehicle TEXT NOT NULL,
              input_version TEXT NOT NULL, payload JSONB NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now());
            CREATE TABLE IF NOT EXISTS worker_health (
              name TEXT PRIMARY KEY, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), detail JSONB NOT NULL);
        """)


def initialize_cloud():
    with connect("CLOUD") as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS consolidated_routes (
              vehicle TEXT PRIMARY KEY, change_seq BIGINT NOT NULL,
              input_version TEXT NOT NULL, payload JSONB NOT NULL,
              synced_at TIMESTAMPTZ NOT NULL DEFAULT now());
            CREATE TABLE IF NOT EXISTS sync_checkpoint (
              id INT PRIMARY KEY DEFAULT 1, cursor BIGINT NOT NULL DEFAULT 0,
              last_success TIMESTAMPTZ, last_error TEXT, source_status JSONB);
            INSERT INTO sync_checkpoint(id) VALUES(1) ON CONFLICT DO NOTHING;
        """)
