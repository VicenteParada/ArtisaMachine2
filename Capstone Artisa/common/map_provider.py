"""Proveedor del mapa: contrato estable para simulación y telemetría procesada."""

from collections import Counter, defaultdict
from functools import lru_cache
from hashlib import sha1
import json
import os
from pathlib import Path
from threading import Lock
import time

from common.telemetry_map import analyze_telemetry


ROUTE_STYLES = {
    "HIGH": {"color": "#14532d", "label": "Uso alto"},
    "MEDIUM": {"color": "#f97316", "label": "Uso medio"},
    "LOW": {"color": "#dc2626", "label": "Uso bajo"},
}
DEFAULT_SIMULATION_PATH = (
    Path(__file__).resolve().parent.parent / "simulacion" / "mapa_procesado.json"
)
_MAP_CACHE = {"expires_at": 0.0, "data": None}
_MAP_CACHE_LOCK = Lock()


class MapDataError(ValueError):
    """Indica que el proveedor no cumple el contrato de datos procesados."""


def _number(value, field, minimum=None):
    if isinstance(value, bool):
        raise MapDataError(f"{field} debe ser numerico")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise MapDataError(f"{field} debe ser numerico") from error
    if minimum is not None and number < minimum:
        raise MapDataError(f"{field} debe ser mayor o igual a {minimum}")
    return number


def _optional_number(value, field, minimum=None):
    if value is None:
        return None
    return _number(value, field, minimum)


def _integer(value, field, minimum=0):
    number = _number(value, field, minimum)
    if not number.is_integer():
        raise MapDataError(f"{field} debe ser entero")
    return int(number)


def _position(item, prefix):
    latitude = _number(item.get("latitude"), f"{prefix}.latitude")
    longitude = _number(item.get("longitude"), f"{prefix}.longitude")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise MapDataError(f"{prefix} contiene coordenadas fuera de rango")
    return latitude, longitude


def _route(route, seen_ids):
    route_id = str(route.get("route_id") or "").strip()
    if not route_id or route_id in seen_ids:
        raise MapDataError("route_id vacio o duplicado")
    seen_ids.add(route_id)
    level = str(route.get("usage_level") or "").upper()
    if level not in ROUTE_STYLES:
        raise MapDataError(f"{route_id}.usage_level no es valido")
    style = ROUTE_STYLES[level]
    if route.get("color") != style["color"]:
        raise MapDataError(f"{route_id}.color no corresponde a {level}")

    geometry = route.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if (not isinstance(geometry, dict) or geometry.get("type") != "LineString" or not isinstance(coordinates, list)):
        raise MapDataError(f"{route_id}.geometry debe ser LineString")
    if len(coordinates) < 2:
        raise MapDataError(f"{route_id}.geometry requiere al menos dos puntos")
    normalized_coordinates = []
    for index, coordinate in enumerate(coordinates):
        if not isinstance(coordinate, list) or len(coordinate) < 2:
            raise MapDataError(f"{route_id}.geometry.coordinates[{index}] no es valido")
        longitude = _number(coordinate[0], f"{route_id}.longitude")
        latitude = _number(coordinate[1], f"{route_id}.latitude")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise MapDataError(f"{route_id} contiene geometria fuera de rango")
        normalized_coordinates.append([longitude, latitude])

    return {
        "route_id": route_id,
        "label": str(route.get("label") or route_id),
        "usage_count": _integer(route.get("usage_count"), f"{route_id}.usage_count"),
        "unique_vehicle_count": _integer(
            route.get("unique_vehicle_count"), f"{route_id}.unique_vehicle_count"
        ),
        "usage_level": level,
        "usage_label": style["label"],
        "color": style["color"],
        "observed_points": _integer(
            route.get("observed_points", len(normalized_coordinates)),
            f"{route_id}.observed_points",
        ),
        "geometry_source": str(route.get("geometry_source") or "PROCESSED"),
        "map_matching": bool(route.get("map_matching", False)),
        "geometry": {"type": "LineString", "coordinates": normalized_coordinates},
    }


