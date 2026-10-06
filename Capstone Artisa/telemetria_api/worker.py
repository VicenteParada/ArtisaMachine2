"""API de la empresa -> MariaDB (telemetria_api). Diseñado para sobrevivir cortes.

Garantías:
- Nunca se cae por un fallo externo: Wi-Fi, API, respuesta inválida o MariaDB
  caída se registran y se reintentan con espera creciente (5 s, 10 s, 20 s... hasta
  COMPANY_API_RETRY_MAX_SECONDS), así al volver la red se reconecta en menos de un minuto.
- No pierde datos: el cursor se guarda en la misma transacción que cada página.
  Al reconectar continúa desde el último reporte guardado y recupera todo el
  período desconectado, página por página.
- No duplica: report_id es la clave primaria (INSERT IGNORE).
- Relee los últimos COMPANY_API_OVERLAP_SECONDS en cada ciclo para capturar
  reportes que el GPS envía con atraso.
- Si el hilo de trabajo muere o se queda colgado, el proceso termina y Docker lo
  reinicia (restart: unless-stopped).
"""

from datetime import datetime, timedelta, timezone
import json
import os
import random
import threading
import time
from urllib.parse import urlsplit

from common.db import connect_db, initialize_db
from common.http import JsonHandler, serve
from common.vehiculos import registrar_nuevos
from telemetria_api.source import (
    ApiConfig, ApiSourceError, build_clock_query, build_query, detect_offset, fetch_rows, native_key, transform_rows,
)


STATE = {
    "estado": "iniciando", "error": None, "fallos_consecutivos": 0,
    "ultimo_exito": None, "advertencia": None, "configurada": None,
}
HEARTBEAT = {"value": time.monotonic()}
LOCK = threading.Lock()
WATCHDOG_SECONDS = int(os.environ.get("COMPANY_API_WATCHDOG_SECONDS", "900"))


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _set_state(**changes):
    with LOCK:
        STATE.update(changes)


def backoff_seconds(failures, config):
    base = config.retry_min_seconds if config else 5
    ceiling = config.retry_max_seconds if config else 60
    delay = min(ceiling, base * (2 ** max(0, failures - 1)))
    return delay * random.uniform(0.8, 1.2)


def _sleep(seconds):
    """Duerme en tramos cortos manteniendo vivo el latido del watchdog."""
    end = time.monotonic() + seconds
    while True:
        HEARTBEAT["value"] = time.monotonic()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(5.0, remaining))


# --- Persistencia del estado ----------------------------------------------------

def load_state(config):
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM telemetria_api_estado WHERE id=1 FOR UPDATE")
        state = cursor.fetchone()
        if not state:
            cursor.execute(
                """INSERT INTO telemetria_api_estado
                (id,origen,cursor_fecha,cursor_id,estado,ultimo_error,fallos_consecutivos,
                 leidos,validos,rechazados,rechazos_json,ultima_revision)
                VALUES (1,%s,NULL,NULL,'iniciando',NULL,0,0,0,0,'[]',UTC_TIMESTAMP(6))""",
                (config.origin,),
            )
            cursor.execute("SELECT * FROM telemetria_api_estado WHERE id=1")
            state = cursor.fetchone()
        elif state["origen"] != config.origin:
            # Cambió la IP o el puerto de la API (p. ej. DHCP del Wi-Fi). Los datos son los
            # mismos, así que se conserva el cursor: no se pierde ni se repite nada.
            cursor.execute("UPDATE telemetria_api_estado SET origen=%s WHERE id=1", (config.origin,))
        connection.commit()
        return state


