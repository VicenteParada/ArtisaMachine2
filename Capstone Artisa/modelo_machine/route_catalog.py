"""Construye un catálogo de rutas a partir de raw_gps, sin modificar la base.

Se ejecuta por separado del servicio HTTP. Cada camión se confirma en una base
SQLite de trabajo, por lo que una interrupción permite reanudar el siguiente.
El JSON público sólo se reemplaza cuando termina toda la fuente solicitada.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
import zlib

import numpy as np
import pymysql
from sklearn.cluster import Birch

from common.telemetry_map import _distance_km, _resample_geometry, _route_geometry


MAX_GAP_SECONDS = 45 * 60
MAX_JOURNEY_SECONDS = 24 * 3600
MAX_SPEED_KMH = 160
MIN_POINTS = 8
MIN_DISTANCE_KM = 1.0
SHAPE_SAMPLES = 16
COLORS = {"HIGH": "#14532d", "MEDIUM": "#f97316", "LOW": "#dc2626"}


def _db():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "mariadb"),
        database=os.environ.get("DB_NAME", "artisa"),
        user=os.environ.get("DB_USER", "artisa_app"),
        password=os.environ["DB_PASSWORD"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
        read_timeout=3600,
    )


def _source_version(db):
    with db.cursor() as cursor:
        cursor.execute("SELECT validos,ultimo_mongo_id FROM raw_gps_estado WHERE id=1")
        state = cursor.fetchone() or {}
    if not state.get("validos"):
        raise ValueError("raw_gps_estado no informa reportes válidos")
    identity = f"{state['validos']}|{state.get('ultimo_mongo_id') or ''}"
    return sha256(identity.encode()).hexdigest(), int(state["validos"])


def _next_asset(db, after):
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT asset_id FROM raw_gps WHERE asset_id > %s ORDER BY asset_id LIMIT 1",
            (after,),
        )
        row = cursor.fetchone()
    return row["asset_id"] if row else None


def _work_db(path):
    work = sqlite3.connect(path)
    work.execute("PRAGMA journal_mode=WAL")
    work.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    work.execute("""CREATE TABLE IF NOT EXISTS trips (
        id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL, started_at TEXT NOT NULL,
        ended_at TEXT NOT NULL, start_lat REAL, start_lon REAL, end_lat REAL,
        end_lon REAL, distance_km REAL, points INTEGER, shape BLOB, path BLOB
    )""")
    work.execute("""CREATE TABLE IF NOT EXISTS stops (
        trip_id INTEGER, asset_id TEXT, lat REAL, lon REAL, duration_min REAL
    )""")
    work.commit()
    return work


def _meta(work, key, default=""):
    row = work.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(work, key, value):
    work.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))


def _valid_row(row):
    try:
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])
        return (-90 <= latitude <= 90 and -180 <= longitude <= 180 and
                (latitude != 0 or longitude != 0) and
                isinstance(row["report_date"], datetime))
    except (TypeError, ValueError):
        return False


def _shape(points):
    samples = _resample_geometry(points, SHAPE_SAMPLES)
    latitude = points[0][0]
    scale = np.array([111.195, 111.195 * math.cos(math.radians(latitude))])
    return (np.asarray(samples) * scale / math.sqrt(SHAPE_SAMPLES)).astype("float32")


def _save_trip(work, asset, rows):
    if len(rows) < MIN_POINTS:
        return False
    points = [(float(row["latitude"]), float(row["longitude"])) for row in rows]
    distances = [_distance_km(first, second) for first, second in zip(points, points[1:])]
    travelled = sum(distances)
    if travelled < MIN_DISTANCE_KM:
        return False
    path = _route_geometry(rows)
    if len(path) < 2:
        return False
    shape = _shape(path)
    packed_path = zlib.compress(json.dumps([[lon, lat] for lat, lon in path],
                                           separators=(",", ":")).encode(), 3)
    started, ended = rows[0]["report_date"], rows[-1]["report_date"]
    record = work.execute("""INSERT INTO trips
        (asset_id,started_at,ended_at,start_lat,start_lon,end_lat,end_lon,
         distance_km,points,shape,path) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (asset, started.isoformat(), ended.isoformat(), *points[0], *points[-1],
         travelled, len(rows), shape.tobytes(), packed_path))
    slow_start = None
    for index in range(len(rows) + 1):
        speed = rows[index].get("speed") if index < len(rows) else None
        slow = speed is not None and 0 <= float(speed) <= 3
        if slow and slow_start is None:
            slow_start = index
        if slow_start is not None and not slow:
            group = rows[slow_start:index]
            duration = (group[-1]["report_date"] - group[0]["report_date"]).total_seconds() / 60
            lat = float(np.median([point["latitude"] for point in group]))
            lon = float(np.median([point["longitude"] for point in group]))
            spread = max(_distance_km((lat, lon),
                                      (float(point["latitude"]), float(point["longitude"])))
                         for point in group)
            if duration >= 5 and spread <= 0.2:
                work.execute("INSERT INTO stops VALUES(?,?,?,?,?)",
                             (record.lastrowid, asset, lat, lon, duration))
            slow_start = None
    return True


