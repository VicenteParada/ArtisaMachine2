"""API de Artisa sobre las seis tablas compartidas con el equipo."""

from datetime import datetime
import json
import socket
import time
from urllib.parse import parse_qs, urlsplit

import pymysql

from common.db import connect_db
from common.http import JsonHandler, RequestError, serve
from common.map_provider import MapDataError, get_map_data, load_processed_map


STARTED_AT = time.monotonic()


def _limit(query, default, maximum):
    value = query.get("limite", [str(default)])[0]
    try:
        limit = int(value)
    except ValueError as error:
        raise RequestError(400, "limite debe ser un entero") from error
    if not 1 <= limit <= maximum:
        raise RequestError(400, f"limite debe estar entre 1 y {maximum}")
    return limit


def _pipeline_state(cursor):
    cursor.execute(
        "SELECT *, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
        "FROM raw_gps_estado WHERE id=1"
    )
    etl = cursor.fetchone()
    cursor.execute(
        "SELECT *, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
        "FROM modelo_telemetria_estado WHERE id=1"
    )
    model = cursor.fetchone()
    etl_ready = bool(etl and etl["estado"] != "error" and etl["hace_segundos"] is not None
                     and etl["hace_segundos"] < 900)
    model_ready = bool(model and model["hace_segundos"] is not None
                       and model["hace_segundos"] < 900)
    return {
        "status": "ok" if etl_ready and model_ready else "degraded",
        "servicio": "artisa",
        "database": {"engine": "MariaDB", "status": "ok"},
        "componentes": {
            "etl": "ok" if etl_ready else "unavailable",
            "mariadb": "ok",
            "machine": "ok" if model_ready else "unavailable",
            "endpoints": "ok",
        },
        "etl": ({k: v for k, v in etl.items() if k not in ("id", "rechazos_json")} | {
            "rechazos": json.loads(etl["rechazos_json"] or "[]")
        }) if etl else None,
        "modelo_telemetria": ({k: v for k, v in model.items()
                               if k not in ("id", "metricas_json", "zonas_json")} | {
            "metricas": json.loads(model["metricas_json"]),
            "zonas": json.loads(model["zonas_json"]),
        }) if model else None,
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 2),
    }


def _map(cursor):
    return get_map_data(cursor)


def _dashboard(data):
    routes = data["routes"]
    vehicles = data["vehicles"]
    return {
        "calidad": {"validos": data["summary"]["available_report_count"]},
        "operacion": {
            "viajes": len(data["trips"]),
            "paradas": data["summary"]["stop_event_count"],
        },
        "rutas": data["summary"]["route_categories"],
        "flota": {
            "en_movimiento": sum((vehicle.get("speed_kmh") or 0) > 3 for vehicle in vehicles),
            "detenidos": sum((vehicle.get("speed_kmh") or 0) <= 3 for vehicle in vehicles),
            "sin_senal": 0,
        },
        "muestra": {
            "reportes": data["summary"]["window_report_count"],
            "rutas": len(routes),
            "nota": "Rutas y paradas inferidas de la ventana GPS; no son resultados persistidos del modelo.",
        },
    }