def _trip(trip, route_ids, seen_ids):
    trip_id = str(trip.get("trip_id") or "").strip()
    route_id = str(trip.get("route_id") or "").strip()
    vehicle_id = str(trip.get("vehicle_id") or "").strip()
    if not trip_id or trip_id in seen_ids:
        raise MapDataError("trip_id vacio o duplicado")
    if route_id not in route_ids:
        raise MapDataError(f"{trip_id} referencia una ruta inexistente")
    if not vehicle_id:
        raise MapDataError(f"{trip_id}.vehicle_id esta vacio")
    geometry = trip.get("geometry")
    normalized_geometry = None
    if geometry is not None:
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if geometry.get("type") != "LineString" or not isinstance(coordinates, list):
            raise MapDataError(f"{trip_id}.geometry debe ser LineString")
        if len(coordinates) < 2:
            raise MapDataError(f"{trip_id}.geometry requiere al menos dos puntos")
        normalized_coordinates = []
        for index, coordinate in enumerate(coordinates):
            if not isinstance(coordinate, list) or len(coordinate) < 2:
                raise MapDataError(
                    f"{trip_id}.geometry.coordinates[{index}] no es valido"
                )
            longitude = _number(coordinate[0], f"{trip_id}.longitude")
            latitude = _number(coordinate[1], f"{trip_id}.latitude")
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise MapDataError(f"{trip_id} contiene geometria fuera de rango")
            normalized_coordinates.append([longitude, latitude])
        normalized_geometry = {
            "type": "LineString",
            "coordinates": normalized_coordinates,
        }

    seen_ids.add(trip_id)
    return {
        "trip_id": trip_id,
        "route_id": route_id,
        "vehicle_id": vehicle_id,
        "started_at": trip.get("started_at"),
        "ended_at": trip.get("ended_at"),
        "reading_count": _integer(
            trip.get("reading_count", 0), f"{trip_id}.reading_count"
        ),
        "distance_km": _optional_number(
            trip.get("distance_km"), f"{trip_id}.distance_km", 0
        ),
        "geometry": normalized_geometry,
        "status": str(trip.get("status") or "UNKNOWN"),
    }


def _stop(stop, route_ids, seen_ids):
    stop_id = str(stop.get("stop_id") or "").strip()
    route_id = str(stop.get("route_id") or "").strip()
    if not stop_id or stop_id in seen_ids:
        raise MapDataError("stop_id vacio o duplicado")
    if route_id and route_id not in route_ids:
        raise MapDataError(f"{stop_id} referencia una ruta inexistente")
    seen_ids.add(stop_id)
    latitude, longitude = _position(stop, stop_id)
    return {
        "stop_id": stop_id,
        "label": str(stop.get("label") or stop_id),
        "route_id": route_id or None,
        "latitude": latitude,
        "longitude": longitude,
        "reading_count": _integer(stop.get("reading_count"), f"{stop_id}.reading_count"),
        "vehicle_count": _integer(stop.get("vehicle_count"), f"{stop_id}.vehicle_count"),
        "stop_event_count": _integer(
            stop.get("stop_event_count", 0), f"{stop_id}.stop_event_count"
        ),
        "duration_minutes": _optional_number(
            stop.get("duration_minutes"), f"{stop_id}.duration_minutes", 0
        ),
    }


