"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import os
import socket
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pymysql

from common.db import connect_db
from common.http import JsonHandler, serve


STARTED_AT = time.monotonic()
ETL_STALE_SECONDS = max(60, int(os.getenv("TELEMETRY_INTERVAL_SECONDS", "30")) * 3)


PENDING_RESPONSES = {
    "/api/telemetria": {"status": "pending", "source": "company_api", "reportes": []},
    "/api/modelo/telemetria": {"status": "pending", "message": "Pendiente de integración Machine"},
    "/api/mapa": {
        "schema_version": 1, "source_mode": "PENDING_MACHINE", "snapshot_date": None,
        "routes": [], "trips": [], "vehicles": [], "readings": [],
        "summary": {key: None for key in (
            "trip_count", "vehicle_count", "route_count", "window_report_count",
            "reading_count", "available_report_count")},
        "ranking": {"most_used": None, "least_used": None},
        "metadata": {
            "status": "pending",
            "classification": "Pendiente de procesamiento Machine.",
            "geometry": "Pendiente de procesamiento Machine.",
            "road_matching": "Pendiente de procesamiento Machine.",
            "data_retention": "Sin resultados Machine disponibles.",
        },
    },
    "/api/dashboard": {
        "status": "pending", "calidad": {"validos": None},
        "operacion": {"viajes": None},
        "rutas": {"HIGH": None, "MEDIUM": None, "LOW": None},
    },
    "/api/telemetria/resumen": {
        "status": "pending", "source": "company_api", "reportes": None, "vehiculos": None,
        "desde": None, "hasta": None,
    },
}


def pipeline_state(cursor, detailed=True):
    etl_columns = "*" if detailed else "estado"
    cursor.execute(
        f"SELECT {etl_columns}, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
        "FROM raw_gps_estado WHERE id=1"
    )
    etl = cursor.fetchone()
    etl_ready = bool(etl and etl["estado"] != "error"
                     and etl["hace_segundos"] is not None and 0 <= etl["hace_segundos"] < ETL_STALE_SECONDS)
    state = {
        "status": "ok" if etl_ready else "degraded",
        "servicio": "artisa-api",
        "database": {"engine": "MariaDB", "status": "ok"},
        "componentes": {
            "etl": "ok" if etl_ready else "unavailable",
            "mariadb": "ok",
            "machine": "pending",
            "endpoints": "ok",
        },
    }
    if not detailed:
        return {key: state[key] for key in ("status", "servicio", "componentes")}
    state.update({
        "etl": ({k: v for k, v in etl.items() if k not in ("id", "rechazos_json")} | {
            "rechazos": json.loads(etl["rechazos_json"])
        }) if etl else None,
        "modelo_telemetria": None,
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 2),
    })
    return state


GPS_MAX_DIAS = 14


def gps_range(query):
    """Lee desde/hasta (YYYY-MM-DD, ambos incluidos). Devuelve (desde, hasta, None) o (None, None, error)."""
    params = parse_qs(query)
    try:
        desde = date.fromisoformat(params["desde"][0])
        hasta = date.fromisoformat(params["hasta"][0])
    except (KeyError, ValueError):
        return None, None, "Usa ?desde=YYYY-MM-DD&hasta=YYYY-MM-DD"
    dias = (hasta - desde).days + 1
    if dias < 1:
        return None, None, "hasta tiene que ser igual o posterior a desde"
    if dias > GPS_MAX_DIAS:
        return None, None, f"El rango es de {dias} dias; el maximo es {GPS_MAX_DIAS}"
    return desde, hasta, None


def gps_points(cursor, desde, hasta):
    """Todos los reportes GPS del rango, en columnas compactas para el mapa del navegador.
    t = segundos desde 'desde' 00:00 (hora tal cual viene en report_date); lat/lon x 1e5."""
    # Leer por el índice de fecha evita ordenar millones de filas en MariaDB.
    # La misma deduplicación y el orden por camión/hora se completan en memoria.
    cursor.execute(
        """SELECT asset_id, report_date, latitude, longitude
           FROM raw_gps
           WHERE report_date >= %s AND report_date < %s
           ORDER BY report_date""",
        (desde, hasta + timedelta(days=1)),
    )
    inicio = datetime(desde.year, desde.month, desde.day)
    indices, cam, t, lat, lon = {}, [], [], [], []
    rows = cursor.fetchall()
    rows.sort(key=lambda row: (row["asset_id"], row["report_date"]))
    seen = set()
    for row in rows:
        key = (row["asset_id"], row["report_date"], row["latitude"], row["longitude"])
        if key in seen:
            continue
        seen.add(key)
        cam.append(indices.setdefault(row["asset_id"], len(indices)))
        t.append(round((row["report_date"] - inicio).total_seconds()))
        lat.append(round(row["latitude"] * 1e5))
        lon.append(round(row["longitude"] * 1e5))
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "camiones": list(indices),
            "cam": cam, "t": t, "lat": lat, "lon": lon}


class Handler(JsonHandler):
    def database_error(self, error):
        self.log_error("Error de MariaDB: %s", type(error).__name__)
        self.respond(503, {
            "status": "error",
            "database": {"engine": "MariaDB", "status": "unavailable"},
        })

    def do_POST(self):
        self.respond(404, {"error": "Ruta no encontrada"})

    do_DELETE = do_POST

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            self.respond(200, {"servicio": "artisa-api", "contenedor": socket.gethostname(),
                               "mensaje": "Endpoints Artisa",
                               "rutas": ["/health", "/api/estado", "/api/faenas",
                                         "/api/gps?desde=YYYY-MM-DD&hasta=YYYY-MM-DD", *PENDING_RESPONSES]})
            return
        if path in PENDING_RESPONSES:
            self.respond(200, PENDING_RESPONSES[path])
            return
        if path not in ("/health", "/api/estado", "/api/faenas", "/api/gps"):
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        if path == "/api/gps":
            desde, hasta, error = gps_range(parsed.query)
            if error:
                self.respond(400, {"error": error})
                return
        try:
            with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
                if path in ("/health", "/api/estado"):
                    state = pipeline_state(cursor, detailed=path == "/api/estado")
                    self.respond(503 if path == "/health" and state["status"] != "ok" else 200, state)
                elif path == "/api/faenas":
                    cursor.execute(
                        """SELECT id, external_id, nombre, grupo, area_m2,
                           ST_AsGeoJSON(geocerca) AS geocerca_geojson, creado_en
                           FROM faenas ORDER BY id"""
                    )
                    faenas = [
                        {**{k: v for k, v in row.items() if k != "geocerca_geojson"},
                         "geocerca": json.loads(row["geocerca_geojson"])}
                        for row in cursor.fetchall()
                    ]
                    self.respond(200, {"faenas": faenas})
                elif path == "/api/gps":
                    self.respond(200, gps_points(cursor, desde, hasta))
        except pymysql.MySQLError as error:
            if path == "/health":
                self.log_error("Error de MariaDB: %s", type(error).__name__)
                self.respond(503, {
                    "status": "degraded", "servicio": "artisa-api",
                    "componentes": {"mariadb": "unavailable", "etl": "unavailable",
                                    "machine": "pending", "endpoints": "ok"},
                })
            else:
                self.database_error(error)


if __name__ == "__main__":
    serve(Handler, "Endpoints")
