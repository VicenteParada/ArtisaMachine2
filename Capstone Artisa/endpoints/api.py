"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import os
import re
import socket
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pymysql

from common import dias
from common.db import connect_db
from common.http import JsonHandler, serve
from common.vehiculos import sql_es_camion
from endpoints import rutas_api


STARTED_AT = time.monotonic()
ETL_STALE_SECONDS = max(60, int(os.getenv("TELEMETRY_INTERVAL_SECONDS", "30")) * 3)


PENDING_RESPONSES = {
    "/api/modelo/telemetria": {"status": "pending", "message": "Pendiente de integración Machine"},
    "/api/mapa": {
        "schema_version": 1, "source_mode": "PENDING_MACHINE", "snapshot_date": None,
        "routes": [], "trips": [], "vehicles": [], "readings": [],
        "summary": {key: None for key in (
            "trip_count", "vehicle_count", "route_count", "window_report_count",
            "reading_count", "available_report_count")},
        "ranking": {"most_used": None, "least_used": None},
        "metadata": {
            "status": "pending",
            "classification": "Pendiente de procesamiento Machine.",
            "geometry": "Pendiente de procesamiento Machine.",
            "road_matching": "Pendiente de procesamiento Machine.",
            "data_retention": "Sin resultados Machine disponibles.",
        },
    },
    "/api/dashboard": {
        "status": "pending", "calidad": {"validos": None},
        "operacion": {"viajes": None},
        "rutas": {"HIGH": None, "MEDIUM": None, "LOW": None},
    },
}


# Telemetría actual (servicio telemetria). Mientras la API de la empresa no esté
# configurada, estas rutas responden exactamente lo mismo que antes ("pending").
TELEMETRY_PENDING = {"status": "pending", "source": "company_api", "reportes": []}
TELEMETRY_SUMMARY_PENDING = {
    "status": "pending", "source": "company_api", "reportes": None, "vehiculos": None,
    "desde": None, "hasta": None,
}
COMPANY_API_INTERVAL = max(5, int(os.getenv("COMPANY_API_INTERVAL_SECONDS", "30")))
# Sin un contacto exitoso en este plazo, la conexión se informa como caída.
COMPANY_API_STALE_SECONDS = max(90, COMPANY_API_INTERVAL * 3)
# Un reporte más nuevo que esto se presenta como posición reciente; uno más antiguo,
# como último dato conocido.
RECENT_REPORT_SECONDS = 15 * 60
TELEMETRY_MAX_LIMIT = 200
CONNECTION_ERRORS = {"sin_conexion", "error_origen", "respuesta_invalida", "error_interno"}


def company_api_state(cursor):
    """Estado de la conexión con la API de la empresa, o None si no está configurada."""
    try:
        cursor.execute(
            """SELECT estado, ultimo_error, fallos_consecutivos, leidos, validos, rechazados,
                      ultimo_exito, ultimo_dato, desconectado_desde, advertencia,
                      TIMESTAMPDIFF(SECOND, ultimo_exito, UTC_TIMESTAMP(6)) AS exito_hace,
                      TIMESTAMPDIFF(SECOND, ultimo_dato, UTC_TIMESTAMP(6)) AS dato_hace,
                      TIMESTAMPDIFF(SECOND, ultima_revision, UTC_TIMESTAMP(6)) AS revision_hace
               FROM telemetria_api_estado WHERE id=1"""
        )
    except pymysql.err.ProgrammingError:
        return None  # tabla aún no creada: el servicio telemetria no ha arrancado nunca
    row = cursor.fetchone()
    if not row or row["estado"] == "sin_configurar":
        return None
    reachable = (row["estado"] in ("conectado", "sincronizando") and row["exito_hace"] is not None
                 and row["exito_hace"] < COMPANY_API_STALE_SECONDS)
    worker_alive = row["revision_hace"] is not None and row["revision_hace"] < max(600, COMPANY_API_STALE_SECONDS * 2)
    if not worker_alive:
        conexion = "servicio_detenido"
    elif reachable and row["estado"] == "sincronizando":
        conexion = "sincronizando"
    elif reachable:
        recent = row["dato_hace"] is not None and row["dato_hace"] < RECENT_REPORT_SECONDS
        conexion = "en_vivo" if recent else "sin_reportes_recientes"
    elif row["estado"] == "error_configuracion":
        conexion = "error_configuracion"
    else:
        conexion = "reconectando"
    return {
        "conexion": conexion,
        "estado": row["estado"],
        "ultimo_error": row["ultimo_error"],
        "fallos_consecutivos": row["fallos_consecutivos"],
        "ultimo_exito": row["ultimo_exito"],
        "ultimo_dato": row["ultimo_dato"],
        "desconectado_desde": row["desconectado_desde"],
        "advertencia": row["advertencia"],
        "leidos": row["leidos"], "guardados": row["validos"], "rechazados": row["rechazados"],
        "reporte_reciente_segundos": RECENT_REPORT_SECONDS,
    }