def save_page(accepted, rejected, raw_count, new_cursor, advertencia):
    """Guarda filas, vehículos nuevos, cursor y contadores en una sola transacción."""
    values = [(
        row["report_id"], row["asset_id"], row["asset_name"], row["report_date"],
        row["latitude"], row["longitude"], row["speed"], row["heading"],
        None if row["ignition"] is None else int(row["ignition"]), row["event_name"],
    ) for row in accepted]
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT cursor_fecha, cursor_id, rechazos_json FROM telemetria_api_estado WHERE id=1 FOR UPDATE")
        current = cursor.fetchone() or {}
        inserted = 0
        if values:
            inserted = cursor.executemany(
                """INSERT IGNORE INTO telemetria_api
                (report_id,asset_id,asset_name,report_date,latitude,longitude,speed,heading,
                 ignition,event_name,recibido_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))""",
                values,
            ) or 0
            registrar_nuevos(cursor, {row["asset_id"]: row["asset_name"] for row in accepted})
        stored = (current.get("cursor_fecha"), current.get("cursor_id") or "")
        has_stored = stored[0] is not None
        # Los rechazos dentro de la ventana releída ya se contaron antes.
        fresh_rejections = [item for item in rejected
                            if not has_stored or item["clave"] is None or item["clave"] > stored]
        try:
            history = json.loads(current.get("rechazos_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            history = []
        history = (history + [{k: v for k, v in item.items() if k != "clave"}
                              for item in fresh_rejections])[-50:]
        # El cursor nunca retrocede.
        cursor_value = new_cursor if (new_cursor and (not has_stored or new_cursor > stored)) else stored
        latest = max((row["report_date"] for row in accepted), default=None)
        cursor.execute(
            """UPDATE telemetria_api_estado SET cursor_fecha=%s,cursor_id=%s,estado='conectado',
            ultimo_error=NULL,fallos_consecutivos=0,leidos=leidos+%s,validos=validos+%s,
            rechazados=rechazados+%s,rechazos_json=%s,ultimo_exito=UTC_TIMESTAMP(6),
            ultimo_dato=CASE WHEN %s IS NULL THEN ultimo_dato
                             WHEN ultimo_dato IS NULL OR %s > ultimo_dato THEN %s ELSE ultimo_dato END,
            desconectado_desde=NULL,advertencia=%s,ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""",
            (cursor_value[0], cursor_value[1] or None, raw_count, inserted, len(fresh_rejections),
             json.dumps(history, ensure_ascii=False, default=str), latest, latest, latest, advertencia),
        )
        connection.commit()
    return inserted


def record_failure(kind, message):
    """Mejor esfuerzo: si MariaDB tampoco responde, el estado queda solo en memoria."""
    safe = " ".join(str(message).split())[:255]
    try:
        with connect_db() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE telemetria_api_estado SET estado=%s,ultimo_error=%s,
                fallos_consecutivos=fallos_consecutivos+1,
                desconectado_desde=COALESCE(desconectado_desde,UTC_TIMESTAMP(6)),
                ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""",
                (kind, safe),
            )
    except Exception:
        pass


def record_unconfigured():
    try:
        with connect_db() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO telemetria_api_estado
                (id,origen,estado,fallos_consecutivos,leidos,validos,rechazados,rechazos_json,ultima_revision)
                VALUES (1,'sin configurar','sin_configurar',0,0,0,0,'[]',UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE estado='sin_configurar',ultimo_error=NULL,
                ultima_revision=UTC_TIMESTAMP(6)""")
    except Exception:
        pass


# --- Ciclo --------------------------------------------------------------------

def timezone_warning(config, offset):
    if offset is None or config.timezone_name == "auto":
        return None
    from zoneinfo import ZoneInfo
    expected = datetime.now(ZoneInfo(config.timezone_name)).utcoffset()
    if abs((expected - offset).total_seconds()) >= 15 * 60:
        hours = offset.total_seconds() / 3600
        return (f"El reloj del origen parece UTC{hours:+.2g} pero COMPANY_API_TIMEZONE="
                f"{config.timezone_name}; las horas mostradas podrían estar corridas")
    return None


def run_cycle(config, fetch=fetch_rows, save=save_page, load=load_state, now=_utc_now):
    """Lee desde el cursor (menos la ventana de relectura) hasta ponerse al día.
    Devuelve True si quedó al día y False si aún quedan páginas atrasadas."""
    state = load(config)
    stored_date = state.get("cursor_fecha")
    if stored_date is None and config.start_utc is not None:
        # Primera vez con COMPANY_API_START: se pregunta la hora del origen para
        # traducir el inicio a su reloj y recuperar todo desde esa fecha.
        offset = detect_offset(fetch(config, build_clock_query(config)), now())
        if offset is None:
            raise ApiSourceError("respuesta_invalida", "La API no informó su hora (NOW)")
        after, since = None, config.start_utc + offset
    elif stored_date is None:
        after, since = None, None  # primera vez: ventana inicial del origen
    elif config.overlap_seconds:
        after, since = None, stored_date - timedelta(seconds=config.overlap_seconds)
    else:
        after, since = (stored_date, state.get("cursor_id") or ""), None
    for _page in range(config.max_pages_per_cycle):
        HEARTBEAT["value"] = time.monotonic()
        rows = fetch(config, build_query(config, after=after, since=since))
        offset = detect_offset(rows, now())
        accepted, rejected = transform_rows(rows, config, offset)
        keys = [key for key in (native_key(row) for row in rows) if key is not None]
        if rows and not keys:
            raise ApiSourceError("respuesta_invalida", "Ninguna fila trae report_id y fecha legibles")
        page_cursor = max(keys) if keys else None
        warning = timezone_warning(config, offset)
        save(accepted, rejected, len(rows), page_cursor, warning)
        _set_state(estado="conectado", error=None, fallos_consecutivos=0,
                   ultimo_exito=now().isoformat() + "Z", advertencia=warning)
        if len(rows) < config.batch_size:
            return True
        after, since = page_cursor, None
    return False


def loop():
    failures = 0
    config = None
    initialized = False
    while True:
        HEARTBEAT["value"] = time.monotonic()
        try:
            if not initialized:
                initialize_db()
                initialized = True
            config = ApiConfig.from_env()
            if config is None:
                _set_state(estado="sin_configurar", configurada=False, error=None)
                record_unconfigured()
                _sleep(60)
                continue
            _set_state(configurada=True)
            caught_up = run_cycle(config)
            failures = 0
            _sleep(config.interval_seconds if caught_up else 1)
        except ApiSourceError as error:
            failures += 1
            _set_state(estado=error.kind, error=str(error), fallos_consecutivos=failures)
            record_failure(error.kind, str(error))
            delay = backoff_seconds(failures, config)
            print(f"Telemetría API: {error} — reintento #{failures} en {delay:.0f} s", flush=True)
            _sleep(delay)
        except ValueError as error:  # configuración inválida en .env
            failures += 1
            _set_state(estado="error_configuracion", error=str(error), fallos_consecutivos=failures)
            record_failure("error_configuracion", str(error))
            print(f"Telemetría API: configuración inválida ({error})", flush=True)
            _sleep(60)
        except Exception as error:  # MariaDB caída, error inesperado: nunca detener el ciclo
            failures += 1
            message = f"{type(error).__name__}: {error}"
            _set_state(estado="error_interno", error=message[:255], fallos_consecutivos=failures)
            record_failure("error_interno", message)
            delay = backoff_seconds(failures, config)
            print(f"Telemetría API: ciclo fallido ({message}) — reintento en {delay:.0f} s", flush=True)
            _sleep(delay)


class Handler(JsonHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        # El servicio está sano si su ciclo sigue vivo, aunque la empresa esté
        # desconectada: la desconexión es un estado de los datos, no una avería.
        alive = time.monotonic() - HEARTBEAT["value"] < 180
        with LOCK:
            snapshot = dict(STATE)
        self.respond(200 if alive else 503, {"servicio": "telemetria-api",
                                             "status": "ok" if alive else "unavailable",
                                             "conexion": snapshot})


def watchdog(worker):
    while True:
        time.sleep(10)
        stalled = time.monotonic() - HEARTBEAT["value"] > WATCHDOG_SECONDS
        if not worker.is_alive() or stalled:
            print("Telemetría API: el ciclo se detuvo; reiniciando el contenedor", flush=True)
            os._exit(1)


if __name__ == "__main__":
    worker = threading.Thread(target=loop, daemon=True, name="telemetria-loop")
    worker.start()
    threading.Thread(target=watchdog, args=(worker,), daemon=True, name="watchdog").start()
    serve(Handler, "Telemetría API")
