"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import socket
import time
from urllib.parse import urlsplit

import pymysql

from app import Handler as LegacyHandler
from common.db import connect_db
from common.http import JsonHandler, serve


STARTED_AT = time.monotonic()


def pipeline_state(cursor):
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
    etl_ready = bool(etl and etl["estado"] != "error" and etl["hace_segundos"] < 90)
    model_ready = bool(model and model["hace_segundos"] < 90)
    return {
        "status": "ok" if etl_ready and model_ready else "degraded",
        "servicio": "artisa-docker-demo",
        "database": {"engine": "MariaDB", "status": "ok"},
        "componentes": {
            "etl": "ok" if etl_ready else "unavailable",
            "mariadb": "ok",
            "machine": "ok" if model_ready else "unavailable",
            "endpoints": "ok",
        },
        "etl": ({k: v for k, v in etl.items() if k not in ("id", "rechazos_json")} | {
            "rechazos": json.loads(etl["rechazos_json"])
        }) if etl else None,
        "modelo_telemetria": ({k: v for k, v in model.items() if k not in ("id", "metricas_json", "zonas_json")} | {
            "metricas": json.loads(model["metricas_json"])
        }) if model else None,
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 2),
    }


class Handler(LegacyHandler):
    respond = JsonHandler.respond
    read_json = JsonHandler.read_json

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            self.respond(200, {"servicio": "artisa-docker-demo", "contenedor": socket.gethostname(),
                               "mensaje": "Endpoints Artisa",
                               "rutas": ["/health", "/api/estado", "/api/faenas", "/api/modelo/telemetria"]})
            return
        if path != "/health" and not path.startswith("/api/"):
            super().do_GET()
            return
        try:
            with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
                if path in ("/health", "/api/estado"):
                    state = pipeline_state(cursor)
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
                else:
                    self.respond(404, {"error": "Ruta no encontrada"})
        except pymysql.MySQLError as error:
            self.database_error(error)


if __name__ == "__main__":
    serve(Handler, "Endpoints")
