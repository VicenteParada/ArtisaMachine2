"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import os
import socket
import time
from urllib.parse import urlsplit

import pymysql

from common.db import connect_db
from common.http import JsonHandler, serve


STARTED_AT = time.monotonic()
ETL_STALE_SECONDS = max(60, int(os.getenv("TELEMETRY_INTERVAL_SECONDS", "30")) * 3)


PENDING_RESPONSES = {
    "/api/telemetria": {"status": "pending", "source": "company_api", "reportes": []},
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
    model_columns = "*" if detailed else "id"
    try:
        cursor.execute(
            f"SELECT {model_columns}, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
            "FROM modelo_telemetria_estado WHERE id=1"
        )
        model = cursor.fetchone()
    except pymysql.ProgrammingError as error:
        if error.args[0] != 1146:  # La tabla opcional de Machine aún no existe.
            raise
        model = None
    etl_ready = bool(etl and etl["estado"] != "error"
                     and etl["hace_segundos"] is not None and 0 <= etl["hace_segundos"] < ETL_STALE_SECONDS)
    model_ready = bool(model and model["hace_segundos"] is not None
                       and 0 <= model["hace_segundos"] < 90)
    state = {
        "status": "ok" if etl_ready else "degraded",
        "servicio": "artisa-api",
        "database": {"engine": "MariaDB", "status": "ok"},
        "componentes": {
            "etl": "ok" if etl_ready else "unavailable",
            "mariadb": "ok",
            "machine": "ok" if model_ready else "pending",
            "endpoints": "ok",
        },
    }
    if not detailed:
        return {key: state[key] for key in ("status", "servicio", "componentes")}
    state.update({
        "etl": ({k: v for k, v in etl.items() if k not in ("id", "rechazos_json")} | {
            "rechazos": json.loads(etl["rechazos_json"])
        }) if etl else None,
        "modelo_telemetria": ({k: v for k, v in model.items() if k not in ("id", "metricas_json", "zonas_json")} | {
            "metricas": json.loads(model["metricas_json"])
        }) if model else None,
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 2),
    })
    return state


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
                               "rutas": ["/health", "/api/estado", "/api/faenas", "/api/modelo/telemetria", *PENDING_RESPONSES]})
            return
        if path in PENDING_RESPONSES:
            self.respond(200, PENDING_RESPONSES[path])
            return
        if path not in ("/health", "/api/estado", "/api/faenas", "/api/modelo/telemetria"):
            self.respond(404, {"error": "Ruta no encontrada"})
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
                elif path == "/api/modelo/telemetria":
                    cursor.execute("SELECT * FROM modelo_telemetria_estado WHERE id=1")
                    model = cursor.fetchone()
                    if model:
                        model = {k: v for k, v in model.items() if k not in ("id", "metricas_json", "zonas_json")} | {
                            "metricas": json.loads(model["metricas_json"]),
                            "zonas": json.loads(model["zonas_json"]),
                        }
                    self.respond(200 if model else 503, model or {"error": "Modelo de telemetria aun no disponible"})
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