def telemetry_limit(query):
    raw = parse_qs(query).get("limite", ["30"])[0]
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= TELEMETRY_MAX_LIMIT else None


def latest_telemetry(cursor, limit):
    api_state = company_api_state(cursor)
    if api_state is None:
        return TELEMETRY_PENDING
    cursor.execute(
        f"""SELECT report_id, asset_id, asset_name, report_date, latitude, longitude, speed,
                  heading, ignition, event_name,
                  TIMESTAMPDIFF(SECOND, report_date, UTC_TIMESTAMP(6)) AS edad_segundos
           FROM telemetria_api WHERE {sql_es_camion("asset_id")}
           ORDER BY report_date DESC LIMIT %s""",
        (limit,),
    )
    return {"status": "ok", "source": "company_api", "conexion": api_state,
            "reportes": cursor.fetchall()}


def telemetry_summary(cursor):
    api_state = company_api_state(cursor)
    if api_state is None:
        return TELEMETRY_SUMMARY_PENDING
    cursor.execute("SELECT MIN(report_date) AS desde, MAX(report_date) AS hasta FROM telemetria_api")
    span = cursor.fetchone() or {}
    cursor.execute(
        f"""SELECT COUNT(DISTINCT asset_id) AS vehiculos FROM telemetria_api
           WHERE report_date >= UTC_TIMESTAMP(6) - INTERVAL 24 HOUR AND {sql_es_camion("asset_id")}"""
    )
    vehicles = (cursor.fetchone() or {}).get("vehiculos")
    return {"status": "ok", "source": "company_api", "conexion": api_state,
            "reportes": api_state["guardados"], "vehiculos": vehicles,
            "vehiculos_ventana_horas": 24, "desde": span.get("desde"), "hasta": span.get("hasta")}


def vehicle_catalog(cursor):
    try:
        cursor.execute(
            """SELECT t.nombre AS tipo, COUNT(*) AS vehiculos FROM vehiculos AS v
               INNER JOIN tipos_vehiculo AS t ON t.id_tipo = v.id_tipo
               GROUP BY t.nombre ORDER BY t.nombre"""
        )
    except pymysql.err.ProgrammingError:
        return None
    return {row["tipo"]: row["vehiculos"] for row in cursor.fetchall()}