def _vehicle(vehicle, route_ids, seen_ids):
    vehicle_id = str(vehicle.get("vehicle_id") or "").strip()
    route_id = str(vehicle.get("route_id") or "").strip()
    if not vehicle_id or vehicle_id in seen_ids:
        raise MapDataError("vehicle_id vacio o duplicado")
    if route_id and route_id not in route_ids:
        route_id = ""
    seen_ids.add(vehicle_id)
    latitude, longitude = _position(vehicle, vehicle_id)
    return {
        "vehicle_id": vehicle_id,
        "vehicle_name": str(vehicle.get("vehicle_name") or vehicle_id),
        "route_id": route_id or None,
        "latitude": latitude,
        "longitude": longitude,
        "timestamp": vehicle.get("timestamp"),
        "speed_kmh": _optional_number(
            vehicle.get("speed_kmh"), f"{vehicle_id}.speed_kmh", 0
        ),
        "heading": _optional_number(vehicle.get("heading"), f"{vehicle_id}.heading"),
        "ignition": vehicle.get("ignition"),
        "event": str(vehicle.get("event") or "Sin evento"),
        "driver_name": vehicle.get("driver_name"),
        "driver_last_name": vehicle.get("driver_last_name"),
        "driver_document_number": vehicle.get("driver_document_number"),
    }


def _reading(reading, route_ids, seen_ids):
    reading_id = str(reading.get("reading_id") or "").strip()
    route_id = str(reading.get("route_id") or "").strip()
    if not reading_id or reading_id in seen_ids:
        raise MapDataError("reading_id vacio o duplicado")
    if route_id and route_id not in route_ids:
        route_id = ""
    seen_ids.add(reading_id)
    latitude, longitude = _position(reading, reading_id)
    return {
        "reading_id": reading_id,
        "route_id": route_id or None,
        "latitude": latitude,
        "longitude": longitude,
        "reading_count": _integer(reading.get("reading_count"), f"{reading_id}.reading_count"),
        "vehicle_count": _integer(reading.get("vehicle_count"), f"{reading_id}.vehicle_count"),
    }


def _bounds(routes, stops, vehicles, readings):
    coordinates = [
        coordinate for route in routes for coordinate in route["geometry"]["coordinates"]
    ]
    coordinates.extend([stop["longitude"], stop["latitude"]] for stop in stops)
    coordinates.extend([vehicle["longitude"], vehicle["latitude"]] for vehicle in vehicles)
    coordinates.extend([reading["longitude"], reading["latitude"]] for reading in readings)
    if not coordinates:
        return None
    return {
        "latitude_min": min(coordinate[1] for coordinate in coordinates),
        "latitude_max": max(coordinate[1] for coordinate in coordinates),
        "longitude_min": min(coordinate[0] for coordinate in coordinates),
        "longitude_max": max(coordinate[0] for coordinate in coordinates),
    }


def _validate_usage(routes, trips):
    trips_by_route = Counter(trip["route_id"] for trip in trips)
    vehicles_by_route = defaultdict(set)
    for trip in trips:
        vehicles_by_route[trip["route_id"]].add(trip["vehicle_id"])
    for route in routes:
        route_id = route["route_id"]
        if route["usage_count"] != trips_by_route[route_id]:
            raise MapDataError(f"{route_id}.usage_count no coincide con sus recorridos")
        if route["unique_vehicle_count"] != len(vehicles_by_route[route_id]):
            raise MapDataError(
                f"{route_id}.unique_vehicle_count no coincide con sus recorridos"
            )


