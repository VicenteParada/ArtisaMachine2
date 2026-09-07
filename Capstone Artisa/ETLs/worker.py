"""Origen (CSV) -> ETL -> MariaDB. Relee el archivo cada 15 segundos."""

import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from common.db import connect_db, initialize_db
from common.http import JsonHandler, serve
from ETLs.telemetry import TelemetryConfig, record_error, run_once as run_telemetry_once
from ETLs.transform import transform


SOURCE = Path(os.environ.get("SOURCE_FILE", "/origen/viajes.csv"))
INTERVAL = int(os.environ.get("ETL_INTERVAL_SECONDS", "15"))
STATE = {"last_success": 0.0, "error": None}
TELEMETRY_ENABLED = os.environ.get("TELEMETRY_ENABLED", "0").strip().lower() in ("1", "true", "yes")
TELEMETRY_INTERVAL = int(os.environ.get("TELEMETRY_INTERVAL_SECONDS", "30"))
TELEMETRY_STATE = {"enabled": TELEMETRY_ENABLED, "last_success": 0.0, "error": None,
                   "estado": "deshabilitado" if not TELEMETRY_ENABLED else "pendiente"}


def run_once():
    raw = SOURCE.read_bytes()
    if len(raw) > 5_000_000:
        raise ValueError("El CSV de demostracion supera 5 MB")
    rows, rejected, version = transform(raw)
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT dataset_version FROM etl_estado WHERE id=1")
        previous = cursor.fetchone()
        if previous and previous["dataset_version"] == version:
            cursor.execute("UPDATE etl_estado SET ultima_revision=UTC_TIMESTAMP(6) WHERE id=1")
        else:
            cursor.executemany(
                """INSERT INTO viajes
                (id,origen,destino,distancia_km,carga_kg,paradas,duracion_horas,dataset_version,actualizado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE origen=VALUES(origen),destino=VALUES(destino),
                distancia_km=VALUES(distancia_km),carga_kg=VALUES(carga_kg),paradas=VALUES(paradas),
                duracion_horas=VALUES(duracion_horas),dataset_version=VALUES(dataset_version),
                actualizado_en=VALUES(actualizado_en)""",
                [(r["id"], r["origen"], r["destino"], r["distancia_km"], r["carga_kg"],
                  r["paradas"], r["duracion_horas"], version) for r in rows],
            )
            cursor.execute(
                """INSERT INTO etl_estado
                (id,dataset_version,origen,leidos,validos,rechazados,rechazos_json,ultima_carga,ultima_revision)
                VALUES (1,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE dataset_version=VALUES(dataset_version),origen=VALUES(origen),
                leidos=VALUES(leidos),validos=VALUES(validos),rechazados=VALUES(rechazados),
                rechazos_json=VALUES(rechazos_json),ultima_carga=VALUES(ultima_carga),
                ultima_revision=VALUES(ultima_revision)""",
                (version, SOURCE.name, len(rows)+len(rejected), len(rows), len(rejected),
                 json.dumps(rejected[:50], ensure_ascii=False)),
            )
            print(f"ETL: {len(rows)} viajes cargados, {len(rejected)} descartados; version {version[:12]}", flush=True)
        connection.commit()


def loop():
    initialized = False
    while True:
        try:
            if not initialized:
                initialize_db()
                initialized = True
            run_once()
            STATE.update(last_success=time.monotonic(), error=None)
        except Exception as error:
            # Una caída de DB o un CSV inválido no mata el trabajador; vuelve a intentar.
            STATE["error"] = type(error).__name__
            print(f"ETL: carga fallida ({type(error).__name__}); se reintentara", flush=True)
        time.sleep(INTERVAL)


def telemetry_loop():
    try:
        initialize_db()
        config = TelemetryConfig.from_env()
    except ValueError as error:
        TELEMETRY_STATE.update(error=str(error), estado="error")
        print(f"ETL telemetria: configuracion invalida ({error})", flush=True)
        return
    while True:
        try:
            result = run_telemetry_once(config)
            TELEMETRY_STATE.update(last_success=time.monotonic(), error=None, **result)
            if result["recibidos"]:
                print(
                    f"ETL telemetria: {result['validos']} guardados, {result['rechazados']} "
                    f"descartados; total {result['total_validos']} ({result['estado']})",
                    flush=True,
                )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            TELEMETRY_STATE.update(error=message, estado="error")
            try:
                record_error(message)
            except Exception:
                pass
            print(f"ETL telemetria: ciclo fallido ({message})", flush=True)
        time.sleep(TELEMETRY_INTERVAL)


class Handler(JsonHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        ready = STATE["error"] is None and time.monotonic() - STATE["last_success"] < max(60, INTERVAL*3)
        telemetry = {key: value for key, value in TELEMETRY_STATE.items() if key != "last_success"}
        self.respond(200 if ready else 503, {"servicio": "etl", "status": "ok" if ready else "unavailable",
                                                "telemetria": telemetry})


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    if TELEMETRY_ENABLED:
        threading.Thread(target=telemetry_loop, daemon=True).start()
    serve(Handler, "ETL")
