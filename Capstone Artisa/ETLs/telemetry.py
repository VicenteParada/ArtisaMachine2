"""Extrae telemetria GPS desde MongoDB (gps_data.raw_gps) y la carga de forma
idempotente en MariaDB (raw_gps), con la misma estructura y nombres de columna
que el documento de origen: _id (mongo_id), report_id, report_date, asset_id,
asset_name, event_name, latitude, longitude, location, speed,
cartography_limit_speed, driver_name, driver_last_name, driver_document_number.

Cursor por _id de Mongo (orden de insercion, siempre indexado): soporta colecciones
de millones de documentos sin cargarlos en memoria, pagina por pagina, y sigue
funcionando como cola infinita (no hay ventana de fechas fija). Cuando se cambie el
origen a la API con los JSON directos del GPS, solo hay que reemplazar fetch_batch,
siempre que la API entregue estos mismos campos.
"""

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
    # Importacion diferida: las transformaciones se pueden probar sin drivers instalados.
    from common.db import connect_db
    return connect_db(autocommit=autocommit)


def fetch_batch(config, after_id):
    """Trae como maximo config.batch_size documentos con _id > after_id, ordenados por _id."""
    from bson import ObjectId
    from bson.errors import InvalidId
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    query = {}
    if after_id:
        try:
            query["_id"] = {"$gt": ObjectId(after_id)}
        except InvalidId as error:
            raise ValueError("cursor invalido") from error
    try:
        with MongoClient(config.uri, serverSelectionTimeoutMS=config.timeout_seconds * 1000) as client:
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
        "cartography_limit_speed": _number(payload.get("cartography_limit_speed"), "cartography_limit_speed", 0),
        "event_name": _text(payload.get("event_name"), "event_name", 128),
        "driver_name": _text(payload.get("driver_name"), "driver_name", 64),
        "driver_last_name": _text(payload.get("driver_last_name"), "driver_last_name", 128),
        "driver_document_number": _text(payload.get("driver_document_number"), "driver_document_number", 32),
    }


def transform_rows(documents):
    accepted, rejected, seen = [], [], set()
    for position, document in enumerate(documents, start=1):
        try:
            normalized = normalize_row(document)
            if normalized["report_id"] in seen:
                raise ValueError("report_id duplicado en la pagina")
            seen.add(normalized["report_id"])
            accepted.append(normalized)
        except ValueError as error:
            rejected.append({
                "fila": position,
                "report_id": str(document.get("report_id", ""))[:128] if isinstance(document, dict) else None,
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
                (id,origen,ultimo_mongo_id,leidos,validos,rechazados,estado,ultimo_error,
                 rechazos_json,ultima_carga,ultima_revision)
                VALUES (1,%s,NULL,0,0,0,'pendiente',NULL,'[]',NULL,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE origen=VALUES(origen),ultimo_mongo_id=NULL,
                leidos=0,validos=0,rechazados=0,estado='pendiente',ultimo_error=NULL,
                rechazos_json='[]',ultima_carga=NULL,ultima_revision=VALUES(ultima_revision)""",
                (identity,),
            )
            connection.commit()
            return {"leidos": 0, "validos": 0, "rechazados": 0, "estado": "pendiente",
                    "ultimo_mongo_id": None, "rechazos_json": "[]"}
        connection.commit()
        return state


def run_once(config):
    state = _state_for(config)
    documents = fetch_batch(config, state["ultimo_mongo_id"])
    accepted, rejected = transform_rows(documents)
    try:
        previous_rejections = json.loads(state.get("rechazos_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        previous_rejections = []
    rejection_history = (previous_rejections + rejected)[-50:]
    last_id = str(documents[-1]["_id"]) if documents else state["ultimo_mongo_id"]
    new_read = int(state["leidos"]) + len(documents)
    new_valid = int(state["validos"]) + len(accepted)
    new_rejected = int(state["rechazados"]) + len(rejected)
    status = "ejecutando" if len(documents) == config.batch_size else "esperando"
    values = [(
        row["mongo_id"], row["report_id"], row["report_date"], row["asset_id"],
        row["asset_name"], row["event_name"], row["latitude"], row["longitude"],
        row["location"], row["speed"], row["cartography_limit_speed"],
        row["driver_name"], row["driver_last_name"], row["driver_document_number"],
    ) for row in accepted]
    with _connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        if values:
            cursor.executemany(
                """INSERT INTO raw_gps
                (mongo_id,report_id,report_date,asset_id,asset_name,event_name,latitude,
                 longitude,location,speed,cartography_limit_speed,driver_name,
                 driver_last_name,driver_document_number,ingestado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE mongo_id=VALUES(mongo_id),report_date=VALUES(report_date),
                asset_id=VALUES(asset_id),asset_name=VALUES(asset_name),event_name=VALUES(event_name),
                latitude=VALUES(latitude),longitude=VALUES(longitude),location=VALUES(location),
                speed=VALUES(speed),cartography_limit_speed=VALUES(cartography_limit_speed),
                driver_name=VALUES(driver_name),driver_last_name=VALUES(driver_last_name),
                driver_document_number=VALUES(driver_document_number),ingestado_en=VALUES(ingestado_en)""",
                values,
            )
        cursor.execute(
            """UPDATE raw_gps_estado SET ultimo_mongo_id=%s,leidos=%s,validos=%s,
            rechazados=%s,estado=%s,ultimo_error=NULL,rechazos_json=%s,
            ultima_carga=UTC_TIMESTAMP(6),ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""",
            (last_id, new_read, new_valid, new_rejected, status,
             json.dumps(rejection_history, ensure_ascii=False)),
        )
        connection.commit()
    return {"estado": status, "recibidos": len(documents), "validos": len(accepted),
            "rechazados": len(rejected), "total_validos": new_valid}


def record_error(message):
    safe_message = " ".join(str(message).split())[:255]
    with _connect_db() as connection, connection.cursor() as cursor:
        cursor.execute("""UPDATE raw_gps_estado SET estado='error',ultimo_error=%s,
            ultima_revision=UTC_TIMESTAMP(6) WHERE id=1""", (safe_message,))
