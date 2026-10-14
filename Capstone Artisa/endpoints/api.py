"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import os
import re
import socket
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pymysql

from common import dias
from common.db import connect_db
from common.http import JsonHandler, serve
from endpoints import camiones_api, prediccion_api, rutas_api


STARTED_AT = time.monotonic()
ETL_STALE_SECONDS = max(60, int(os.getenv("TELEMETRY_INTERVAL_SECONDS", "30")) * 3)


PENDING_RESPONSES = {
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
    "/api/prediccion": {
        "status": "pending", "dias": [],
        "message": "La predicción se calcula con al menos 21 días cerrados.",
    },
    "/api/camiones": {"status": "pending", "camiones": [], "resumen": None},
    "/api/prediccion-rutas": {
        "status": "pending", "dias": [],
        "message": "La predicción por ruta se está calculando (o faltan días cerrados: necesita al menos 21). "
                   "Se actualiza sola en unos minutos.",
    },
}
BUILDERS = {"/api/mapa": rutas_api.classified_map, "/api/dashboard": rutas_api.classified_dashboard,
            "/api/prediccion": prediccion_api.prediction,
            "/api/prediccion-rutas": lambda cursor: prediccion_api.prediction(cursor, "rutas"),
            "/api/camiones": camiones_api.fleet}


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


def truck_ids(cursor):
    cursor.execute(
        """SELECT v.asset_id FROM vehiculos AS v INNER JOIN tipos_vehiculo AS t ON t.id_tipo = v.id_tipo
           WHERE t.nombre = 'camion'"""
    )
    return {row["asset_id"] for row in cursor.fetchall()}


def latest_telemetry(cursor, limit):
    api_state = company_api_state(cursor)
    if api_state is None:
        return TELEMETRY_PENDING
    # Se recorre el índice por fecha desde lo más nuevo y se filtran los camiones en Python.
    # Un "WHERE asset_id IN (subconsulta) ORDER BY ... LIMIT" lleva a MariaDB a ordenar
    # millones de filas antes de filtrar y supera el tiempo límite.
    trucks = truck_ids(cursor)
    reports, before, batch = [], None, max(200, limit * 10)
    for _ in range(20):
        condition = "WHERE report_date < %s" if before else ""
        cursor.execute(
            f"""SELECT report_id, asset_id, asset_name, report_date, latitude, longitude, speed,
                      heading, ignition, event_name, driver_name, driver_last_name,
                      TIMESTAMPDIFF(SECOND, report_date, UTC_TIMESTAMP(6)) AS edad_segundos
               FROM telemetria_api FORCE INDEX (idx_telemetria_api_fecha_recibido) {condition}
               ORDER BY report_date DESC LIMIT %s""",
            ((before, batch) if before else (batch,)),
        )
        rows = cursor.fetchall()
        reports.extend(row for row in rows if row["asset_id"] in trucks)
        if len(reports) >= limit or len(rows) < batch:
            break
        before = rows[-1]["report_date"]
    return {"status": "ok", "source": "company_api", "conexion": api_state,
            "reportes": reports[:limit]}


def telemetry_summary(cursor):
    api_state = company_api_state(cursor)
    if api_state is None:
        return TELEMETRY_SUMMARY_PENDING
    cursor.execute(
        """SELECT MIN(report_date) AS desde, MAX(report_date) AS hasta
           FROM telemetria_api FORCE INDEX (idx_telemetria_api_fecha_recibido)"""
    )
    span = cursor.fetchone() or {}
    # Último reporte de cada vehículo con el índice (asset_id, report_date): MariaDB lee una
    # entrada por vehículo (loose index scan) en vez de recorrer 24 h de reportes.
    cursor.execute(
        """SELECT asset_id, MAX(report_date) AS ultimo FROM telemetria_api
           FORCE INDEX (idx_telemetria_api_asset_fecha) GROUP BY asset_id"""
    )
    limite = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    activos = {row["asset_id"] for row in cursor.fetchall() if row["ultimo"] and row["ultimo"] >= limite}
    vehicles = len(activos & truck_ids(cursor))
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


ETL_HISTORICO = os.getenv("ETL_HISTORICO", "1").strip() != "0"


