"""Importación paginada e idempotente de telemetría MongoDB hacia MariaDB."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os


@dataclass(frozen=True)
class TelemetryConfig:
    uri: str
    database: str
    collection: str
    batch_size: int
    timeout_seconds: int

    @classmethod
    def from_env(cls):
        uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017").strip()
        database = os.environ.get("MONGO_DATABASE", "gps_data").strip()
        collection = os.environ.get("MONGO_COLLECTION", "raw_gps").strip()
        if not uri:
            raise ValueError("MONGO_URI es obligatorio")
        if not database or not collection:
            raise ValueError("MONGO_DATABASE y MONGO_COLLECTION son obligatorios")
        return cls(
            uri=uri,
            database=database,
            collection=collection,
            batch_size=_integer_env("TELEMETRY_BATCH_SIZE", 2000, 1, 5000),
            timeout_seconds=_integer_env("MONGO_TIMEOUT_SECONDS", 10, 1, 120),
        )


def _integer_env(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} debe ser un entero") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} debe estar entre {minimum} y {maximum}")
    return value


def _connect_db(autocommit=True):
    from common.db import connect_db
    return connect_db(autocommit=autocommit)


def _mongo_client(config):
    from pymongo import MongoClient
    return MongoClient(config.uri, serverSelectionTimeoutMS=config.timeout_seconds * 1000)


def source_total(config):
    """Obtiene un conteo rápido para calcular progreso sin recorrer la colección."""
    from pymongo.errors import PyMongoError
    try:
        with _mongo_client(config) as client:
            return int(client[config.database][config.collection].estimated_document_count())
    except PyMongoError as error:
        raise RuntimeError("No fue posible contar MongoDB") from error


def fetch_batch(config, after_id):
    """Trae como máximo batch_size documentos con _id mayor al cursor."""
    from bson import ObjectId
    from bson.errors import InvalidId
    from pymongo.errors import PyMongoError

    query = {}
    if after_id:
        try:
            query["_id"] = {"$gt": ObjectId(after_id)}
        except InvalidId as error:
            raise ValueError("cursor invalido") from error
    try:
        with _mongo_client(config) as client:
            return list(
                client[config.database][config.collection]
                .find(query)
                .sort("_id", 1)
                .limit(config.batch_size)
            )
    except PyMongoError as error:
        raise RuntimeError("No fue posible leer MongoDB") from error


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


def _optional_integer(value, field):
    number = _number(value, field)
    if number is None:
        return None
    if not number.is_integer():
        raise ValueError(f"{field}: debe ser entero")
    return int(number)


def _optional_boolean(value, field):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "on", "si", "sí", "encendido"}:
        return True
    if normalized in {"0", "false", "off", "no", "apagado"}:
        return False
    raise ValueError(f"{field}: booleano invalido")


def normalize_row(document):
    if not isinstance(document, dict):
        raise ValueError("la fila no es un objeto")
    mongo_id = document.get("_id")
    if not mongo_id:
        raise ValueError("_id ausente")
    payload = {key: value for key, value in document.items() if key != "_id"}
    latitude = _number(payload.get("latitude"), "latitude", -90, 90)
    longitude = _number(payload.get("longitude"), "longitude", -180, 180)
    if latitude is None or longitude is None:
        raise ValueError("coordenadas ausentes")
    return {
        "mongo_id": str(mongo_id),
        "report_id": _text(payload.get("report_id"), "report_id", 36, required=True),
        "asset_id": _text(payload.get("asset_id"), "asset_id", 16, required=True),
        "asset_name": _text(payload.get("asset_name"), "asset_name", 64),
        "report_date": _datetime(payload.get("report_date"), "report_date"),
        "latitude": latitude,
        "longitude": longitude,
        "location": _text(payload.get("location"), "location", 255),
        "speed": _number(payload.get("speed"), "speed", 0),
        "gps_speed": _number(payload.get("gps_speed"), "gps_speed", 0),
        "heading": _number(payload.get("heading"), "heading", 0, 360),
        "odometer": _number(payload.get("odometer"), "odometer", 0),
        "ignition": _optional_boolean(payload.get("ignition"), "ignition"),
        "event_id": _optional_integer(payload.get("event_id"), "event_id"),
        "sequential_id": _optional_integer(payload.get("sequential_id"), "sequential_id"),
        "cartography_limit_speed": _number(
            payload.get("cartography_limit_speed"), "cartography_limit_speed", 0
        ),
        "event_name": _text(payload.get("event_name"), "event_name", 128),
        "driver_name": _text(payload.get("driver_name"), "driver_name", 64),
        "driver_last_name": _text(payload.get("driver_last_name"), "driver_last_name", 128),
        "driver_document_number": _text(
            payload.get("driver_document_number"), "driver_document_number", 32
        ),
    }


def transform_rows(documents):
    accepted, rejected, seen = [], [], set()
    for position, document in enumerate(documents, start=1):
        try:
            normalized = normalize_row(document)
            if normalized["report_id"] in seen:
                raise ValueError("report_id duplicado en la pagina")
            seen.add(normalized["report_id"])
            normalized["_source_position"] = position
            accepted.append(normalized)
        except ValueError as error:
            rejected.append({
                "fila": position,
                "report_id": str(document.get("report_id", ""))[:128]
                if isinstance(document, dict) else None,
                "motivo": str(error),
            })
    return accepted, rejected


def _identity(config):
    return f"{config.uri}|{config.database}|{config.collection}"


def _state_for(config):
    identity = _identity(config)
    with _connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM raw_gps_estado WHERE id=1 FOR UPDATE")
        state = cursor.fetchone()
        changed = bool(state and state["origen"] != identity)
        if not state or changed:
            cursor.execute(
                """INSERT INTO raw_gps_estado
                (id,origen,ultimo_mongo_id,leidos,validos,rechazados,total_origen,
                 bloques_completados,bloques_totales,tamano_bloque,estado,ultimo_error,
                 rechazos_json,ultima_carga,ultima_revision)
                VALUES (1,%s,NULL,0,0,0,0,0,0,%s,'pendiente',NULL,'[]',NULL,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE origen=VALUES(origen),ultimo_mongo_id=NULL,
                leidos=0,validos=0,rechazados=0,total_origen=0,bloques_completados=0,
                bloques_totales=0,tamano_bloque=VALUES(tamano_bloque),estado='pendiente',
                ultimo_error=NULL,rechazos_json='[]',ultima_carga=NULL,
                ultima_revision=VALUES(ultima_revision)""",
                (identity, config.batch_size),
            )
            connection.commit()
            return {
                "leidos": 0, "validos": 0, "rechazados": 0, "total_origen": 0,
                "bloques_completados": 0, "bloques_totales": 0,
                "tamano_bloque": config.batch_size, "estado": "pendiente",
                "ultimo_mongo_id": None, "rechazos_json": "[]",
            }
        connection.commit()
        return state


def _set_total(config, state):
    total = int(state.get("total_origen") or 0)
    if total:
        return total
    total = source_total(config)
    block_total = math.ceil(total / config.batch_size) if total else 0
    with _connect_db() as connection, connection.cursor() as cursor:
        cursor.execute(
            """UPDATE raw_gps_estado SET total_origen=%s,bloques_totales=%s,
            tamano_bloque=%s,ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""",
            (total, block_total, config.batch_size),
        )
    state["total_origen"] = total
    state["bloques_totales"] = block_total
    state["tamano_bloque"] = config.batch_size
    return total


def _existing_keys(cursor, rows):
    report_ids, mongo_ids = set(), set()
    for offset in range(0, len(rows), 500):
        chunk = rows[offset:offset + 500]
        placeholders = ",".join(["%s"] * len(chunk))
        cursor.execute(
            f"SELECT report_id,mongo_id FROM raw_gps WHERE report_id IN ({placeholders})",
            tuple(row["report_id"] for row in chunk),
        )
        for item in cursor.fetchall():
            report_ids.add(item["report_id"])
            mongo_ids.add(item["mongo_id"])
        cursor.execute(
            f"SELECT report_id,mongo_id FROM raw_gps WHERE mongo_id IN ({placeholders})",
            tuple(row["mongo_id"] for row in chunk),
        )
        for item in cursor.fetchall():
            report_ids.add(item["report_id"])
            mongo_ids.add(item["mongo_id"])
    return report_ids, mongo_ids


def _latest_by_vehicle(rows):
    latest = {}
    for row in rows:
        current = latest.get(row["asset_id"])
        key = (row["report_date"], row["report_id"])
        if current is None or key > (current["report_date"], current["report_id"]):
            latest[row["asset_id"]] = row
    return list(latest.values())


def _insert_latest_positions(cursor, rows):
    values = [(
        row["asset_id"], row["asset_name"], row["report_id"], row["report_date"],
        row["latitude"], row["longitude"], row["speed"], row["gps_speed"],
        row["heading"], row["odometer"], row["ignition"], row["event_name"],
        row["driver_name"], row["driver_last_name"], row["driver_document_number"],
    ) for row in _latest_by_vehicle(rows)]
    if not values:
        return
    cursor.executemany(
        """INSERT INTO vehiculos_ultima_posicion
        (asset_id,asset_name,report_id,report_date,latitude,longitude,speed,gps_speed,
         heading,odometer,ignition,event_name,driver_name,driver_last_name,
         driver_document_number,actualizado_en)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))
        ON DUPLICATE KEY UPDATE
        asset_name=IF(VALUES(report_date)>=report_date,VALUES(asset_name),asset_name),
        report_id=IF(VALUES(report_date)>=report_date,VALUES(report_id),report_id),
        latitude=IF(VALUES(report_date)>=report_date,VALUES(latitude),latitude),
        longitude=IF(VALUES(report_date)>=report_date,VALUES(longitude),longitude),
        speed=IF(VALUES(report_date)>=report_date,VALUES(speed),speed),
        gps_speed=IF(VALUES(report_date)>=report_date,VALUES(gps_speed),gps_speed),
        heading=IF(VALUES(report_date)>=report_date,VALUES(heading),heading),
        odometer=IF(VALUES(report_date)>=report_date,VALUES(odometer),odometer),
        ignition=IF(VALUES(report_date)>=report_date,VALUES(ignition),ignition),
        event_name=IF(VALUES(report_date)>=report_date,VALUES(event_name),event_name),
        driver_name=IF(VALUES(report_date)>=report_date,VALUES(driver_name),driver_name),
        driver_last_name=IF(VALUES(report_date)>=report_date,VALUES(driver_last_name),driver_last_name),
        driver_document_number=IF(VALUES(report_date)>=report_date,
                                  VALUES(driver_document_number),driver_document_number),
        actualizado_en=IF(VALUES(report_date)>=report_date,UTC_TIMESTAMP(6),actualizado_en),
        report_date=GREATEST(report_date,VALUES(report_date))""",
        values,
    )


def mark_status(status, error=None):
    if status not in {"pendiente", "ejecutando", "pausado", "completado", "error"}:
        raise ValueError("estado ETL no valido")
    safe_error = " ".join(str(error).split())[:255] if error else None
    with _connect_db() as connection, connection.cursor() as cursor:
        cursor.execute(
            """UPDATE raw_gps_estado SET estado=%s,ultimo_error=%s,
            ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""",
            (status, safe_error),
        )


def progress_snapshot():
    with _connect_db() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM raw_gps_estado WHERE id=1")
        state = cursor.fetchone()
    if not state:
        return None
    total = int(state.get("total_origen") or 0)
    read = int(state.get("leidos") or 0)
    state["documentos_pendientes"] = max(0, total - read)
    state["bloques_pendientes"] = max(
        0, int(state.get("bloques_totales") or 0) - int(state.get("bloques_completados") or 0)
    )
    state["progreso_pct"] = round(read * 100 / total, 4) if total else 0.0
    return state


def run_once(config):
    started_at = datetime.utcnow()
    state = _state_for(config)
    total = _set_total(config, state)
    documents = fetch_batch(config, state["ultimo_mongo_id"])
    accepted, rejected = transform_rows(documents)
    last_id = str(documents[-1]["_id"]) if documents else state["ultimo_mongo_id"]

    with _connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        existing_reports, existing_mongo = _existing_keys(cursor, accepted) if accepted else (set(), set())
        unique_rows = []
        for row in accepted:
            if row["report_id"] in existing_reports or row["mongo_id"] in existing_mongo:
                rejected.append({
                    "fila": row["_source_position"],
                    "report_id": row["report_id"],
                    "motivo": "registro duplicado en MariaDB",
                })
            else:
                unique_rows.append(row)

        values = [(
            row["mongo_id"], row["report_id"], row["report_date"], row["asset_id"],
            row["asset_name"], row["event_name"], row["latitude"], row["longitude"],
            row["location"], row["speed"], row["gps_speed"], row["heading"],
            row["odometer"], row["ignition"], row["event_id"], row["sequential_id"],
            row["cartography_limit_speed"], row["driver_name"], row["driver_last_name"],
            row["driver_document_number"],
        ) for row in unique_rows]
        if values:
            cursor.executemany(
                """INSERT INTO raw_gps
                (mongo_id,report_id,report_date,asset_id,asset_name,event_name,latitude,
                 longitude,location,speed,gps_speed,heading,odometer,ignition,event_id,
                 sequential_id,cartography_limit_speed,driver_name,driver_last_name,
                 driver_document_number,ingestado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        UTC_TIMESTAMP(6))""",
                values,
            )
        _insert_latest_positions(cursor, unique_rows)

        try:
            previous_rejections = json.loads(state.get("rechazos_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            previous_rejections = []
        rejection_history = (previous_rejections + rejected)[-50:]
        new_read = int(state["leidos"]) + len(documents)
        new_valid = int(state["validos"]) + len(unique_rows)
        new_rejected = int(state["rechazados"]) + len(rejected)
        new_blocks = int(state.get("bloques_completados") or 0) + (1 if documents else 0)
        blocks_total = math.ceil(total / config.batch_size) if total else 0
        status = "completado" if len(documents) < config.batch_size else "ejecutando"
        cursor.execute(
            """UPDATE raw_gps_estado SET ultimo_mongo_id=%s,leidos=%s,validos=%s,
            rechazados=%s,total_origen=%s,bloques_completados=%s,bloques_totales=%s,
            tamano_bloque=%s,estado=%s,ultimo_error=NULL,rechazos_json=%s,
            ultima_carga=UTC_TIMESTAMP(6),ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""",
            (last_id, new_read, new_valid, new_rejected, total, new_blocks, blocks_total,
             config.batch_size, status, json.dumps(rejection_history, ensure_ascii=False)),
        )
        if documents:
            cursor.execute(
                """INSERT INTO etl_bloques
                (numero_bloque,cursor_desde,cursor_hasta,leidos,validos,rechazados,
                 estado,iniciado_en,completado_en)
                VALUES (%s,%s,%s,%s,%s,%s,'completado',%s,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE cursor_desde=VALUES(cursor_desde),
                cursor_hasta=VALUES(cursor_hasta),leidos=VALUES(leidos),validos=VALUES(validos),
                rechazados=VALUES(rechazados),estado='completado',
                completado_en=VALUES(completado_en)""",
                (new_blocks, state["ultimo_mongo_id"], last_id, len(documents),
                 len(unique_rows), len(rejected), started_at),
            )
        connection.commit()

    pending_documents = max(0, total - new_read)
    return {
        "estado": status,
        "recibidos": len(documents),
        "validos": len(unique_rows),
        "rechazados": len(rejected),
        "total_leidos": new_read,
        "total_validos": new_valid,
        "total_rechazados": new_rejected,
        "total_origen": total,
        "bloques_completados": new_blocks,
        "bloques_totales": blocks_total,
        "bloques_pendientes": max(0, blocks_total - new_blocks),
        "documentos_pendientes": pending_documents,
        "progreso_pct": round(new_read * 100 / total, 4) if total else 100.0,
    }


def run_batches(config, max_blocks=1):
    """Procesa N bloques, o todos cuando max_blocks es None, y deja un estado explícito."""
    if max_blocks is not None and max_blocks < 1:
        raise ValueError("max_blocks debe ser mayor o igual a 1")
    results = []
    while max_blocks is None or len(results) < max_blocks:
        result = run_once(config)
        results.append(result)
        if result["estado"] == "completado" or result["recibidos"] == 0:
            mark_status("completado")
            break
    if results and results[-1]["estado"] != "completado":
        mark_status("pausado")
        results[-1]["estado"] = "pausado"
    return results


def record_error(message):
    mark_status("error", message)
