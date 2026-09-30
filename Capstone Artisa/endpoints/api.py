"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import socket
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

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
    # DISTINCT quita las filas que la ETL repite, igual que en telemetria_colab.ipynb
    cursor.execute(
        """SELECT DISTINCT asset_id, report_date, latitude, longitude
           FROM raw_gps
           WHERE report_date >= %s AND report_date < %s
           ORDER BY asset_id, report_date""",
        (desde, hasta + timedelta(days=1)),
    )
    inicio = datetime(desde.year, desde.month, desde.day)
    indices, cam, t, lat, lon = {}, [], [], [], []
    for row in cursor.fetchall():
        cam.append(indices.setdefault(row["asset_id"], len(indices)))
        t.append(round((row["report_date"] - inicio).total_seconds()))
        lat.append(round(row["latitude"] * 1e5))
        lon.append(round(row["longitude"] * 1e5))
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "camiones": list(indices),
            "cam": cam, "t": t, "lat": lat, "lon": lon}


class Handler(LegacyHandler):
    respond = JsonHandler.respond
    read_json = JsonHandler.read_json

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            self.respond(200, {"servicio": "artisa-docker-demo", "contenedor": socket.gethostname(),
                               "mensaje": "Endpoints Artisa",
                               "rutas": ["/health", "/api/estado", "/api/faenas", "/api/modelo/telemetria",
                                         "/api/gps?desde=YYYY-MM-DD&hasta=YYYY-MM-DD"]})
            return
        if path != "/health" and not path.startswith("/api/"):
            super().do_GET()
            return
        if path == "/api/gps":
            desde, hasta, error = gps_range(parsed.query)
            if error:
                self.respond(400, {"error": error})
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
                elif path == "/api/gps":
                    self.respond(200, gps_points(cursor, desde, hasta))
                else:
                    self.respond(404, {"error": "Ruta no encontrada"})
        except pymysql.MySQLError as error:
            self.database_error(error)


if __name__ == "__main__":
    serve(Handler, "Endpoints")