def _process_asset(db, work, asset):
    read = accepted = trips = 0
    current = []
    last = None
    with db.cursor(pymysql.cursors.SSDictCursor) as cursor:
        cursor.execute("""SELECT report_id,report_date,latitude,longitude,speed
            FROM raw_gps WHERE asset_id=%s ORDER BY report_date""", (asset,))
        for row in cursor:
            read += 1
            if not _valid_row(row):
                continue
            if last is not None:
                elapsed = (row["report_date"] - last["report_date"]).total_seconds()
                distance = _distance_km((float(last["latitude"]), float(last["longitude"])),
                                        (float(row["latitude"]), float(row["longitude"])))
                if elapsed <= 0 or (elapsed > 0 and distance / (elapsed / 3600) > MAX_SPEED_KMH):
                    continue
                if (elapsed > MAX_GAP_SECONDS or
                        (row["report_date"] - current[0]["report_date"]).total_seconds()
                        > MAX_JOURNEY_SECONDS):
                    trips += int(_save_trip(work, asset, current))
                    current = []
            current.append(row)
            last = row
            accepted += 1
    trips += int(_save_trip(work, asset, current))
    return read, accepted, trips


def _usage_levels(counts):
    values = sorted(set(counts))
    if len(values) == 1:
        return (values[0], values[0])
    return (values[(len(values) - 1) // 3], values[2 * (len(values) - 1) // 3])


def _build_catalog(work, version, expected, complete):
    rows = work.execute("""SELECT id,asset_id,started_at,ended_at,start_lat,start_lon,
        end_lat,end_lon,distance_km,points,shape,path FROM trips ORDER BY id""").fetchall()
    if not rows:
        raise ValueError("No se detectaron viajes válidos")
    od_features = np.asarray([
        [row[4] * 111.195, row[5] * 111.195 * math.cos(math.radians(row[4])),
         row[6] * 111.195, row[7] * 111.195 * math.cos(math.radians(row[6]))]
        for row in rows], dtype="float32") / math.sqrt(2)
    od_labels = Birch(threshold=4.0, n_clusters=None).fit_predict(od_features)
    by_od = defaultdict(list)
    for index, label in enumerate(od_labels):
        by_od[int(label)].append(index)
    groups = []
    for indices in by_od.values():
        vectors = np.asarray([np.frombuffer(rows[index][10], dtype="float32")
                              for index in indices])
        labels = (Birch(threshold=1.4, n_clusters=None).fit_predict(vectors)
                  if len(indices) > 1 else [0])
        for label in set(labels):
            members = [indices[position] for position, value in enumerate(labels)
                       if value == label]
            center = vectors[[position for position, value in enumerate(labels)
                              if value == label]].mean(axis=0)
            representative = min(members, key=lambda index: float(np.linalg.norm(
                np.frombuffer(rows[index][10], dtype="float32") - center)))
            groups.append((members, representative))
    low, high = _usage_levels([len(members) for members, _ in groups])
    routes = []
    trip_route = {}
    for members, representative in groups:
        path = json.loads(zlib.decompress(rows[representative][11]))
        count = len(members)
        level = "HIGH" if count > high else "MEDIUM" if count > low else "LOW"
        identity = json.dumps([[round(lon, 5), round(lat, 5)]
                               for lat, lon in _resample_geometry(
                                   [(lat, lon) for lon, lat in path], 12)],
                              separators=(",", ":"))
        route_id = "R_" + sha256(identity.encode()).hexdigest()[:12].upper()
        for member in members:
            trip_route[rows[member][0]] = route_id
        routes.append({
            "route_id": route_id, "label": route_id, "usage_count": count,
            "unique_vehicle_count": len({rows[member][1] for member in members}),
            "usage_level": level, "color": COLORS[level],
            "observed_points": rows[representative][9],
            "geometry_source": "GPS_TRACE_UNMATCHED", "map_matching": False,
            "geometry": {"type": "LineString", "coordinates": path},
        })
    zones = defaultdict(lambda: {"trips": set(), "assets": set(), "duration": 0.0,
                                 "lat": 0.0, "lon": 0.0, "count": 0})
    for trip_id, asset, lat, lon, duration in work.execute("SELECT * FROM stops"):
        route_id = trip_route.get(trip_id)
        key = (route_id, round(lat, 2), round(lon, 2))
        zone = zones[key]
        zone["trips"].add(trip_id)
        zone["assets"].add(asset)
        zone["duration"] += duration
        zone["lat"] += lat
        zone["lon"] += lon
        zone["count"] += 1
    stops = []
    for index, ((route_id, _, _), zone) in enumerate(
            sorted(zones.items(), key=lambda item: -item[1]["count"])[:500], 1):
        stops.append({
            "stop_id": f"STOP_{index}", "label": f"Zona de parada {index}",
            "route_id": route_id, "latitude": zone["lat"] / zone["count"],
            "longitude": zone["lon"] / zone["count"],
            "reading_count": zone["count"], "vehicle_count": len(zone["assets"]),
            "stop_event_count": zone["count"],
            "duration_minutes": zone["duration"] / zone["count"],
        })
    processed = int(_meta(work, "read", 0))
    return {
        "schema_version": "1.3", "source_mode": "HISTORICAL_GPS_CATALOG",
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "description": ("Catálogo histórico de recorridos GPS agrupados. "
                        "La geometría aún no está ajustada a la red vial."),
        "routes": routes, "trips": [], "stops": stops, "vehicles": [], "readings": [],
        "summary": {"reading_count": 0, "window_report_count": 0,
                    "available_report_count": expected, "trip_count": len(rows),
                    "stop_event_count": sum(zone["count"] for zone in zones.values())},
        "metadata": {"dataset_version": version, "processed_report_count": processed,
                     "source_report_count": expected, "catalog_complete": complete,
                     "generated_at": datetime.now(timezone.utc).isoformat(),
                     "classification": "Uso medido por recorridos completos; no por puntos GPS.",
                     "geometry": "Trazado GPS representativo por ruta.",
                     "road_matching": "Map matching vial pendiente; no se infieren caminos sin GPS.",
                     "refresh_seconds": 300},
    }


def build(output, max_assets=None):
    with _db() as db:
        version, expected = _source_version(db)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        work_path = output.parent / f"route_catalog_work_{version[:12]}.sqlite"
        work = _work_db(work_path)
        try:
            last = _meta(work, "last_asset")
            completed_assets = 0
            while asset := _next_asset(db, last):
                work.execute("BEGIN")
                read, accepted, trips = _process_asset(db, work, asset)
                _set_meta(work, "read", int(_meta(work, "read", 0)) + read)
                _set_meta(work, "accepted", int(_meta(work, "accepted", 0)) + accepted)
                _set_meta(work, "last_asset", asset)
                work.commit()
                completed_assets += 1
                last = asset
                print(f"{asset}: {read:,} GPS, {trips:,} viajes; "
                      f"acumulado {int(_meta(work, 'read', 0)):,}/{expected:,}", flush=True)
                if max_assets and completed_assets >= max_assets:
                    break
            complete = _next_asset(db, last) is None
        finally:
            work.close()
        work = _work_db(work_path)
        try:
            catalog = _build_catalog(work, version, expected, complete)
        finally:
            work.close()
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
                             encoding="utf-8")
        os.replace(temporary, output)
        print(f"Catálogo: {len(catalog['routes']):,} rutas, "
              f"{catalog['summary']['trip_count']:,} viajes; "
              f"completo={complete}; {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/app/exports/route_catalog.json")
    parser.add_argument("--max-assets", type=int, default=None,
                        help="Solo para ensayo; el JSON se marca como incompleto")
    arguments = parser.parse_args()
    build(arguments.output, arguments.max_assets)