def pipeline_state(cursor, detailed=True):
    etl_columns = "*" if detailed else "estado"
    cursor.execute(
        f"SELECT {etl_columns}, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
        "FROM raw_gps_estado WHERE id=1"
    )
    etl = cursor.fetchone()
    etl_ready = bool(etl and etl["estado"] != "error"
                     and etl["hace_segundos"] is not None and 0 <= etl["hace_segundos"] < ETL_STALE_SECONDS)
    state = {
        "status": "ok" if etl_ready else "degraded",
        "servicio": "artisa-api",
        "database": {"engine": "MariaDB", "status": "ok"},
        "componentes": {
            "etl": "ok" if etl_ready else "unavailable",
            "mariadb": "ok",
            "machine": "pending",
            "endpoints": "ok",
            "telemetria_api": "pending",
        },
    }
    if rutas_api.active_version(cursor):
        state["componentes"]["machine"] = "ok"
    api_state = company_api_state(cursor)
    if api_state is not None:
        live = api_state["conexion"] in ("en_vivo", "sin_reportes_recientes", "sincronizando")
        state["componentes"]["telemetria_api"] = "ok" if live else "unavailable"
    if not detailed:
        return {key: state[key] for key in ("status", "servicio", "componentes")}
    state.update({
        "etl": ({k: v for k, v in etl.items() if k not in ("id", "rechazos_json")} | {
            "rechazos": json.loads(etl["rechazos_json"])
        }) if etl else None,
        "modelo_telemetria": None,
        "telemetria_api": api_state,
        "catalogo_vehiculos": vehicle_catalog(cursor),
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 2),
    })
    return state


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
    """Reportes GPS de camiones del rango, en columnas compactas para el mapa del navegador.
    Las camionetas, furgones y demás vehículos siguen guardados en raw_gps, pero no se muestran.
    t = segundos desde 'desde' 00:00 (hora tal cual viene en report_date); lat/lon x 1e5."""
    # Leer por el índice de fecha evita ordenar millones de filas en MariaDB.
    # La misma deduplicación y el orden por camión/hora se completan en memoria.
    # Histórico (raw_gps) + telemetría en vivo (telemetria_api): los mapas del cuaderno
    # se calculan igual sobre ambos. Un reporte presente en las dos tablas se cuenta una vez.
    rows = []
    for table in ("raw_gps", "telemetria_api"):
        try:
            cursor.execute(
                f"""SELECT asset_id, report_date, latitude, longitude
                   FROM {table}
                   WHERE report_date >= %s AND report_date < %s AND {sql_es_camion("asset_id")}""",
                (desde, hasta + timedelta(days=1)),
            )
        except pymysql.err.ProgrammingError:
            continue  # telemetria_api aún no creada
        rows.extend(cursor.fetchall())
    inicio = datetime(desde.year, desde.month, desde.day)
    indices, cam, t, lat, lon = {}, [], [], [], []
    rows.sort(key=lambda row: (row["asset_id"], row["report_date"]))
    seen = set()
    for row in rows:
        key = (row["asset_id"], row["report_date"], row["latitude"], row["longitude"])
        if key in seen:
            continue
        seen.add(key)
        cam.append(indices.setdefault(row["asset_id"], len(indices)))
        t.append(round((row["report_date"] - inicio).total_seconds()))
        lat.append(round(row["latitude"] * 1e5))
        lon.append(round(row["longitude"] * 1e5))
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "camiones": list(indices),
            "cam": cam, "t": t, "lat": lat, "lon": lon}


# --- Historial diario (cierre de 00:00 a 00:00 hora Chile, machine_learning/diario.py) ---

ASSET_RE = re.compile(r"[A-Za-z0-9_-]{1,16}")
JOURNEY_RE = re.compile(r"/api/vehiculos/([A-Za-z0-9_-]{1,16})/recorrido")
DETALLE_MAX = 1000


def _param(query, name):
    values = parse_qs(query).get(name)
    return values[0] if values else None


def daily_report(cursor, dia):
    try:
        cursor.execute("SELECT * FROM reporte_diario_estado WHERE fecha=%s", (dia,))
    except pymysql.err.ProgrammingError:
        return {"fecha": dia, "zona": dias.ZONA, "estado": None, "camiones": []}
    estado = cursor.fetchone()
    cursor.execute(
        """SELECT asset_id, asset_name, reportes, primer_reporte, ultimo_reporte, km_observados,
                  viajes, tramos_unidos
           FROM reporte_diario WHERE fecha=%s ORDER BY reportes DESC, asset_id""",
        (dia,),
    )
    return {"fecha": dia, "zona": dias.ZONA, "hoy": dias.hoy(), "estado": estado,
            "camiones": cursor.fetchall()}