def normalize_processed_map(raw):
    """Valida un DTO procesado y calcula solamente totales de presentación."""
    route_ids = set()
    routes = [_route(route, route_ids) for route in raw.get("routes", [])]
    trip_ids = set()
    trips = [_trip(trip, route_ids, trip_ids) for trip in raw.get("trips", [])]
    stop_ids = set()
    stops = [_stop(stop, route_ids, stop_ids) for stop in raw.get("stops", [])]
    vehicle_ids = set()
    vehicles = [_vehicle(vehicle, route_ids, vehicle_ids) for vehicle in raw.get("vehicles", [])]
    reading_ids = set()
    readings = [_reading(reading, route_ids, reading_ids) for reading in raw.get("readings", [])]
    if not routes and not vehicles and not readings:
        raise MapDataError("El proveedor del mapa no contiene datos geograficos")
    _validate_usage(routes, trips)

    ordered_routes = sorted(
        routes,
        key=lambda route: (
            -route["usage_count"], -route["unique_vehicle_count"], route["route_id"]
        ),
    )
    categories = {
        level: sum(route["usage_level"] == level for route in routes)
        for level in ROUTE_STYLES
    }
    source_summary = raw.get("summary") or {}
    calculated_readings = sum(reading["reading_count"] for reading in readings)
    reading_count = int(source_summary.get("reading_count", calculated_readings))
    window_report_count = int(source_summary.get("window_report_count", reading_count))
    summary = {
        "reading_count": reading_count,
        "window_report_count": window_report_count,
        "available_report_count": int(
            source_summary.get("available_report_count", window_report_count)
        ),
        "trip_count": len(trips),
        "route_count": len(routes),
        "vehicle_count": len(vehicles),
        "stop_count": len(stops),
        "stop_event_count": int(source_summary.get("stop_event_count", 0)),
        "route_categories": categories,
    }
    return {
        "schema_version": str(raw.get("schema_version") or "1.0"),
        "source_mode": str(raw.get("source_mode") or "PROCESSED"),
        "snapshot_date": raw.get("snapshot_date"),
        "description": str(raw.get("description") or ""),
        "routes": ordered_routes,
        "trips": trips,
        "stops": stops,
        "vehicles": vehicles,
        "readings": readings,
        "summary": summary,
        "bounds": _bounds(routes, stops, vehicles, readings),
        "ranking": {
            "most_used": ordered_routes[0] if ordered_routes else None,
            "least_used": min(
                routes,
                key=lambda route: (
                    route["usage_count"], route["unique_vehicle_count"], route["route_id"]
                ),
                default=None,
            ),
        },
        "metadata": raw.get("metadata") or {},
    }


def load_processed_map(path=None):
    """Carga el contrato preparado en archivo, útil como simulación aislada."""
    source = Path(path or os.environ.get("MAP_SIMULATION_FILE") or DEFAULT_SIMULATION_PATH)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MapDataError(f"No se pudo cargar la simulacion: {error}") from error
    return normalize_processed_map(raw)


def _telemetry_contract(analysis, available_report_count, refresh_seconds):
    routes = []
    for feature in analysis["rutas"]:
        properties = feature["properties"]
        routes.append({
            "route_id": properties["route_id"],
            "label": properties["label"],
            "usage_count": properties["usage_count"],
            "unique_vehicle_count": properties["unique_vehicle_count"],
            "usage_level": properties["usage_level"],
            "color": properties["color"],
            "observed_points": properties["observed_points"],
            "geometry_source": properties["geometry_source"],
            "map_matching": properties["map_matching"],
            "geometry": feature["geometry"],
        })

    vehicles = [{
        "vehicle_id": vehicle["asset_id"],
        "vehicle_name": vehicle["asset_name"],
        "route_id": vehicle.get("route_id"),
        "latitude": vehicle["latitud"],
        "longitude": vehicle["longitud"],
        "timestamp": vehicle["report_date"],
        "speed_kmh": vehicle["velocidad"],
        "heading": vehicle["rumbo"],
        "ignition": vehicle["encendido"],
        "event": vehicle["evento"],
        "driver_name": vehicle.get("driver_name"),
        "driver_last_name": vehicle.get("driver_last_name"),
        "driver_document_number": vehicle.get("driver_document_number"),
    } for vehicle in analysis["vehiculos_actuales"]]

    readings = []
    for cell in analysis["celdas"]:
        identity = f"{cell['latitud']:.5f}|{cell['longitud']:.5f}"
        readings.append({
            "reading_id": "CELL_" + sha1(identity.encode("utf-8")).hexdigest()[:10].upper(),
            "route_id": None,
            "latitude": cell["latitud"],
            "longitude": cell["longitud"],
            "reading_count": cell["reportes"],
            "vehicle_count": cell["vehiculos"],
        })

    latest = analysis.get("ultima_posicion")
    snapshot_date = latest.date().isoformat() if latest is not None else None
    summary = analysis["resumen"]
    return {
        "schema_version": "1.2",
        "source_mode": "TELEMETRY_PROCESSED_SIMULATION",
        "snapshot_date": snapshot_date,
        "description": (
            "Simulación procesada desde la telemetría existente en MariaDB. "
            "Cada recorrido se registra como trip y se asocia a una route equivalente."
        ),
        "routes": routes,
        "trips": analysis["recorridos"],
        "stops": analysis["paradas_geograficas"],
        "vehicles": vehicles,
        "readings": readings,
        "summary": {
            "reading_count": summary["reportes"],
            "window_report_count": summary["filas_ventana"],
            "available_report_count": available_report_count,
            "stop_event_count": summary["paradas"],
        },
        "metadata": {
            "classification": (
                "usage_count es la cantidad de trips observados asociados a cada route_id; "
                "reading_count no participa en HIGH, MEDIUM o LOW."
            ),
            "geometry": (
                "Cada LineString sigue la secuencia cronológica de latitude/longitude "
                "del recorrido representativo."
            ),
            "geometry_source": "GPS_TRACE_FILTERED",
            "route_grouping": analysis["metodo"].get("agrupacion_ruta"),
            "road_matching": (
                "La traza está guiada por GPS real; el ajuste vial exacto queda pendiente "
                "de un motor de map matching On-Premise."
            ),
            "data_retention": (
                "La ventana del mapa no borra ni reemplaza el histórico completo de MariaDB."
            ),
            "available_report_count": available_report_count,
            "window_report_count": summary["filas_ventana"],
            "latest_position": latest,
            "refresh_seconds": refresh_seconds,
        },
    }


