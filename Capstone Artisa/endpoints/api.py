"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import re
import socket
import time
from urllib.parse import parse_qs, urlsplit
import uuid

import pymysql

from app import Handler as LegacyHandler
from common.db import connect_db
from common.http import JsonHandler, RequestError, serve
from common.validation import features


STARTED_AT = time.monotonic()
PREDICTION_PATH = re.compile(r"/api/predicciones/([0-9a-f-]{36})")


def pipeline_state(cursor):
    cursor.execute("SELECT *, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos FROM etl_estado WHERE id=1")
    etl = cursor.fetchone()
    cursor.execute("SELECT *, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos FROM modelo_estado WHERE id=1")
    model = cursor.fetchone()
    etl_ready = bool(etl and etl["hace_segundos"] < 45)
    model_ready = bool(model and model["hace_segundos"] < 15 and etl and model["dataset_version"] == etl["dataset_version"])
    return {
        "status": "ok" if etl_ready and model_ready else "degraded",
        "servicio": "artisa-docker-demo",
        "database": {"engine": "MariaDB", "status": "ok"},
        "componentes": {"etl": "ok" if etl_ready else "unavailable", "mariadb": "ok",
                        "machine": "ok" if model_ready else "unavailable", "endpoints": "ok"},
        "etl": ({k: v for k, v in etl.items() if k not in ("id", "rechazos_json")} | {
            "rechazos": json.loads(etl["rechazos_json"])
        }) if etl else None,
        "modelo": ({k: v for k, v in model.items() if k not in ("id", "metricas_json", "coeficientes_json")} | {
            "metricas": json.loads(model["metricas_json"]), "coeficientes": json.loads(model["coeficientes_json"])
        }) if model else None,
        "uptime_seconds": round(time.monotonic()-STARTED_AT, 2),
        "datos": "sinteticos",
    }


class Handler(LegacyHandler):
    respond = JsonHandler.respond
    read_json = JsonHandler.read_json

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            self.respond(200, {"servicio": "artisa-docker-demo", "contenedor": socket.gethostname(),
                               "mensaje": "Endpoints Artisa", "rutas": ["/api/estado", "/api/viajes", "/api/modelo", "/api/predicciones"]})
            return
        if path != "/health" and not path.startswith("/api/"):
            super().do_GET()
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