def monthly_report(cursor, year, month):
    first = date(year, month, 1)
    following = date(year + (month == 12), month % 12 + 1, 1)
    try:
        cursor.execute(
            """SELECT fecha, estado, reportes, camiones, km_observados, calculado_en, cerrado_en, recalculos
               FROM reporte_diario_estado WHERE fecha >= %s AND fecha < %s ORDER BY fecha""",
            (first, following),
        )
    except pymysql.err.ProgrammingError:
        return {"mes": f"{year:04d}-{month:02d}", "zona": dias.ZONA, "dias": []}
    return {"mes": f"{year:04d}-{month:02d}", "zona": dias.ZONA, "hoy": dias.hoy(), "dias": cursor.fetchall()}


def truck_day(cursor, asset_id, dia, limit, after):
    """Reportes de un camión en un día local, paginados por (report_date, report_id)."""
    inicio, fin = dias.limites_utc(dia)
    rows = []
    for table in ("telemetria_api", "raw_gps"):
        extra_columns = "ignition" if table == "telemetria_api" else "NULL AS ignition"
        conditions = "asset_id=%s AND report_date >= %s AND report_date < %s"
        params = [asset_id, inicio, fin]
        if after:
            conditions += " AND (report_date > %s OR (report_date = %s AND report_id > %s))"
            params += [after[0], after[0], after[1]]
        try:
            cursor.execute(
                f"""SELECT report_id, asset_name, report_date, latitude, longitude, speed,
                          event_name, {extra_columns}
                   FROM {table} WHERE {conditions}
                   ORDER BY report_date, report_id LIMIT %s""",
                (*params, limit + 1),
            )
        except pymysql.err.ProgrammingError:
            continue
        rows.extend(cursor.fetchall())
    rows.sort(key=lambda row: (row["report_date"], row["report_id"]))
    unique, seen = [], set()
    for row in rows:
        if row["report_id"] in seen:
            continue
        seen.add(row["report_id"])
        unique.append(row)
    page = unique[:limit]
    more = len(unique) > limit
    next_cursor = None
    if more and page:
        last = page[-1]
        next_cursor = last["report_date"].isoformat() + "|" + last["report_id"]
    return {"asset_id": asset_id, "fecha": dia, "zona": dias.ZONA, "reportes": page,
            "siguiente": next_cursor}


def history_request(path, query):
    """Valida parámetros. Devuelve (handler, args) o (None, mensaje_error)."""
    if path == "/api/reportes/mes":
        mes = _param(query, "mes") or dias.hoy().strftime("%Y-%m")
        match = re.fullmatch(r"(\d{4})-(\d{2})", mes)
        if not match or not 1 <= int(match.group(2)) <= 12:
            return None, "Usa ?mes=YYYY-MM"
        return monthly_report, (int(match.group(1)), int(match.group(2)))
    try:
        dia = date.fromisoformat(_param(query, "fecha") or "")
    except ValueError:
        return None, "Usa ?fecha=YYYY-MM-DD"
    if dia > dias.hoy():
        return None, "La fecha no puede ser futura"
    if path == "/api/reportes/dia":
        return daily_report, (dia,)
    asset_id = _param(query, "asset_id") or ""
    if not ASSET_RE.fullmatch(asset_id):
        return None, "asset_id inválido"
    try:
        limit = int(_param(query, "limite") or "500")
    except ValueError:
        limit = 0
    if not 1 <= limit <= DETALLE_MAX:
        return None, f"limite debe estar entre 1 y {DETALLE_MAX}"
    after = None
    raw_after = _param(query, "despues")
    if raw_after:
        try:
            moment, identifier = raw_after.split("|", 1)
            after = (datetime.fromisoformat(moment.replace("Z", "")), identifier)
        except ValueError:
            return None, "despues inválido"
    return truck_day, (asset_id, dia, limit, after)


