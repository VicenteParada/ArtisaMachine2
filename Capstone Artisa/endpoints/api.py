"""Endpoints leen MariaDB; Machine procesa las solicitudes por separado."""

import json
import os
import re
import socket
import time
import unicodedata
from datetime import date, datetime, time as clock_time, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pymysql

from common.db import connect_db
from common.http import JsonHandler, serve


STARTED_AT = time.monotonic()
ETL_STALE_SECONDS = max(60, int(os.getenv("TELEMETRY_INTERVAL_SECONDS", "30")) * 3)
CHILE_ZONE = ZoneInfo("America/Santiago")


PENDING_RESPONSES = {
    "/api/telemetria": {"status": "pending", "source": "company_api", "reportes": []},
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
    "/api/telemetria/resumen": {
        "status": "pending", "source": "company_api", "reportes": None, "vehiculos": None,
        "desde": None, "hasta": None,
    },
}


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
        },
    }
    if not detailed:
        return {key: state[key] for key in ("status", "servicio", "componentes")}
    state.update({
        "etl": ({k: v for k, v in etl.items() if k not in ("id", "rechazos_json")} | {
            "rechazos": json.loads(etl["rechazos_json"])
        }) if etl else None,
        "modelo_telemetria": None,
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 2),
    })
    return state


GPS_MAX_DIAS = 14


def es_camion_para_mapa(asset_name):
    """Reconoce los camiones del histórico por el nombre GPS; descarta tipos dudosos."""
    if not isinstance(asset_name, str) or not asset_name.strip():
        return False
    name = unicodedata.normalize("NFKD", asset_name).encode("ascii", "ignore").decode("ascii").casefold().strip()
    if re.search(r"camioneta|furgon|mantenc|\b(?:bus|auto|automovil|suv|van|pickup|ambulancia|moto)\b", name):
        return False
    # Los camiones históricos se nombran por lugar y número (Lampa-826,
    # Codelco Calama 835); un nombre desconocido no confirma que sea camión.
    return bool(re.search(r"\bcamion\b", name) or re.fullmatch(r".+[- ]\d{2,5}", name))


def dashboard_dates(query, today=None):
    """Acepta solo jornadas cerradas en la zona horaria de Chile."""
    params = parse_qs(query)
    today = today or datetime.now(CHILE_ZONE).date()
    try:
        fecha = date.fromisoformat(params["fecha"][0]) if "fecha" in params else None
        days = int(params.get("dias", ["7"])[0])
    except ValueError:
        return None, None, None, "Usa ?fecha=YYYY-MM-DD&dias=7 o 30"
    if days not in (7, 30):
        return None, None, None, "dias debe ser 7 o 30"
    if fecha is not None and fecha.year < 1000:
        return None, None, None, "La fecha está fuera del rango de MariaDB"
    if fecha is not None and fecha >= today:
        return None, None, None, "El dashboard solo muestra días cerrados anteriores al corte"
    return fecha, today, days, None


def chile_midnight_utc(day):
    """Límite local de jornada convertido al UTC sin zona que guarda raw_gps."""
    local_midnight = datetime.combine(day, clock_time.min, tzinfo=CHILE_ZONE)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def dashboard_routes(cursor, fecha, days):
    """Punto de integración para las rutas diarias que Machine publicará en MariaDB."""
    return {
        "status": "pending", "metrica": "vehiculos_unicos_por_ruta",
        "niveles": {"HIGH": None, "MEDIUM": None, "LOW": None},
        "mas_usadas": [], "menos_usadas": [], "tendencia": [],
    }


