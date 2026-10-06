"""Cliente de la API SQL de la empresa (POST {"sql": ...} -> {"rows": [...]}).

Todo lo que puede fallar fuera de nuestro control (Wi-Fi, servidor de la empresa,
respuestas mal formadas) termina en un ApiSourceError con un tipo claro, para que
el worker decida cómo reintentar sin caerse.

Fechas: el cursor se construye con la fecha tal como la guarda la base del origen
(texto "YYYY-MM-DD HH:MM:SS.ffffff" obtenido con DATE_FORMAT). Así las consultas
de paginación nunca dependen de cómo el servidor de la API convierte zonas horarias,
que es la causa típica de saltarse horas de datos.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import http.client
import json
import math
import os
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_RESPONSE_BYTES = 25_000_000
NATIVE_FORMAT = "%Y-%m-%d %H:%i:%s.%f"  # formato MySQL/MariaDB del lado del origen
PG_FORMAT = "YYYY-MM-DD HH24:MI:SS.US"   # mismo formato en PostgreSQL (la API de la empresa)
DIALECTS = ("postgres", "mysql")
SOURCE_COLUMNS = ("report_id", "asset_id", "asset_name", "latitude", "longitude",
                  "speed", "heading", "ignition", "event_name")


class ApiSourceError(RuntimeError):
    """kind: sin_conexion | error_origen | error_configuracion | respuesta_invalida."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _env_int(name, default, minimum, maximum):
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as error:
        raise ValueError(f"{name} debe ser un entero") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} debe estar entre {minimum} y {maximum}")
    return value


