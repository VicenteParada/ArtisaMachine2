"""Ingesta incremental de telemetría nueva desde la API SQL cada cinco minutos."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from common.db import connect_db, initialize_db
from common.http import JsonHandler, serve
from ETLs.telemetry import _existing_keys, _insert_latest_positions, transform_rows


@dataclass(frozen=True)
class RealtimeConfig:
    url: str
    batch_size: int
    interval_seconds: int
    catchup_seconds: int
    timeout_seconds: int
    start: datetime

    @classmethod
    def from_env(cls):
        start_text = os.environ.get("REALTIME_START", "").strip()
        if start_text:
            start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
            if start.tzinfo is not None:
                start = start.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        return cls(
            url=os.environ.get("SOURCE_API_URL", "").strip(),
            batch_size=min(5000, max(1, int(os.environ.get("REALTIME_BATCH_SIZE", "1000")))),
            interval_seconds=max(60, int(os.environ.get("REALTIME_INTERVAL_SECONDS", "300"))),
            catchup_seconds=max(1, int(os.environ.get("REALTIME_CATCHUP_SECONDS", "5"))),
            timeout_seconds=max(5, int(os.environ.get("SOURCE_API_TIMEOUT_SECONDS", "90"))),
            start=start,
        )


def _sql_text(value):
    return str(value).replace("'", "''")


def _state(config):
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM realtime_estado WHERE id=1 FOR UPDATE")
        state = cursor.fetchone()
        if not state or state["origen"] != config.url:
            cursor.execute(
                """INSERT INTO realtime_estado
                (id,origen,ultimo_report_date,ultimo_report_id,leidos,validos,rechazados,
                 duplicados,estado,ultimo_error,ultima_carga,ultima_revision)
                VALUES (1,%s,%s,NULL,0,0,0,0,'pendiente',NULL,NULL,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE origen=VALUES(origen),
                ultimo_report_date=VALUES(ultimo_report_date),ultimo_report_id=NULL,
                leidos=0,validos=0,rechazados=0,duplicados=0,estado='pendiente',
                ultimo_error=NULL,ultima_carga=NULL,ultima_revision=VALUES(ultima_revision)""",
                (config.url, config.start),
            )
            connection.commit()
            return {"ultimo_report_date": config.start, "ultimo_report_id": None,
                    "leidos": 0, "validos": 0, "rechazados": 0, "duplicados": 0}
        connection.commit()
        return state


def _query(state, limit):
    date = state.get("ultimo_report_date")
    report_id = state.get("ultimo_report_id") or ""
    date_text = date.strftime("%Y-%m-%d %H:%M:%S.%f") if isinstance(date, datetime) else str(date)
    return f"""SELECT report_id,report_date,asset_id,asset_name,event_id,event_name,
    latitude,longitude,location,speed,gps_speed,cartography_limit_speed,odometer,
    ignition,sequential_id,driver_name,driver_last_name,driver_document_number
    FROM reportes
    WHERE (report_date > '{_sql_text(date_text)}')
       OR (report_date = '{_sql_text(date_text)}' AND report_id > '{_sql_text(report_id)}')
    ORDER BY report_date,report_id LIMIT {int(limit)}"""


def fetch_rows(config, state):
    if not config.url:
        raise RuntimeError("SOURCE_API_URL no está configurada")
    body = json.dumps({"sql": _query(state, config.batch_size)}).encode("utf-8")
    request = Request(config.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError("No fue posible consultar la API de telemetría") from error
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("La API no entregó una lista rows")
    for row in rows:
        if isinstance(row, dict):
            identity = str(row.get("report_id") or json.dumps(row, sort_keys=True, default=str))
            row["_id"] = sha256(("realtime|" + identity).encode("utf-8")).hexdigest()[:24]
    return rows


def run_once(config):
    state = _state(config)
    documents = fetch_rows(config, state)
    accepted, rejected = transform_rows(documents)
    duplicates = []
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        existing_reports, existing_mongo = _existing_keys(cursor, accepted) if accepted else (set(), set())
        unique_rows = []
        for row in accepted:
            if row["report_id"] in existing_reports or row["mongo_id"] in existing_mongo:
                duplicates.append(row)
            else:
                unique_rows.append(row)
        if unique_rows:
            cursor.executemany(
                """INSERT INTO raw_gps
                (mongo_id,report_id,report_date,asset_id,asset_name,event_name,latitude,
                 longitude,location,speed,gps_speed,heading,odometer,ignition,event_id,
                 sequential_id,cartography_limit_speed,driver_name,driver_last_name,
                 driver_document_number,ingestado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        UTC_TIMESTAMP(6))""",
                [(row["mongo_id"],row["report_id"],row["report_date"],row["asset_id"],
                  row["asset_name"],row["event_name"],row["latitude"],row["longitude"],
                  row["location"],row["speed"],row["gps_speed"],row["heading"],
                  row["odometer"],row["ignition"],row["event_id"],row["sequential_id"],
                  row["cartography_limit_speed"],row["driver_name"],row["driver_last_name"],
                  row["driver_document_number"]) for row in unique_rows],
            )
        _insert_latest_positions(cursor, unique_rows)
        if documents:
            last = documents[-1]
            last_date = last.get("report_date")
            if isinstance(last_date, str):
                last_date = datetime.fromisoformat(last_date.replace("Z", "+00:00"))
                if last_date.tzinfo is not None:
                    last_date = last_date.astimezone(timezone.utc).replace(tzinfo=None)
            last_id = str(last.get("report_id") or "")
        else:
            last_date, last_id = state.get("ultimo_report_date"), state.get("ultimo_report_id")
        status = "actualizado" if len(documents) < config.batch_size else "recuperando"
        cursor.execute(
            """UPDATE realtime_estado SET ultimo_report_date=%s,ultimo_report_id=%s,
            leidos=leidos+%s,validos=validos+%s,rechazados=rechazados+%s,
            duplicados=duplicados+%s,estado=%s,ultimo_error=NULL,
            ultima_carga=UTC_TIMESTAMP(6),ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""",
            (last_date,last_id,len(documents),len(unique_rows),len(rejected),len(duplicates),status),
        )
        connection.commit()
    return {"estado": status, "leidos": len(documents), "validos": len(unique_rows),
            "rechazados": len(rejected), "duplicados": len(duplicates)}


STATE = {"last_success": 0.0, "estado": "iniciando", "error": None}


def loop():
    enabled = os.environ.get("REALTIME_ENABLED", "0").strip().casefold() in {"1","true","on","si","sí"}
    try:
        initialize_db()
        config = RealtimeConfig.from_env()
    except Exception as error:
        STATE.update(estado="error", error=f"{type(error).__name__}: {error}")
        return
    if not enabled:
        with connect_db() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE realtime_estado SET estado='pausado',ultima_revision=UTC_TIMESTAMP(6) WHERE id=1")
        STATE.update(last_success=time.monotonic(), estado="pausado", error=None)
    while True:
        if not enabled:
            STATE["last_success"] = time.monotonic()
            time.sleep(30)
            continue
        try:
            result = run_once(config)
            STATE.update(last_success=time.monotonic(), error=None, **result)
            delay = config.catchup_seconds if result["leidos"] == config.batch_size else config.interval_seconds
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            STATE.update(estado="error", error=message)
            with connect_db() as connection, connection.cursor() as cursor:
                cursor.execute("""UPDATE realtime_estado SET estado='error',ultimo_error=%s,
                ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""", (message[:255],))
            delay = config.interval_seconds
        time.sleep(delay)


class Handler(JsonHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        ready = STATE["error"] is None and (
            STATE["estado"] == "pausado" or time.monotonic() - STATE["last_success"] < 900
        )
        payload = {key: value for key, value in STATE.items() if key != "last_success"}
        self.respond(200 if ready else 503, {"servicio": "realtime", "status": "ok" if ready else "unavailable",
                                                "telemetria": payload})


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    serve(Handler, "Realtime ETL")