def pipeline_state(cursor, detailed=True):
    """Estado de las etapas. Con ETL_HISTORICO=0 (instalación de la empresa, sin MongoDB ni
    el JSON histórico) la etapa ETL es el conector de la API de la empresa."""
    etl = None
    if ETL_HISTORICO:
        etl_columns = "*" if detailed else "estado"
        cursor.execute(
            f"SELECT {etl_columns}, TIMESTAMPDIFF(SECOND,ultima_revision,UTC_TIMESTAMP()) AS hace_segundos "
            "FROM raw_gps_estado WHERE id=1"
        )
        etl = cursor.fetchone()
    api_state = company_api_state(cursor)
    if api_state is None:
        api_component = "pending"
    elif api_state["conexion"] in ("en_vivo", "sin_reportes_recientes", "sincronizando"):
        api_component = "ok"
    else:
        api_component = "unavailable"
    if ETL_HISTORICO:
        etl_ready = bool(etl and etl["estado"] != "error"
                         and etl["hace_segundos"] is not None and 0 <= etl["hace_segundos"] < ETL_STALE_SECONDS)
        etl_component = "ok" if etl_ready else "unavailable"
    else:
        etl_component = api_component
    healthy = etl_component in ("ok", "pending") and api_component != "unavailable"
    state = {
        "status": "ok" if healthy else "degraded",
        "servicio": "artisa-api",
        "database": {"engine": "MariaDB", "status": "ok"},
        "componentes": {
            "etl": etl_component,
            "mariadb": "ok",
            "machine": "ok" if rutas_api.active_version(cursor) else "pending",
            "endpoints": "ok",
            "telemetria_api": api_component,
        },
        "etl_historico": ETL_HISTORICO,
    }
    if not detailed:
        return {key: state[key] for key in ("status", "servicio", "componentes")}
    state.update({
        "etl": ({k: v for k, v in etl.items() if k not in ("id", "rechazos_json")} | {
            "rechazos": json.loads(etl["rechazos_json"])
        }) if etl else None,
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
    Los días van de 00:00 a 00:00 hora de Chile, igual que el cierre diario.
    t = segundos transcurridos desde las 00:00 (Chile) de 'desde'; lat/lon x 1e5."""
    # Leer por el índice de fecha evita ordenar millones de filas en MariaDB.
    # La misma deduplicación y el orden por camión/hora se completan en memoria.
    # Histórico (raw_gps) + telemetría en vivo (telemetria_api): los mapas del cuaderno
    # se calculan igual sobre ambos. Un reporte presente en las dos tablas se cuenta una vez.
    inicio, fin = dias.limites_utc(desde)[0], dias.limites_utc(hasta)[1]
    rows = []
    for table in ("raw_gps", "telemetria_api"):
        try:
            cursor.execute(
                f"""SELECT asset_id, report_date, latitude, longitude
                   FROM {table} WHERE report_date >= %s AND report_date < %s""",
                (inicio, fin),
            )
        except pymysql.err.ProgrammingError:
            continue  # telemetria_api aún no creada
        rows.extend(cursor.fetchall())
    trucks = truck_ids(cursor)  # filtrar en Python evita un plan lento con IN (subconsulta)
    rows = [row for row in rows if row["asset_id"] in trucks]
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
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "zona": dias.ZONA, "camiones": list(indices),
            "cam": cam, "t": t, "lat": lat, "lon": lon}


# --- Historial diario (cierre de 00:00 a 00:00 hora Chile, machine_learning/diario.py) ---

ASSET_RE = re.compile(r"[A-Za-z0-9_-]{1,16}")
JOURNEY_RE = re.compile(r"/api/vehiculos/([A-Za-z0-9_-]{1,16})/recorrido")
TRUCK_RE = re.compile(r"/api/camiones/([A-Za-z0-9_-]{1,16})")
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
                  viajes, tramos_unidos, paradas, minutos_detenido
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


def monthly_full_report(cursor, year, month):
    """Detalle mensual exportable: una fila por camión y día ya procesado."""
    first = date(year, month, 1)
    following = date(year + (month == 12), month % 12 + 1, 1)
    try:
        cursor.execute(
            """SELECT r.fecha, e.estado, r.asset_id, r.asset_name, r.reportes,
                      r.primer_reporte, r.ultimo_reporte, r.km_observados, r.viajes,
                      r.tramos_unidos, r.paradas, r.minutos_detenido
               FROM reporte_diario AS r
               INNER JOIN reporte_diario_estado AS e ON e.fecha = r.fecha
               WHERE r.fecha >= %s AND r.fecha < %s
               ORDER BY r.fecha, r.asset_name, r.asset_id""",
            (first, following),
        )
    except pymysql.err.ProgrammingError:
        rows = []
    else:
        rows = cursor.fetchall()
    return {"mes": f"{year:04d}-{month:02d}", "zona": dias.ZONA, "filas": rows}


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
    if path in ("/api/reportes/mes", "/api/reportes/mes-completo"):
        mes = _param(query, "mes") or dias.hoy().strftime("%Y-%m")
        match = re.fullmatch(r"(\d{4})-(\d{2})", mes)
        if not match or not 1 <= int(match.group(2)) <= 12:
            return None, "Usa ?mes=YYYY-MM"
        handler = monthly_full_report if path == "/api/reportes/mes-completo" else monthly_report
        return handler, (int(match.group(1)), int(match.group(2)))
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


# --- Caché de /api/mapa y /api/dashboard -----------------------------------------
# El mapa recorre la última posición de cada camión: con MariaDB ocupada (cierre diario)
# puede tardar. Se calcula como máximo una vez por minuto, una sola consulta a la vez, y si
# un cálculo falla se entrega el último resultado bueno.
CACHE_SECONDS = 60
_cache = {}
# Un candado por ruta: el mapa, el dashboard y la predicción se calculan en paralelo; antes
# compartían uno y la predicción esperaba al mapa hasta agotar el tiempo del navegador.
_cache_locks = {}


def cached_result(path):
    now = time.monotonic()
    entry = _cache.get(path)
    if entry and now - entry[0] < CACHE_SECONDS:
        return entry[1]
    with _cache_locks.setdefault(path, threading.Lock()):
        entry = _cache.get(path)
        if entry and time.monotonic() - entry[0] < CACHE_SECONDS:
            return entry[1]  # otra petición lo calculó mientras esperábamos
        try:
            with connect_db(read_timeout=80) as connection, connection.cursor() as cursor:
                result = BUILDERS[path](cursor)
        except pymysql.MySQLError as error:
            print(f"Endpoints: no se pudo calcular {path} ({type(error).__name__}); se usa el último resultado",
                  flush=True)
            return entry[1] if entry else None
        result = result if result is not None else PENDING_RESPONSES[path]
        _cache[path] = (time.monotonic(), result)
        return result


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
                                         "/api/reportes/mes-completo?mes=YYYY-MM",
                                         "/api/reportes/camion?asset_id=X&fecha=YYYY-MM-DD",
                                         "/api/camiones/X?fecha=YYYY-MM-DD",
                                         *PENDING_RESPONSES]})
            return
        if path in BUILDERS:
            # Resultados de Machine si existe una clasificación activa; si no, "pendiente".
            result = cached_result(path)
            if result is None:
                self.respond(503, {"status": "error", "error": "No se pudo calcular el mapa; se reintentará."})
                return
            self.respond(200, result)
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
        truck = TRUCK_RE.fullmatch(path)
        if truck:
            try:
                dia = date.fromisoformat(_param(parsed.query, "fecha") or dias.hoy().isoformat())
            except ValueError:
                self.respond(400, {"error": "Usa ?fecha=YYYY-MM-DD"})
                return
            if dia > dias.hoy():
                self.respond(400, {"error": "La fecha no puede ser futura"})
                return
            try:
                with connect_db(read_timeout=60) as connection, connection.cursor() as cursor:
                    result = camiones_api.truck_detail(cursor, truck.group(1), dia)
            except pymysql.MySQLError as error:
                self.database_error(error)
                return
            self.respond(200 if result else 404, result or {"error": "No es un camión del catálogo"})
            return
        if path in PENDING_RESPONSES:
            self.respond(200, PENDING_RESPONSES[path])
            return
        history = None
        if path in ("/api/reportes/dia", "/api/reportes/mes", "/api/reportes/mes-completo",
                    "/api/reportes/camion"):
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
            # Un rango de varios días de GPS puede tardar más que el límite normal de 15 s
            # (Nginx corta a los 150 s).
            read_timeout = 120 if path == "/api/gps" else 15
            with connect_db(autocommit=False, read_timeout=read_timeout) as connection, connection.cursor() as cursor:
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