def dashboard_day(cursor, fecha, corte, days):
    """Resumen GPS del intervalo [00:00 Chile, 00:00 siguiente) en UTC."""
    cursor.execute(
        "SELECT MAX(report_date) AS ultima_fecha FROM raw_gps WHERE report_date < %s",
        (chile_midnight_utc(corte),),
    )
    last_report = cursor.fetchone()["ultima_fecha"]
    last_day = last_report.replace(tzinfo=timezone.utc).astimezone(CHILE_ZONE).date() if last_report else None
    selected = fecha or last_day or (corte - timedelta(days=1))
    following = selected + timedelta(days=1)
    start_utc = chile_midnight_utc(selected)
    end_utc = chile_midnight_utc(following)
    cursor.execute(
        "SELECT COUNT(*) AS reportes FROM raw_gps WHERE report_date >= %s AND report_date < %s",
        (start_utc, end_utc),
    )
    hay_reportes = int(cursor.fetchone()["reportes"]) > 0
    reportes = 0
    vehiculos = 0
    if hay_reportes:
        # DISTINCT por fecha obliga a leer filas aleatorias del histórico completo.
        # El índice (asset_id, report_date) permite clasificar y contar cada camión rápidamente.
        cursor.execute("SELECT DISTINCT asset_id FROM raw_gps")
        asset_ids = [row["asset_id"] for row in cursor.fetchall()]
        for asset_id in asset_ids:
            cursor.execute(
                """SELECT asset_name FROM raw_gps
                   WHERE asset_id = %s AND report_date >= %s AND report_date < %s
                     AND asset_name IS NOT NULL AND asset_name <> ''
                   ORDER BY report_date DESC LIMIT 1""",
                (asset_id, start_utc, end_utc),
            )
            name_row = cursor.fetchone()
            if not name_row or not es_camion_para_mapa(name_row["asset_name"]):
                continue
            cursor.execute(
                """SELECT COUNT(*) AS reportes FROM raw_gps
                   WHERE asset_id = %s AND report_date >= %s AND report_date < %s""",
                (asset_id, start_utc, end_utc),
            )
            count = int(cursor.fetchone()["reportes"])
            reportes += count
            vehiculos += count > 0
    return {
        "status": "ready" if reportes else "empty",
        "fuente": "raw_gps", "fecha": selected.isoformat(),
        "dias_tendencia": days,
        "ultima_fecha_cerrada": last_day.isoformat() if last_day else None,
        "periodo": {"zona_horaria": "America/Santiago",
                    "desde_utc": start_utc.isoformat() + "Z",
                    "hasta_utc_exclusivo": end_utc.isoformat() + "Z"},
        "reportes": reportes, "vehiculos": vehiculos,
        "rutas": dashboard_routes(cursor, selected, days),
    }


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
    """Reportes GPS de camiones del rango, en columnas compactas para el mapa.
    t = segundos desde 'desde' 00:00 (hora tal cual viene en report_date); lat/lon x 1e5."""
    # Leer por el índice de fecha evita ordenar millones de filas en MariaDB.
    # La misma deduplicación y el orden por camión/hora se completan en memoria.
    cursor.execute(
        """SELECT asset_id, asset_name, report_date, latitude, longitude
           FROM raw_gps
           WHERE report_date >= %s AND report_date < %s
           ORDER BY report_date""",
        (desde, hasta + timedelta(days=1)),
    )
    inicio = datetime(desde.year, desde.month, desde.day)
    indices, cam, t, lat, lon = {}, [], [], [], []
    rows = cursor.fetchall()
    # Clasificar una vez por vehículo con su último nombre no vacío del rango.
    # Así un reporte aislado sin asset_name no rompe la trayectoria del camión.
    names = {}
    for row in rows:
        if row["asset_name"]:
            names[row["asset_id"]] = row["asset_name"]
    trucks = {asset_id for asset_id, name in names.items() if es_camion_para_mapa(name)}
    rows = [row for row in rows if row["asset_id"] in trucks]
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


def gps_vehicle_request(query):
    """Valida la selección puntual de un vehículo en un día del explorador GPS."""
    params = parse_qs(query)
    try:
        value = params["fecha"][0]
        selected = date.fromisoformat(value)
        asset_id = params["asset_id"][0].strip()
    except (KeyError, ValueError, IndexError):
        return None, None, "Usa ?fecha=YYYY-MM-DD&asset_id=ID"
    if selected.year < 1000 or value != selected.isoformat() or not 0 < len(asset_id) <= 16:
        return None, None, "Fecha o identificador de vehículo inválido"
    return selected, asset_id, None


def gps_vehicle_detail(cursor, selected, asset_id):
    """Solo primer y último reporte del día, usando el índice por vehículo y fecha."""
    start = datetime.combine(selected, clock_time.min)
    end = start + timedelta(days=1)
    rows = []
    for direction in ("ASC", "DESC"):
        cursor.execute(
            f"""SELECT report_date, asset_name, driver_name, driver_last_name
                FROM raw_gps WHERE asset_id = %s AND report_date >= %s AND report_date < %s
                ORDER BY report_date {direction} LIMIT 1""",
            (asset_id, start, end),
        )
        rows.append(cursor.fetchone())
    if rows[0] is None:
        return None
    cursor.execute(
        """SELECT asset_name FROM raw_gps
           WHERE asset_id = %s AND report_date >= %s AND report_date < %s
             AND asset_name IS NOT NULL AND asset_name <> ''
           ORDER BY report_date DESC LIMIT 1""",
        (asset_id, start, end),
    )
    name_row = cursor.fetchone()
    if not name_row or not es_camion_para_mapa(name_row["asset_name"]):
        return None
    return {"fecha": selected.isoformat(), "asset_id": asset_id,
            "primer_reporte": rows[0], "ultimo_reporte": rows[1]}


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
                                         "/api/dashboard?fecha=YYYY-MM-DD&dias=7",
                                         *PENDING_RESPONSES]})
            return
        if path in PENDING_RESPONSES:
            self.respond(200, PENDING_RESPONSES[path])
            return
        if path not in ("/health", "/api/estado", "/api/faenas", "/api/gps",
                        "/api/gps/vehiculo", "/api/dashboard"):
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        if path == "/api/gps":
            desde, hasta, error = gps_range(parsed.query)
            if error:
                self.respond(400, {"error": error})
                return
        if path == "/api/gps/vehiculo":
            fecha, asset_id, error = gps_vehicle_request(parsed.query)
            if error:
                self.respond(400, {"error": error})
                return
        if path == "/api/dashboard":
            fecha, corte, days, error = dashboard_dates(parsed.query)
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
                elif path == "/api/gps/vehiculo":
                    detail = gps_vehicle_detail(cursor, fecha, asset_id)
                    self.respond(200 if detail else 404, detail or {"error": "Vehículo sin reportes ese día"})
                elif path == "/api/dashboard":
                    self.respond(200, dashboard_day(cursor, fecha, corte, days))
        except pymysql.MySQLError as error:
            if path == "/health":
                self.log_error("Error de MariaDB: %s", type(error).__name__)
                self.respond(503, {
                    "status": "degraded", "servicio": "artisa-api",
                    "componentes": {"mariadb": "unavailable", "etl": "unavailable",
                                    "machine": "pending", "endpoints": "ok"},
                })
            else:
                self.database_error(error)


if __name__ == "__main__":
    serve(Handler, "Endpoints")