class Handler(JsonHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            self.respond(200, {"servicio": "artisa", "contenedor": socket.gethostname(),
                               "mensaje": "API Artisa sobre raw_gps"})
            return
        if path != "/health" and not path.startswith("/api/"):
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        try:
            with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
                if path in ("/health", "/api/estado"):
                    state = _pipeline_state(cursor)
                    self.respond(200, state)
                elif path == "/api/mapa":
                    self.respond(200, _map(cursor))
                elif path == "/api/dashboard":
                    self.respond(200, _dashboard(_map(cursor)))
                elif path == "/api/telemetria/resumen":
                    cursor.execute(
                        "SELECT MIN(report_date) AS desde,MAX(report_date) AS hasta FROM raw_gps"
                    )
                    span = cursor.fetchone() or {}
                    cursor.execute(
                        "SELECT speed FROM raw_gps ORDER BY report_date DESC LIMIT 1000"
                    )
                    speeds = [float(row["speed"]) for row in cursor.fetchall()
                              if row["speed"] is not None]
                    data = _map(cursor)
                    self.respond(200, {
                        "reportes": data["summary"]["available_report_count"],
                        "vehiculos": data["summary"]["vehicle_count"],
                        "desde": span.get("desde"),
                        "hasta": span.get("hasta"),
                        "velocidad_promedio": sum(speeds) / len(speeds) if speeds else 0,
                        "velocidad_maxima": max(speeds, default=0),
                        "velocidad_muestra": len(speeds),
                    })
                elif path == "/api/telemetria":
                    limit = _limit(parse_qs(parsed.query), 30, 500)
                    cursor.execute(
                        """SELECT report_id,asset_id,asset_name,report_date,latitude,longitude,
                        speed,event_name FROM raw_gps ORDER BY report_date DESC LIMIT %s""",
                        (limit,),
                    )
                    self.respond(200, {"reportes": [
                        {**row, "gps_speed": None, "ignition": None}
                        for row in cursor.fetchall()
                    ]})
                elif path == "/api/resumen":
                    simulation = load_processed_map()
                    self.respond(200, {
                        "viajes": len(simulation["trips"]),
                        "predicciones": 0,
                        "destinos": len(simulation["routes"]),
                    })
                elif path == "/api/viajes":
                    limit = _limit(parse_qs(parsed.query), 100, 500)
                    simulation = load_processed_map()
                    names = {route["route_id"]: route["label"] for route in simulation["routes"]}
                    trips = []
                    for trip in simulation["trips"][:limit]:
                        start, end = trip["started_at"], trip["ended_at"]
                        if isinstance(start, str) and isinstance(end, str):
                            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
                            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
                        duration = ((end - start).total_seconds() / 3600
                                    if isinstance(start, datetime) and isinstance(end, datetime)
                                    else 0)
                        trips.append({
                            "id": trip["trip_id"], "origen": trip["vehicle_id"],
                            "destino": names.get(trip["route_id"], trip["route_id"]),
                            "distancia_km": trip["distance_km"],
                            "duracion_horas": max(0, duration),
                            "duracion_estimada": None,
                        })
                    self.respond(200, {"viajes": trips})
                elif path == "/api/faenas":
                    cursor.execute(
                        """SELECT id,external_id,nombre,grupo,area_m2,
                        ST_AsGeoJSON(geocerca) AS geocerca_geojson,creado_en
                        FROM faenas ORDER BY id"""
                    )
                    self.respond(200, {"faenas": [
                        {**{k: v for k, v in row.items() if k != "geocerca_geojson"},
                         "geocerca": json.loads(row["geocerca_geojson"])}
                        for row in cursor.fetchall()
                    ]})
                elif path == "/api/modelo/telemetria":
                    cursor.execute("SELECT * FROM modelo_telemetria_estado WHERE id=1")
                    model = cursor.fetchone()
                    if model:
                        model = {k: v for k, v in model.items()
                                 if k not in ("id", "metricas_json", "zonas_json")} | {
                            "metricas": json.loads(model["metricas_json"]),
                            "zonas": json.loads(model["zonas_json"]),
                        }
                    self.respond(200 if model else 503,
                                 model or {"error": "Modelo de telemetria aun no disponible"})
                else:
                    self.respond(404, {"error": "Ruta no encontrada"})
        except RequestError as error:
            self.respond(error.status, {"error": error.message})
        except MapDataError as error:
            self.respond(503, {"error": str(error)})
        except pymysql.MySQLError as error:
            self.log_error("Error de MariaDB: %s", type(error).__name__)
            self.respond(503, {"error": "MariaDB no disponible"})

    def do_POST(self):
        if urlsplit(self.path).path == "/api/predicciones":
            self.respond(409, {"error": "La prediccion de viajes requiere un modelo y datos que aun no estan en el esquema compartido."})
        else:
            self.respond(404, {"error": "Ruta no encontrada"})


if __name__ == "__main__":
    serve(Handler, "Endpoints")