class Handler(JsonHandler):
    def database_error(self, error):
        self.log_error("Error de MariaDB: %s", type(error).__name__)
        self.respond(503, {
            "status": "error",
            "database": {"engine": "MariaDB", "status": "unavailable"},
        })

    def do_POST(self):
        self.respond(404, {"error": "Ruta no encontrada"})

    do_DELETE = do_POST

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            self.respond(200, {"servicio": "artisa-api", "contenedor": socket.gethostname(),
                               "mensaje": "Endpoints Artisa",
                               "rutas": ["/health", "/api/estado", "/api/faenas",
                                         "/api/gps?desde=YYYY-MM-DD&hasta=YYYY-MM-DD",
                                         "/api/telemetria?limite=30", "/api/telemetria/resumen",
                                         "/api/reportes/dia?fecha=YYYY-MM-DD", "/api/reportes/mes?mes=YYYY-MM",
                                         "/api/reportes/camion?asset_id=X&fecha=YYYY-MM-DD",
                                         *PENDING_RESPONSES]})
            return
        if path in ("/api/mapa", "/api/dashboard"):
            # Resultados de Machine si existe una clasificación activa; si no, "pendiente".
            try:
                with connect_db(read_timeout=60) as connection, connection.cursor() as cursor:
                    result = (rutas_api.classified_map if path == "/api/mapa" else rutas_api.classified_dashboard)(cursor)
            except pymysql.MySQLError as error:
                self.database_error(error)
                return
            self.respond(200, result if result is not None else PENDING_RESPONSES[path])
            return
        journey = JOURNEY_RE.fullmatch(path)
        if journey:
            try:
                with connect_db(read_timeout=60) as connection, connection.cursor() as cursor:
                    result = rutas_api.truck_journey(cursor, journey.group(1))
            except pymysql.MySQLError as error:
                self.database_error(error)
                return
            self.respond(200 if result else 404, result or {"error": "Camión sin reportes"})
            return
        if path in PENDING_RESPONSES:
            self.respond(200, PENDING_RESPONSES[path])
            return
        history = None
        if path in ("/api/reportes/dia", "/api/reportes/mes", "/api/reportes/camion"):
            history, args = history_request(path, parsed.query)
            if history is None:
                self.respond(400, {"error": args})
                return
        elif path not in ("/health", "/api/estado", "/api/faenas", "/api/gps",
                          "/api/telemetria", "/api/telemetria/resumen"):
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        if path == "/api/telemetria":
            limit = telemetry_limit(parsed.query)
            if limit is None:
                self.respond(400, {"error": f"limite debe ser un entero entre 1 y {TELEMETRY_MAX_LIMIT}"})
                return
        if path == "/api/gps":
            desde, hasta, error = gps_range(parsed.query)
            if error:
                self.respond(400, {"error": error})
                return
        try:
            with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
                if path in ("/health", "/api/estado"):
                    state = pipeline_state(cursor, detailed=path == "/api/estado")
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
                elif path == "/api/gps":
                    self.respond(200, gps_points(cursor, desde, hasta))
                elif path == "/api/telemetria":
                    self.respond(200, latest_telemetry(cursor, limit))
                elif path == "/api/telemetria/resumen":
                    self.respond(200, telemetry_summary(cursor))
                elif history is not None:
                    self.respond(200, history(cursor, *args))
        except pymysql.MySQLError as error:
            if path == "/health":
                self.log_error("Error de MariaDB: %s", type(error).__name__)
                self.respond(503, {
                    "status": "degraded", "servicio": "artisa-api",
                    "componentes": {"mariadb": "unavailable", "etl": "unavailable",
                                    "machine": "pending", "endpoints": "ok",
                                    "telemetria_api": "unavailable"},
                })
            else:
                self.database_error(error)


if __name__ == "__main__":
    serve(Handler, "Endpoints")