def _parse_start(raw):
    """COMPANY_API_START en hora de Chile (o con zona explícita) -> UTC naive."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("COMPANY_API_START debe ser 'YYYY-MM-DD HH:MM' (hora de Chile)") from error
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(os.environ.get("ARTISA_TIMEZONE", "America/Santiago")))
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _dialect(raw):
    value = (raw or "postgres").strip().lower()
    if value not in DIALECTS:
        raise ValueError("COMPANY_API_DIALECT debe ser 'postgres' o 'mysql'")
    return value


def build_clock_query(config):
    """Consulta mínima para conocer el reloj del origen (sin leer reportes)."""
    return f"SELECT {_date_expression(config, _now(config))} AS ahora_origen"


@dataclass(frozen=True)
class ApiConfig:
    url: str
    table: str
    timezone_name: str          # "auto" o una zona IANA, solo para convertir a UTC
    native_dates: bool          # True: la fecha viaja como texto con el valor exacto del origen
    auth_header: str
    auth_value: str
    timeout_seconds: int
    batch_size: int
    interval_seconds: int
    overlap_seconds: int
    initial_lookback_hours: int
    start_utc: object            # datetime UTC naive o None (COMPANY_API_START)
    max_future_minutes: int
    max_pages_per_cycle: int
    retry_min_seconds: int
    retry_max_seconds: int
    dialect: str = "postgres"   # motor SQL detrás de la API: postgres (actual) o mysql

    @classmethod
    def from_env(cls):
        """None si la API no está configurada: el servicio queda esperando sin error."""
        url = os.environ.get("COMPANY_API_URL", "").strip()
        if not url:
            return None
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("COMPANY_API_URL debe ser una URL http(s) válida")
        table = os.environ.get("COMPANY_API_TABLE", "reportes").strip() or "reportes"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", table):
            raise ValueError("COMPANY_API_TABLE debe ser un nombre de tabla simple")
        timezone_name = os.environ.get("COMPANY_API_TIMEZONE", "auto").strip() or "auto"
        if timezone_name != "auto":
            try:
                ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError("COMPANY_API_TIMEZONE debe ser 'auto' o una zona IANA") from error
        retry_min = _env_int("COMPANY_API_RETRY_MIN_SECONDS", 5, 1, 300)
        return cls(
            url=url,
            table=table,
            timezone_name=timezone_name,
            native_dates=os.environ.get("COMPANY_API_NATIVE_DATES", "1").strip() != "0",
            dialect=_dialect(os.environ.get("COMPANY_API_DIALECT", "postgres")),
            auth_header=os.environ.get("COMPANY_API_AUTH_HEADER", "Authorization").strip() or "Authorization",
            auth_value=os.environ.get("COMPANY_API_TOKEN", "").strip(),
            timeout_seconds=_env_int("COMPANY_API_TIMEOUT_SECONDS", 30, 3, 300),
            batch_size=_env_int("COMPANY_API_BATCH_SIZE", 1000, 1, 5000),
            interval_seconds=_env_int("COMPANY_API_INTERVAL_SECONDS", 30, 5, 3600),
            overlap_seconds=_env_int("COMPANY_API_OVERLAP_SECONDS", 300, 0, 86400),
            initial_lookback_hours=_env_int("COMPANY_API_INITIAL_LOOKBACK_HOURS", 24, 1, 24 * 31),
            start_utc=_parse_start(os.environ.get("COMPANY_API_START", "")),
            max_future_minutes=_env_int("COMPANY_API_MAX_FUTURE_MINUTES", 10, 0, 1440),
            max_pages_per_cycle=_env_int("COMPANY_API_MAX_PAGES_PER_CYCLE", 50, 1, 10000),
            retry_min_seconds=retry_min,
            retry_max_seconds=_env_int("COMPANY_API_RETRY_MAX_SECONDS", 60, retry_min, 3600),
        )

    @property
    def origin(self):
        """Identificación del origen sin ruta, usuario ni contraseña (se guarda en MariaDB)."""
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"


# --- Construcción de la consulta ------------------------------------------------

def _sql_string(value, dialect="postgres"):
    text = str(value)
    if dialect == "mysql":  # en MySQL la barra invertida escapa; en PostgreSQL es literal
        text = text.replace("\\", "\\\\")
    return "'" + text.replace("'", "''") + "'"


def native_literal(value, dialect="postgres"):
    return _sql_string(value.strftime("%Y-%m-%d %H:%M:%S.%f"), dialect)


def _now(config):
    # Hora del reloj del origen, en los mismos términos que su columna report_date.
    return "LOCALTIMESTAMP" if config.dialect == "postgres" else "NOW(6)"


def _interval(config, amount, unit):
    if config.dialect == "postgres":
        return f"INTERVAL '{int(amount)} {unit.lower()}s'"
    return f"INTERVAL {int(amount)} {unit}"


def _date_expression(config, column):
    if not config.native_dates:
        return column
    if config.dialect == "postgres":
        return f"to_char({column}, '{PG_FORMAT}')"
    return f"DATE_FORMAT({column}, '{NATIVE_FORMAT}')"


def date_expression(config, column):
    return _date_expression(config, column)


def build_query(config, after=None, since=None):
    """Una página ordenada por (report_date, report_id).

    after: (fecha_nativa, report_id) -> solo filas estrictamente posteriores (keyset).
    since: fecha_nativa -> filas desde esa fecha (incluida). Sin ambos, usa la ventana
           inicial relativa al reloj del propio origen.
    Siempre excluye fechas más allá de NOW() + margen del origen: un GPS con el reloj
    adelantado no puede empujar el cursor al futuro y dejarnos sin datos nuevos.
    """
    columns = [f"r.{name}" for name in SOURCE_COLUMNS]
    columns.append(f"{_date_expression(config, 'r.report_date')} AS report_date_origen")
    columns.append(f"{_date_expression(config, _now(config))} AS ahora_origen")
    conditions = ["r.report_id IS NOT NULL", "r.report_date IS NOT NULL",
                  f"r.report_date <= {_now(config)} + {_interval(config, config.max_future_minutes, 'MINUTE')}"]
    if after is not None:
        date_value, identifier = after
        literal = native_literal(date_value, config.dialect)
        identifier = _sql_string(identifier, config.dialect)
        report_id = "r.report_id::text" if config.dialect == "postgres" else "r.report_id"
        conditions.append(
            f"(r.report_date > {literal} OR (r.report_date = {literal} AND {report_id} > {identifier}))"
        )
    elif since is not None:
        conditions.append(f"r.report_date >= {native_literal(since, config.dialect)}")
    else:
        conditions.append(f"r.report_date >= {_now(config)} - {_interval(config, config.initial_lookback_hours, 'HOUR')}")
    return (
        f"SELECT {', '.join(columns)} FROM {config.table} AS r WHERE "
        + " AND ".join(conditions)
        + f" ORDER BY r.report_date, {'r.report_id::text' if config.dialect == 'postgres' else 'r.report_id'}"
        + f" LIMIT {int(config.batch_size)}"
    )


# --- Llamada HTTP ---------------------------------------------------------------

def _safe_message(text, limit=160):
    return " ".join(str(text).split())[:limit]


def fetch_rows(config, query):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.auth_value:
        headers[config.auth_header] = config.auth_value
    request = Request(config.url, data=json.dumps({"sql": query}).encode("utf-8"),
                      headers=headers, method="POST")
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        detail = ""
        try:
            detail = _safe_message(error.read(2000).decode("utf-8", "replace"))
        except Exception:
            pass
        if error.code in (401, 403):
            raise ApiSourceError("error_configuracion", f"La API rechazó las credenciales (HTTP {error.code})") from None
        if error.code in (400, 404, 405, 422):
            raise ApiSourceError("error_configuracion",
                                 f"La API rechazó la consulta (HTTP {error.code}) {detail}".strip()) from None
        raise ApiSourceError("error_origen", f"La API respondió HTTP {error.code} {detail}".strip()) from None
    except (URLError, TimeoutError, socket.timeout, ConnectionError,
            http.client.HTTPException, OSError) as error:
        reason = getattr(error, "reason", error)
        raise ApiSourceError("sin_conexion",
                             f"Sin conexión con la API de la empresa ({type(reason).__name__})") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ApiSourceError("respuesta_invalida", "La respuesta de la API supera 25 MB")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiSourceError("respuesta_invalida", "La API no devolvió JSON válido") from None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
    elif isinstance(payload, dict) and payload.get("error"):
        raise ApiSourceError("error_origen", "La API informó un error: " + _safe_message(payload["error"]))
    else:
        raise ApiSourceError("respuesta_invalida", "La respuesta no contiene la lista rows")
    if len(rows) > config.batch_size:
        raise ApiSourceError("respuesta_invalida", "La API devolvió más filas que el límite pedido")
    return rows


# --- Normalización de filas ------------------------------------------------------

def parse_source_datetime(value):
    """Devuelve datetime (naive si el origen no informó zona, aware si la informó)."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("fecha ausente")
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("fecha inválida") from None


