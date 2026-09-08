"""Extrae telemetria de la API SQL y la carga de forma idempotente en MariaDB."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

SOURCE_COLUMNS = (
    "report_id", "sequential_id", "asset_id", "asset_name", "report_date",
    "latitude", "longitude", "speed", "gps_speed", "heading", "odometer",
    "ignition", "event_id", "event_name", "gps_validity", "gps_satellites", "gps_dop",
)
MAX_RESPONSE_BYTES = 25_000_000


def _connect_db(autocommit=True):
    # Importación diferida: las transformaciones y consultas se pueden probar sin un driver SQL instalado.
    from common.db import connect_db
    return connect_db(autocommit=autocommit)


def _integer_env(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} debe ser un entero") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} debe estar entre {minimum} y {maximum}")
    return value


def _datetime(value, field):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(normalized)
        except ValueError as error:
            raise ValueError(f"{field}: fecha invalida") from error
    else:
        raise ValueError(f"{field}: fecha ausente")
    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


@dataclass(frozen=True)
class TelemetryConfig:
    url: str
    start: datetime
    end: datetime
    batch_size: int
    max_total_rows: int
    timeout_seconds: int
    continuous: bool = False
    pages_per_cycle: int = 1

    @classmethod
    def from_env(cls):
        url = os.environ.get("SOURCE_API_URL", "").strip()
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("SOURCE_API_URL debe ser una URL HTTP valida")
        if len(url) > 255:
            raise ValueError("SOURCE_API_URL supera 255 caracteres")
        start = _datetime(os.environ.get("TELEMETRY_START", ""), "TELEMETRY_START")
        continuous = os.environ.get("TELEMETRY_CONTINUOUS", "0").strip().lower() in ("1", "true", "yes")
        end = (datetime.now(timezone.utc).replace(tzinfo=None) if continuous else
               _datetime(os.environ.get("TELEMETRY_END", ""), "TELEMETRY_END"))
        if end <= start:
            raise ValueError("TELEMETRY_END debe ser posterior a TELEMETRY_START")
        return cls(
            url=url,
            start=start,
            end=end,
            batch_size=_integer_env("TELEMETRY_BATCH_SIZE", 500, 1, 5000),
            max_total_rows=_integer_env("TELEMETRY_MAX_TOTAL_ROWS", 5000, 1, 1_000_000),
            timeout_seconds=_integer_env("SOURCE_API_TIMEOUT_SECONDS", 90, 5, 300),
            continuous=continuous,
            pages_per_cycle=_integer_env("TELEMETRY_PAGES_PER_CYCLE", 1, 1, 100),
        )


def _sql_string(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _sql_datetime(value):
    return _sql_string(value.strftime("%Y-%m-%d %H:%M:%S.%f"))


def build_query(config, cursor_date=None, cursor_id=None, limit=None):
    limit = config.batch_size if limit is None else int(limit)
    if not 1 <= limit <= config.batch_size:
        raise ValueError("limite de pagina invalido")
    conditions = [
        f"report_date >= {_sql_datetime(config.start)}",
        f"report_date < {_sql_datetime(config.end)}",
        "report_id IS NOT NULL",
    ]
    if cursor_date is not None or cursor_id is not None:
        if cursor_date is None or not cursor_id:
            raise ValueError("el cursor requiere fecha e identificador")
        date_value = _sql_datetime(_datetime(cursor_date, "ultimo_report_date"))
        identifier = _sql_string(cursor_id)
        conditions.append(
            f"(report_date > {date_value} OR (report_date = {date_value} AND report_id > {identifier}))"
        )
    return (
        f"SELECT {', '.join(SOURCE_COLUMNS)} FROM reportes WHERE "
        + " AND ".join(conditions)
        + f" ORDER BY report_date, report_id LIMIT {limit}"
    )


def fetch_rows(config, query):
    request = Request(
        config.url,
        data=json.dumps({"sql": query}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise RuntimeError(f"La API origen respondio HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("No fue posible conectar con la API origen") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("La respuesta del origen supera 25 MB")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("La API origen no devolvio JSON valido") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise ValueError("La respuesta debe contener una lista rows")
    if len(payload["rows"]) > config.batch_size:
        raise ValueError("La API devolvio mas filas que el limite solicitado")
    return payload["rows"]


def _text(value, field, maximum, required=False):
    result = " ".join(str(value or "").split())
    if required and not result:
        raise ValueError(f"{field}: valor ausente")
    if len(result) > maximum:
        raise ValueError(f"{field}: supera {maximum} caracteres")
    return result or None


def _number(value, field, minimum=None, maximum=None):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field}: numero invalido")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field}: numero invalido") from error
    if not math.isfinite(result):
        raise ValueError(f"{field}: numero invalido")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field}: menor que {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{field}: mayor que {maximum}")
    return result


def _integer(value, field):
    number = _number(value, field)
    if number is None:
        return None
    if not number.is_integer():
        raise ValueError(f"{field}: debe ser entero")
    return int(number)


def _boolean(value, field):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
        return value.strip().lower() in ("true", "1")
    raise ValueError(f"{field}: booleano invalido")


def normalize_row(row):
    if not isinstance(row, dict):
        raise ValueError("la fila no es un objeto JSON")
    latitude = _number(row.get("latitude"), "latitude", -90, 90)
    longitude = _number(row.get("longitude"), "longitude", -180, 180)
    if latitude is None or longitude is None:
        raise ValueError("coordenadas ausentes")
    return {
        "report_id": _text(row.get("report_id"), "report_id", 128, required=True),
        "sequential_id": _integer(row.get("sequential_id"), "sequential_id"),
        "asset_id": _text(row.get("asset_id"), "asset_id", 64, required=True),
        "asset_name": _text(row.get("asset_name"), "asset_name", 255),
        "report_date": _datetime(row.get("report_date"), "report_date"),
        "latitude": latitude,
        "longitude": longitude,
        "speed": _number(row.get("speed"), "speed", 0),
        "gps_speed": _number(row.get("gps_speed"), "gps_speed", 0),
        "heading": _number(row.get("heading"), "heading", 0, 360),
        "odometer": _number(row.get("odometer"), "odometer", 0),
        "ignition": _boolean(row.get("ignition"), "ignition"),
        "event_id": _integer(row.get("event_id"), "event_id"),
        "event_name": _text(row.get("event_name"), "event_name", 255),
        "gps_validity": _integer(row.get("gps_validity"), "gps_validity"),
        "gps_satellites": _integer(row.get("gps_satellites"), "gps_satellites"),
        "gps_dop": _number(row.get("gps_dop"), "gps_dop", 0),
        "raw_json": json.dumps(row, ensure_ascii=False, separators=(",", ":")),
    }


def transform_rows(rows):
    accepted, rejected, seen = [], [], set()
    for position, row in enumerate(rows, start=1):
        try:
            normalized = normalize_row(row)
            if normalized["report_id"] in seen:
                raise ValueError("report_id duplicado en la pagina")
            seen.add(normalized["report_id"])
            accepted.append(normalized)
        except ValueError as error:
            rejected.append({
                "fila": position,
                "report_id": str(row.get("report_id", ""))[:128] if isinstance(row, dict) else None,
                "motivo": str(error),
            })
    return accepted, rejected


def _state_for(config):
    with _connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM telemetria_etl_estado WHERE id=1 FOR UPDATE")
        state = cursor.fetchone()
        changed = bool(state and (
            state["origen"] != config.url
            or state["ventana_desde"] != config.start
            or (not config.continuous and state["ventana_hasta"] != config.end)
        ))
        if not state or changed:
            cursor.execute(
                """INSERT INTO telemetria_etl_estado
                (id,origen,ventana_desde,ventana_hasta,ultimo_report_date,ultimo_report_id,
                 leidos,validos,rechazados,estado,ultimo_error,rechazos_json,ultima_carga,ultima_revision)
                VALUES (1,%s,%s,%s,NULL,NULL,0,0,0,'pendiente',NULL,'[]',NULL,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE origen=VALUES(origen),ventana_desde=VALUES(ventana_desde),
                ventana_hasta=VALUES(ventana_hasta),ultimo_report_date=NULL,ultimo_report_id=NULL,
                leidos=0,validos=0,rechazados=0,estado='pendiente',ultimo_error=NULL,
                rechazos_json='[]',ultima_carga=NULL,ultima_revision=VALUES(ultima_revision)""",
                (config.url, config.start, config.end),
            )
            connection.commit()
            return {"leidos": 0, "validos": 0, "rechazados": 0, "estado": "pendiente",
                    "ultimo_report_date": None, "ultimo_report_id": None}
        if config.continuous and state["ventana_hasta"] != config.end:
            cursor.execute("""UPDATE telemetria_etl_estado SET ventana_hasta=%s,
                ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""", (config.end,))
            state["ventana_hasta"] = config.end
        connection.commit()
        return state


def _cursor_from(row):
    identifier = _text(row.get("report_id") if isinstance(row, dict) else None,
                       "report_id", 128, required=True)
    report_date = _datetime(row.get("report_date") if isinstance(row, dict) else None,
                            "report_date")
    return report_date, identifier


def run_once(config):
    state = _state_for(config)
    if state["estado"] in ("completo", "limite_alcanzado") and not config.continuous:
        return {"estado": state["estado"], "recibidos": 0, "validos": 0, "rechazados": 0,
                "total_validos": state["validos"]}
    remaining = config.batch_size if config.continuous else config.max_total_rows - int(state["leidos"])
    if remaining <= 0:
        with _connect_db() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE telemetria_etl_estado SET estado='limite_alcanzado',
                ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""")
        return {"estado": "limite_alcanzado", "recibidos": 0, "validos": 0,
                "rechazados": 0, "total_validos": state["validos"]}
    page_limit = min(config.batch_size, remaining)
    query = build_query(config, state["ultimo_report_date"], state["ultimo_report_id"], page_limit)
    rows = fetch_rows(config, query)
    accepted, rejected = transform_rows(rows)
    try:
        previous_rejections = json.loads(state.get("rechazos_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        previous_rejections = []
    rejection_history = (previous_rejections + rejected)[:50]
    cursor_date, cursor_id = _cursor_from(rows[-1]) if rows else (
        state["ultimo_report_date"], state["ultimo_report_id"]
    )
    new_read = int(state["leidos"]) + len(rows)
    new_valid = int(state["validos"]) + len(accepted)
    new_rejected = int(state["rechazados"]) + len(rejected)
    if len(rows) < page_limit:
        status = "esperando_nuevos_datos" if config.continuous else "completo"
    elif not config.continuous and new_read >= config.max_total_rows:
        status = "limite_alcanzado"
    else:
        status = "ejecutando"
    values = [(
        row["report_id"], row["sequential_id"], row["asset_id"], row["asset_name"],
        row["report_date"], row["latitude"], row["longitude"], row["speed"],
        row["gps_speed"], row["heading"], row["odometer"], row["ignition"],
        row["event_id"], row["event_name"], row["gps_validity"], row["gps_satellites"],
        row["gps_dop"], row["raw_json"],
    ) for row in accepted]
    with _connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        if values:
            cursor.executemany(
                """INSERT INTO telemetria_raw
                (report_id,sequential_id,asset_id,asset_name,report_date,latitude,longitude,
                 speed,gps_speed,heading,odometer,ignition,event_id,event_name,gps_validity,
                 gps_satellites,gps_dop,raw_json,ingestado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE sequential_id=VALUES(sequential_id),asset_id=VALUES(asset_id),
                asset_name=VALUES(asset_name),report_date=VALUES(report_date),latitude=VALUES(latitude),
                longitude=VALUES(longitude),speed=VALUES(speed),gps_speed=VALUES(gps_speed),
                heading=VALUES(heading),odometer=VALUES(odometer),ignition=VALUES(ignition),
                event_id=VALUES(event_id),event_name=VALUES(event_name),gps_validity=VALUES(gps_validity),
                gps_satellites=VALUES(gps_satellites),gps_dop=VALUES(gps_dop),raw_json=VALUES(raw_json),
                ingestado_en=VALUES(ingestado_en)""",
                values,
            )
        cursor.execute(
            """UPDATE telemetria_etl_estado SET ultimo_report_date=%s,ultimo_report_id=%s,
            leidos=%s,validos=%s,rechazados=%s,estado=%s,ultimo_error=NULL,rechazos_json=%s,
            ultima_carga=UTC_TIMESTAMP(6),ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""",
            (cursor_date, cursor_id, new_read, new_valid, new_rejected, status,
             json.dumps(rejection_history, ensure_ascii=False)),
        )
        connection.commit()
    return {"estado": status, "recibidos": len(rows), "validos": len(accepted),
            "rechazados": len(rejected), "total_validos": new_valid}


def record_error(message):
    safe_message = " ".join(str(message).split())[:255]
    with _connect_db() as connection, connection.cursor() as cursor:
        cursor.execute("""UPDATE telemetria_etl_estado SET estado='error',ultimo_error=%s,
            ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""", (safe_message,))