def _environment_integer(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise MapDataError(f"{name} debe ser entero") from error
    if not minimum <= value <= maximum:
        raise MapDataError(f"{name} debe estar entre {minimum} y {maximum}")
    return value


def _telemetry_source(cursor):
    """Elige la telemetría importada por main y usa el histórico anterior como respaldo."""
    cursor.execute("SELECT validos FROM raw_gps_estado WHERE id=1")
    raw_count = int((cursor.fetchone() or {}).get("validos") or 0)
    if raw_count:
        return "raw_gps", raw_count
    cursor.execute("SELECT validos FROM telemetria_etl_estado WHERE id=1")
    legacy_count = int((cursor.fetchone() or {}).get("validos") or 0)
    return "telemetria_raw", legacy_count



def _load_persisted_map(cursor, available_report_count, refresh_seconds):
    """Lee resultados analíticos guardados; evita recalcular rutas desde GPS en cada petición."""
    cursor.execute("SELECT COUNT(*) AS cantidad FROM rutas_inferidas")
    if int((cursor.fetchone() or {}).get("cantidad") or 0) == 0:
        return None
    cursor.execute(
        """SELECT route_id,nombre,cantidad_viajes,cantidad_vehiculos,usage_level,color,
        geometria_json,geometry_source,map_matching_estado FROM rutas_inferidas
        ORDER BY cantidad_viajes DESC,route_id"""
    )
    routes = []
    for row in cursor.fetchall():
        routes.append({
            "route_id": row["route_id"],
            "label": row["nombre"],
            "usage_count": row["cantidad_viajes"],
            "unique_vehicle_count": row["cantidad_vehiculos"],
            "usage_level": row["usage_level"],
            "color": row["color"],
            "observed_points": 0,
            "geometry_source": row["geometry_source"],
            "map_matching": row["map_matching_estado"] == "completado",
            "geometry": json.loads(row["geometria_json"]),
        })
    cursor.execute(
        """SELECT viaje_id,route_id,asset_id,inicio,termino,puntos_gps,distancia_km,
        geometria_json,estado FROM viajes_telemetria ORDER BY inicio,viaje_id"""
    )
    trips = [{
        "trip_id": row["viaje_id"], "route_id": row["route_id"],
        "vehicle_id": row["asset_id"], "started_at": row["inicio"],
        "ended_at": row["termino"], "reading_count": row["puntos_gps"],
        "distance_km": row["distancia_km"], "geometry": json.loads(row["geometria_json"]),
        "status": row["estado"].upper(),
    } for row in cursor.fetchall()]
    observed = defaultdict(int)
    for trip in trips:
        observed[trip["route_id"]] += int(trip["reading_count"] or 0)
    for route in routes:
        route["observed_points"] = observed[route["route_id"]]

    cursor.execute(
        """SELECT parada_id,viaje_id,latitude,longitude,duracion_minutos,zona,tipo
        FROM paradas_telemetria ORDER BY inicio DESC LIMIT 500"""
    )
    stops = [{
        "stop_id": row["parada_id"], "label": row["zona"] or row["tipo"],
        "route_id": None, "latitude": row["latitude"], "longitude": row["longitude"],
        "reading_count": 1, "vehicle_count": 1, "stop_event_count": 1,
        "duration_minutes": row["duracion_minutos"],
    } for row in cursor.fetchall()]
    cursor.execute(
        """SELECT asset_id,asset_name,report_date,latitude,longitude,speed,gps_speed,
        heading,ignition,event_name,driver_name,driver_last_name,driver_document_number
        FROM vehiculos_ultima_posicion ORDER BY asset_id"""
    )
    vehicles = [{
        "vehicle_id": row["asset_id"], "vehicle_name": row["asset_name"] or row["asset_id"],
        "route_id": None, "latitude": row["latitude"], "longitude": row["longitude"],
        "timestamp": row["report_date"],
        "speed_kmh": row["gps_speed"] if row["gps_speed"] is not None else row["speed"],
        "heading": row["heading"], "ignition": row["ignition"],
        "event": row["event_name"] or "Sin evento", "driver_name": row["driver_name"],
        "driver_last_name": row["driver_last_name"],
        "driver_document_number": row["driver_document_number"],
    } for row in cursor.fetchall()]
    snapshot = max((vehicle["timestamp"] for vehicle in vehicles), default=None)
    return normalize_processed_map({
        "schema_version": "1.3",
        "source_mode": "PERSISTED_ANALYTICS",
        "snapshot_date": snapshot.date().isoformat() if snapshot else None,
        "description": "Rutas, viajes y paradas persistidos por el procesador analítico.",
        "routes": routes, "trips": trips, "stops": stops, "vehicles": vehicles,
        "readings": [],
        "summary": {
            "reading_count": available_report_count,
            "window_report_count": sum(int(trip["reading_count"] or 0) for trip in trips),
            "available_report_count": available_report_count,
            "stop_event_count": len(stops),
        },
        "metadata": {
            "classification": "Clasificación global persistida por ruta completa.",
            "geometry": "Geometría persistida; OSRM cuando está configurado o traza GPS filtrada.",
            "geometry_source": "PERSISTED",
            "route_grouping": "Agrupación geoespacial explícita fuera de las peticiones HTTP.",
            "road_matching": "Estado guardado individualmente en cada ruta.",
            "data_retention": "Los resultados derivados no sustituyen raw_gps.",
            "available_report_count": available_report_count,
            "window_report_count": sum(int(trip["reading_count"] or 0) for trip in trips),
            "latest_position": snapshot,
            "refresh_seconds": refresh_seconds,
        },
    })

def load_telemetry_map(cursor):
    """Lee una ventana indexada y conserva la última posición de toda la flota."""
    max_reports = _environment_integer("MAP_MAX_REPORTS", 10000, 1000, 100000)
    refresh_seconds = _environment_integer("MAP_REFRESH_SECONDS", 300, 30, 3600)
    telemetry_table, available_report_count = _telemetry_source(cursor)
    if telemetry_table == "raw_gps":
        persisted = _load_persisted_map(cursor, available_report_count, refresh_seconds)
        if persisted is not None:
            persisted["metadata"]["telemetry_table"] = telemetry_table
            return persisted

    if telemetry_table == "raw_gps":
        cursor.execute(
            """SELECT report_id,asset_id,asset_name,report_date,latitude,longitude,
            speed,gps_speed,heading,ignition,event_name,driver_name,driver_last_name,
            driver_document_number FROM raw_gps
            ORDER BY report_date DESC,report_id DESC LIMIT %s""",
            (max_reports,),
        )
        rows = cursor.fetchall()
        cursor.execute(
            """SELECT report_id,asset_id,asset_name,report_date,latitude,longitude,
            speed,gps_speed,heading,ignition,event_name,driver_name,driver_last_name,
            driver_document_number FROM vehiculos_ultima_posicion
            ORDER BY asset_id"""
        )
        current_rows = cursor.fetchall()
        if not current_rows:
            cursor.execute(
                """SELECT t.report_id,t.asset_id,t.asset_name,t.report_date,t.latitude,t.longitude,
                t.speed,t.gps_speed,t.heading,t.ignition,t.event_name,t.driver_name,
                t.driver_last_name,t.driver_document_number FROM raw_gps t
                JOIN (SELECT asset_id,MAX(report_date) AS report_date FROM raw_gps
                      GROUP BY asset_id) latest
                  ON latest.asset_id=t.asset_id AND latest.report_date=t.report_date
                ORDER BY t.asset_id,t.report_id"""
            )
            current_rows = cursor.fetchall()
    else:
        cursor.execute(
            """SELECT report_id,asset_id,asset_name,report_date,latitude,longitude,
            speed,gps_speed,heading,ignition,event_name FROM telemetria_raw
            ORDER BY report_date DESC,report_id DESC LIMIT %s""",
            (max_reports,),
        )
        rows = cursor.fetchall()
        cursor.execute(
            """SELECT t.report_id,t.asset_id,t.asset_name,t.report_date,t.latitude,t.longitude,
            t.speed,t.gps_speed,t.heading,t.ignition,t.event_name
            FROM telemetria_raw t
            JOIN (SELECT asset_id,MAX(report_date) AS report_date FROM telemetria_raw
                  GROUP BY asset_id) latest
              ON latest.asset_id=t.asset_id AND latest.report_date=t.report_date
            ORDER BY t.asset_id,t.report_id"""
        )
        current_rows = cursor.fetchall()


    if not rows:
        raise MapDataError("MariaDB aun no contiene telemetria para procesar")

    analysis = analyze_telemetry(rows, current_rows=current_rows)
    data = normalize_processed_map(
        _telemetry_contract(analysis, available_report_count, refresh_seconds)
    )
    data["metadata"]["telemetry_table"] = telemetry_table
    return data

@lru_cache(maxsize=1)
def _simulation_map():
    return load_processed_map()


def clear_map_cache():
    with _MAP_CACHE_LOCK:
        _MAP_CACHE.update(expires_at=0.0, data=None)
    _simulation_map.cache_clear()


def get_map_data(cursor=None):
    """Resuelve el proveedor sin acoplar el frontend a la fuente real o simulada."""
    source = os.environ.get("MAP_DATA_SOURCE", "telemetry").strip().casefold()
    if source == "simulation":
        return _simulation_map()
    if source != "telemetry":
        raise MapDataError(f"MAP_DATA_SOURCE={source!r} no tiene un proveedor registrado")
    if cursor is None:
        raise MapDataError("El proveedor telemetry requiere una conexion a MariaDB")

    refresh_seconds = _environment_integer("MAP_REFRESH_SECONDS", 300, 30, 3600)
    with _MAP_CACHE_LOCK:
        if _MAP_CACHE["data"] is not None and time.monotonic() < _MAP_CACHE["expires_at"]:
            return _MAP_CACHE["data"]
        data = load_telemetry_map(cursor)
        _MAP_CACHE.update(
            expires_at=time.monotonic() + refresh_seconds,
            data=data,
        )
        return data


