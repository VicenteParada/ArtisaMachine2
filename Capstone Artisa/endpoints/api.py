"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import os
import re
import socket
from threading import Lock
import time
from urllib.parse import parse_qs, urlsplit
import uuid

import pymysql

from app import Handler as LegacyHandler
from common.db import connect_db
from common.http import JsonHandler, RequestError, serve
from common.map_provider import MapDataError, get_map_data
from common.validation import features


STARTED_AT = time.monotonic()
PREDICTION_PATH = re.compile(r"/api/predicciones/([0-9a-f-]{36})")
TELEMETRY_SUMMARY_CACHE = {"expires_at": 0.0, "summary": None}
TELEMETRY_SUMMARY_LOCK = Lock()


def _seconds(value):
    return int(value) if value is not None else 10**9


def _rejections(row):
    try:
        return json.loads(row.get("rechazos_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []


def _telemetry_source(cursor):
    """Prefiere el ETL MongoDB de main y conserva la telemetría anterior como respaldo."""
    cursor.execute("SELECT validos FROM raw_gps_estado WHERE id=1")
    raw_count = int((cursor.fetchone() or {}).get("validos") or 0)
    if raw_count:
        return "raw_gps", raw_count
    cursor.execute("SELECT validos FROM telemetria_etl_estado WHERE id=1")
    legacy_count = int((cursor.fetchone() or {}).get("validos") or 0)
    return "telemetria_raw", legacy_count


def _telemetry_summary(cursor):
    """Resume el histórico sin escanear cientos de miles de filas en cada carga."""
    table, report_count = _telemetry_source(cursor)
    cursor.execute(f"SELECT report_date FROM {table} ORDER BY report_date ASC LIMIT 1")
    first = cursor.fetchone()
    cursor.execute(f"SELECT report_date FROM {table} ORDER BY report_date DESC LIMIT 1")
    last = cursor.fetchone()
    cursor.execute(
        f"SELECT COUNT(*) AS vehiculos FROM "
        f"(SELECT asset_id FROM {table} GROUP BY asset_id) AS flota"
    )
    vehicles = int((cursor.fetchone() or {}).get("vehiculos") or 0)
    cursor.execute(
        f"""SELECT COALESCE(AVG(speed),0) AS velocidad_promedio,
        COALESCE(MAX(speed),0) AS velocidad_maxima
        FROM (SELECT speed FROM {table}
              ORDER BY report_date DESC LIMIT 30000) AS ventana_reciente"""
    )
    speeds = cursor.fetchone() or {}
    return {
        "reportes": report_count,
        "vehiculos": vehicles,
        "desde": first.get("report_date") if first else None,
        "hasta": last.get("report_date") if last else None,
        "velocidad_promedio": speeds.get("velocidad_promedio") or 0,
        "velocidad_maxima": speeds.get("velocidad_maxima") or 0,
        "origen": table,
        "ventana_velocidad": min(report_count, 30000),
    }


def pipeline_state(cursor):
    cursor.execute(
        "SELECT *, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
        "FROM raw_gps_estado WHERE id=1"
    )
    mongo_etl = cursor.fetchone()
    cursor.execute(
        "SELECT *, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
        "FROM etl_estado WHERE id=1"
    )
    legacy_etl = cursor.fetchone()
    cursor.execute(
        "SELECT *, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
        "FROM modelo_estado WHERE id=1"
    )
    model = cursor.fetchone()
    cursor.execute("SELECT * FROM telemetria_etl_estado WHERE id=1")
    legacy_telemetry = cursor.fetchone()
    cursor.execute("SELECT * FROM modelo_telemetria_estado WHERE id=1")
    telemetry_model = cursor.fetchone()

    etl_ready = bool(
        mongo_etl
        and mongo_etl.get("estado") != "error"
        and _seconds(mongo_etl.get("hace_segundos"))
        < int(os.environ.get("ETL_HEALTH_MAX_AGE_SECONDS", "900"))
    )
    model_ready = bool(
        model
        and _seconds(model.get("hace_segundos")) < 120
        and legacy_etl
        and model.get("dataset_version") == legacy_etl.get("dataset_version")
    )
    mongo_has_data = bool(mongo_etl and int(mongo_etl.get("validos") or 0) > 0)
    telemetry = mongo_etl if mongo_has_data else legacy_telemetry

    legacy_payload = None
    if legacy_etl:
        legacy_payload = {
            key: value
            for key, value in legacy_etl.items()
            if key not in ("id", "rechazos_json")
        } | {"rechazos": _rejections(legacy_etl)}

    mongo_payload = None
    if mongo_etl:
        mongo_payload = {
            key: value
            for key, value in mongo_etl.items()
            if key not in ("id", "rechazos_json")
        } | {"rechazos": _rejections(mongo_etl)}

    model_payload = None
    if model:
        model_payload = {
            key: value
            for key, value in model.items()
            if key not in ("id", "metricas_json", "coeficientes_json")
        } | {
            "metricas": json.loads(model["metricas_json"]),
            "coeficientes": json.loads(model["coeficientes_json"]),
        }

    telemetry_model_payload = None
    if telemetry_model:
        telemetry_model_payload = {
            key: value
            for key, value in telemetry_model.items()
            if key not in ("id", "metricas_json", "zonas_json")
        } | {"metricas": json.loads(telemetry_model["metricas_json"])}

    telemetry_payload = None
    if telemetry:
        telemetry_payload = {
            key: value
            for key, value in telemetry.items()
            if key not in ("id", "origen", "rechazos_json", "hace_segundos")
        } | {"rechazos": _rejections(telemetry)}

    return {
        "status": "ok" if etl_ready else "degraded",
        "servicio": "artisa-docker-demo",
        "database": {"engine": "MariaDB", "status": "ok"},
        "componentes": {
            "etl": "ok" if etl_ready else "unavailable",
            "mariadb": "ok",
            "machine": "ok" if model_ready else "unavailable",
            "endpoints": "ok",
            "front": "ok",
        },
        "etl": legacy_payload,
        "etl_mongodb": mongo_payload,
        "modelo": model_payload,
        "modelo_telemetria": telemetry_model_payload,
        "telemetria": telemetry_payload,
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 2),
        "datos": "mongodb_y_telemetria_historica" if mongo_has_data else "telemetria_historica",
    }

class Handler(LegacyHandler):
    respond = JsonHandler.respond
    read_json = JsonHandler.read_json

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            self.respond(200, {"servicio": "artisa-docker-demo", "contenedor": socket.gethostname(),
                               "mensaje": "Endpoints Artisa", "rutas": ["/api/estado", "/api/viajes",
                               "/api/telemetria/resumen", "/api/telemetria", "/api/mapa", "/api/telemetria/mapa-calor", "/api/modelo", "/api/modelo/telemetria",
                               "/api/predicciones"]})
            return
        if path != "/health" and not path.startswith("/api/"):
            super().do_GET()
            return
        if path in ("/api/mapa", "/api/telemetria/mapa-calor"):
            try:
                with connect_db(autocommit=True) as connection, connection.cursor() as cursor:
                    self.respond(200, get_map_data(cursor))
            except MapDataError as error:
                self.respond(503, {"error": str(error)})
            except pymysql.MySQLError as error:
                self.database_error(error)
            return
        try:
            with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
                if path in ("/health", "/api/estado"):
                    state = pipeline_state(cursor)
                    self.respond(503 if path == "/health" and state["status"] != "ok" else 200, state)
                elif path == "/api/resumen":
                    cursor.execute("""SELECT COUNT(*) AS viajes, COUNT(p.viaje_id) AS predicciones,
                        COALESCE(AVG(v.duracion_horas),0) AS duracion_promedio, COUNT(DISTINCT v.destino) AS destinos
                        FROM viajes v JOIN etl_estado e ON e.id=1 AND v.dataset_version=e.dataset_version
                        LEFT JOIN predicciones_viajes p ON p.viaje_id=v.id AND p.dataset_version=v.dataset_version""")
                    self.respond(200, cursor.fetchone())
                elif path == "/api/viajes":
                    try:
                        limit = int(parse_qs(parsed.query).get("limite", ["20"])[0])
                        if not 1 <= limit <= 500:
                            raise ValueError
                    except ValueError:
                        raise RequestError(400, "limite debe ser un entero entre 1 y 500") from None
                    cursor.execute("""SELECT v.id,v.origen,v.destino,v.distancia_km,v.carga_kg,v.paradas,
                        v.duracion_horas,p.duracion_estimada,v.dataset_version
                        FROM viajes v JOIN etl_estado e ON e.id=1 AND v.dataset_version=e.dataset_version
                        LEFT JOIN predicciones_viajes p ON p.viaje_id=v.id AND p.dataset_version=v.dataset_version
                        ORDER BY v.id LIMIT %s""", (limit,))
                    self.respond(200, {"viajes": cursor.fetchall(), "limite": limit})
                elif path == "/api/telemetria/resumen":
                    with TELEMETRY_SUMMARY_LOCK:
                        if (
                            TELEMETRY_SUMMARY_CACHE["summary"] is not None
                            and time.monotonic() < TELEMETRY_SUMMARY_CACHE["expires_at"]
                        ):
                            summary = TELEMETRY_SUMMARY_CACHE["summary"]
                        else:
                            summary = _telemetry_summary(cursor)
                            TELEMETRY_SUMMARY_CACHE.update(
                                expires_at=time.monotonic() + 60, summary=summary
                            )
                    self.respond(200, summary)
                elif path == "/api/telemetria":
                    try:
                        limit = int(parse_qs(parsed.query).get("limite", ["30"])[0])
                        if not 1 <= limit <= 200:
                            raise ValueError
                    except ValueError:
                        raise RequestError(400, "limite debe ser un entero entre 1 y 200") from None
                    telemetry_table, _ = _telemetry_source(cursor)
                    if telemetry_table == "raw_gps":
                        cursor.execute("""SELECT asset_id,asset_name,report_date,event_name,
                            latitude,longitude,speed,NULL AS gps_speed,NULL AS heading,
                            NULL AS ignition FROM raw_gps
                            ORDER BY report_date DESC,report_id DESC LIMIT %s""", (limit,))
                    else:
                        cursor.execute("""SELECT asset_id,asset_name,report_date,event_name,
                            latitude,longitude,speed,gps_speed,heading,ignition
                            FROM telemetria_raw
                            ORDER BY report_date DESC,report_id DESC LIMIT %s""", (limit,))
                    self.respond(200, {
                        "reportes": cursor.fetchall(),
                        "limite": limit,
                        "origen": telemetry_table,
                    })

                elif path == "/api/modelo/telemetria":
                    cursor.execute("SELECT * FROM modelo_telemetria_estado WHERE id=1")
                    model = cursor.fetchone()
                    if model:
                        model = {k: v for k, v in model.items() if k not in ("id", "metricas_json", "zonas_json")} | {
                            "metricas": json.loads(model["metricas_json"]),
                            "zonas": json.loads(model["zonas_json"]),
                        }
                    self.respond(200 if model else 503, model or {"error": "Modelo de telemetria aun no disponible"})
                elif path == "/api/modelo":
                    model = pipeline_state(cursor)["modelo"]
                    self.respond(200 if model else 503, model or {"error": "Modelo aun no disponible"})
                elif match := PREDICTION_PATH.fullmatch(path):
                    cursor.execute("SELECT * FROM solicitudes_prediccion WHERE id=%s", (match[1],))
                    request = cursor.fetchone()
                    self.respond(200 if request else 404, request or {"error": "Prediccion no encontrada"})
                else:
                    self.respond(404, {"error": "Ruta no encontrada"})
        except RequestError as error:
            self.respond(error.status, {"error": error.message})
        except pymysql.MySQLError as error:
            self.database_error(error)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/predicciones":
            super().do_POST()
            return
        try:
            values = features(self.read_json())
            identifier = str(uuid.uuid4())
            with connect_db() as connection, connection.cursor() as cursor:
                state = pipeline_state(cursor)
                if state["componentes"]["machine"] != "ok":
                    raise RequestError(503, "El modelo se esta preparando; intenta nuevamente en unos segundos")
                cursor.execute("""INSERT INTO solicitudes_prediccion
                    (id,distancia_km,carga_kg,paradas,estado,creada_en)
                    VALUES (%s,%s,%s,%s,'pendiente',UTC_TIMESTAMP(6))""",
                    (identifier, values["distancia_km"], values["carga_kg"], values["paradas"]))
            self.respond(202, {"id": identifier, "estado": "pendiente", "consulta": f"/api/predicciones/{identifier}"})
        except RequestError as error:
            self.respond(error.status, {"error": error.message})
        except ValueError as error:
            self.respond(400, {"error": str(error)})
        except pymysql.MySQLError as error:
            self.database_error(error)


if __name__ == "__main__":
    serve(Handler, "Endpoints")
