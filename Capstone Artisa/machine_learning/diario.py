"""Cierre diario: el mismo trabajo de guias_ml/telemetria_colab.ipynb, día por día.

Fuente: telemetria_api (API de la empresa, en vivo) + raw_gps (histórico), solo
camiones según el catálogo. Mismas reglas que el cuaderno:
- gps_validos: coordenadas en rango y sin el 0,0.
- Se quitan repeticiones (mismo camión y misma hora) y reportes repetidos entre tablas.
- Dos reportes seguidos se unen como recorrido solo si la pausa es <= MAX_GAP_MIN y el
  salto no supera VEL_MAX_KMH (no se inventa recorrido ni se unen saltos de GPS).
- Una pausa mayor a GAP_VIAJE_MIN inicia un nuevo viaje.

Corte: el día va de 00:00 a 00:00 hora Chile (common/dias.py). Un viaje que cruza
la medianoche aparece en ambos días con sus propios reportes; ningún reporte se
pierde ni se cuenta dos veces.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import math

from common import dias
from common.vehiculos import sql_es_camion


MAX_GAP_MIN = 5
VEL_MAX_KMH = 150
GAP_VIAJE_MIN = 30
METROS_POR_GRADO = 111_000


def gps_valido(lat, lon):
    return (lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
            and not (lat == 0 and lon == 0))


def distancia_m(lat0, lon0, lat1, lon1):
    """Misma aproximación plana del cuaderno (suficiente entre reportes de 1 minuto)."""
    return math.hypot(lat1 - lat0, (lon1 - lon0) * math.cos(math.radians(lat1))) * METROS_POR_GRADO


@dataclass
class ResumenCamion:
    asset_id: str
    asset_name: str
    reportes: int
    primer_reporte: datetime
    ultimo_reporte: datetime
    km_observados: float
    viajes: int
    tramos_unidos: int


def resumir(filas):
    """filas: dicts con asset_id, asset_name, report_id, report_date (UTC), latitude, longitude.
    Devuelve (lista de ResumenCamion, descartados)."""
    vistos_id, vistos_hora = set(), set()
    por_camion = defaultdict(list)
    descartados = 0
    for fila in filas:
        lat, lon = fila.get("latitude"), fila.get("longitude")
        lat = None if lat is None else float(lat)
        lon = None if lon is None else float(lon)
        if not gps_valido(lat, lon) or fila.get("report_date") is None:
            descartados += 1
            continue
        clave_hora = (fila["asset_id"], fila["report_date"])
        if fila.get("report_id") in vistos_id or clave_hora in vistos_hora:
            continue  # el mismo reporte en ambas tablas o repetido por la ETL
        if fila.get("report_id"):
            vistos_id.add(fila["report_id"])
        vistos_hora.add(clave_hora)
        por_camion[fila["asset_id"]].append((fila["report_date"], lat, lon, fila.get("asset_name")))

    resumenes = []
    for asset_id, puntos in por_camion.items():
        puntos.sort(key=lambda p: p[0])
        metros, viajes, unidos = 0.0, 1, 0
        for (t0, la0, lo0, _), (t1, la1, lo1, _) in zip(puntos, puntos[1:]):
            horas = (t1 - t0).total_seconds() / 3600
            if horas * 60 > GAP_VIAJE_MIN:
                viajes += 1
            d = distancia_m(la0, lo0, la1, lo1)
            if 0 < horas <= MAX_GAP_MIN / 60 and d > 0 and d / 1000 <= VEL_MAX_KMH * horas:
                metros += d
                unidos += 1
        nombre = next((p[3] for p in reversed(puntos) if p[3]), None)
        resumenes.append(ResumenCamion(
            asset_id=asset_id, asset_name=nombre, reportes=len(puntos),
            primer_reporte=puntos[0][0], ultimo_reporte=puntos[-1][0],
            km_observados=round(metros / 1000, 2), viajes=viajes, tramos_unidos=unidos,
        ))
    resumenes.sort(key=lambda r: (-r.reportes, r.asset_id))
    return resumenes, descartados


# --- Base de datos -----------------------------------------------------------------

def leer_filas_dia(cursor, dia):
    """Reportes de camiones del día local, desde ambas tablas, por índice de fecha."""
    inicio, fin = dias.limites_utc(dia)
    filas = []
    for tabla in ("telemetria_api", "raw_gps"):
        cursor.execute(
            f"""SELECT report_id, asset_id, asset_name, report_date, latitude, longitude
               FROM {tabla} WHERE report_date >= %s AND report_date < %s
               AND {sql_es_camion("asset_id")}""",
            (inicio, fin),
        )
        filas.extend(cursor.fetchall())
    return filas


def ultimo_recibido(cursor, dia):
    """Último momento en que llegó (desde la API) un reporte de ese día."""
    inicio, fin = dias.limites_utc(dia)
    cursor.execute(
        "SELECT MAX(recibido_en) AS recibido FROM telemetria_api WHERE report_date >= %s AND report_date < %s",
        (inicio, fin),
    )
    fila = cursor.fetchone() or {}
    return fila.get("recibido")


def guardar_dia(connection, dia, estado, resumenes, descartados, recibido, recalculo=False):
    """Reemplaza el resumen del día en una sola transacción (son datos derivados)."""
    inicio, fin = dias.limites_utc(dia)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM reporte_diario WHERE fecha=%s", (dia,))
        if resumenes:
            cursor.executemany(
                """INSERT INTO reporte_diario
                (fecha,asset_id,asset_name,reportes,primer_reporte,ultimo_reporte,km_observados,viajes,tramos_unidos)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(dia, r.asset_id, r.asset_name, r.reportes, r.primer_reporte, r.ultimo_reporte,
                  r.km_observados, r.viajes, r.tramos_unidos) for r in resumenes],
            )
        cursor.execute(
            """INSERT INTO reporte_diario_estado
            (fecha,zona,inicio_utc,fin_utc,estado,reportes,camiones,km_observados,descartados,
             ultimo_recibido,calculado_en,cerrado_en,recalculos)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),
                    CASE WHEN %s='cerrado' THEN UTC_TIMESTAMP(6) ELSE NULL END,0)
            ON DUPLICATE KEY UPDATE zona=VALUES(zona),inicio_utc=VALUES(inicio_utc),fin_utc=VALUES(fin_utc),
            estado=VALUES(estado),reportes=VALUES(reportes),camiones=VALUES(camiones),
            km_observados=VALUES(km_observados),descartados=VALUES(descartados),
            ultimo_recibido=VALUES(ultimo_recibido),calculado_en=VALUES(calculado_en),
            cerrado_en=CASE WHEN VALUES(estado)='cerrado' THEN COALESCE(cerrado_en, UTC_TIMESTAMP(6)) ELSE NULL END,
            recalculos=recalculos+%s""",
            (dia, dias.ZONA, inicio, fin, estado, sum(r.reportes for r in resumenes), len(resumenes),
             round(sum(r.km_observados for r in resumenes), 2), descartados, recibido, estado,
             1 if recalculo else 0),
        )
    connection.commit()


def procesar_dia(connection, dia, estado, recalculo=False):
    print(f"Cierre diario: calculando {dia}…", flush=True)
    with connection.cursor() as cursor:
        filas = leer_filas_dia(cursor, dia)
        recibido = ultimo_recibido(cursor, dia)
    resumenes, descartados = resumir(filas)
    guardar_dia(connection, dia, estado, resumenes, descartados, recibido, recalculo)
    return len(resumenes), sum(r.reportes for r in resumenes)