def native_key(row):
    """(fecha_nativa, report_id) de una fila cruda, o None si no se puede leer."""
    if not isinstance(row, dict):
        return None
    try:
        moment = parse_source_datetime(row.get("report_date_origen"))
    except ValueError:
        return None
    identifier = " ".join(str(row.get("report_id") or "").split())
    if not identifier:
        return None
    if moment.tzinfo is not None:  # modo sin DATE_FORMAT: se trabaja en UTC
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment, identifier


def detect_offset(rows, utc_now):
    """Diferencia entre el reloj del origen y UTC, redondeada a 15 minutos."""
    for row in rows:
        try:
            source_now = parse_source_datetime(row.get("ahora_origen"))
        except (ValueError, AttributeError):
            continue
        if source_now.tzinfo is not None:
            return timedelta(0)
        minutes = (source_now - utc_now).total_seconds() / 60
        return timedelta(minutes=15 * round(minutes / 15))
    return None


def to_utc(moment, config, offset):
    """Fecha del origen -> UTC naive para guardar en MariaDB."""
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    if config.timezone_name == "auto":
        return moment - (offset or timedelta(0))
    local = moment.replace(tzinfo=ZoneInfo(config.timezone_name))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _text(value, field, maximum, required=False):
    result = " ".join(str(value if value is not None else "").split())
    if required and not result:
        raise ValueError(f"{field}: valor ausente")
    if len(result) > maximum:
        raise ValueError(f"{field}: supera {maximum} caracteres")
    return result or None


def _number(value, field, minimum, maximum, required=False):
    if value is None or value == "":
        if required:
            raise ValueError(f"{field}: valor ausente")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field}: número inválido")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field}: número inválido") from None
    if not math.isfinite(result) or not minimum <= result <= maximum:
        if required:
            raise ValueError(f"{field}: fuera de rango")
        return None  # dato accesorio dudoso: se descarta el valor, no la posición
    return result


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "on", "si", "sí", "encendido"):
            return True
        if normalized in ("0", "false", "off", "no", "apagado"):
            return False
    return None


def normalize_row(row, config, offset):
    if not isinstance(row, dict):
        raise ValueError("la fila no es un objeto JSON")
    key = native_key(row)
    if key is None:
        raise ValueError("report_id o fecha inválidos")
    latitude = _number(row.get("latitude"), "latitude", -90, 90, required=True)
    longitude = _number(row.get("longitude"), "longitude", -180, 180, required=True)
    if latitude == 0 and longitude == 0:
        raise ValueError("coordenadas 0,0 (GPS sin señal)")
    return {
        "report_id": _text(row.get("report_id"), "report_id", 128, required=True),
        "asset_id": _text(row.get("asset_id"), "asset_id", 16, required=True),
        "asset_name": _text(row.get("asset_name"), "asset_name", 128),
        "report_date": to_utc(key[0], config, offset),
        "latitude": latitude,
        "longitude": longitude,
        "speed": _number(row.get("speed"), "speed", 0, 300),
        "heading": _number(row.get("heading"), "heading", 0, 360),
        "ignition": _boolean(row.get("ignition")),
        "event_name": _text(row.get("event_name"), "event_name", 128),
    }


def transform_rows(rows, config, offset):
    accepted, rejected, seen = [], [], set()
    for position, row in enumerate(rows, start=1):
        try:
            normalized = normalize_row(row, config, offset)
            if normalized["report_id"] in seen:
                raise ValueError("report_id duplicado en la página")
            seen.add(normalized["report_id"])
            accepted.append(normalized)
        except ValueError as error:
            rejected.append({
                "fila": position,
                "report_id": str(row.get("report_id", ""))[:128] if isinstance(row, dict) else None,
                "motivo": str(error),
                "clave": native_key(row),
            })
    return accepted, rejected
