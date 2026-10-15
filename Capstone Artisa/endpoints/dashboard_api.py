"""Respuesta de /api/dashboard: la flota mes a mes desde el primer día con datos.

Todo sale del cierre diario (machine_learning/diario.py), no de los millones de reportes:
reporte_diario guarda por camión y día los reportes GPS válidos (sin repetidos ni
coordenadas inválidas) y los km observados; reporte_diario_estado guarda por
día si está cerrado y cuántos reportes se descartaron. Solo camiones del catálogo.

El mes en curso incluye el día de hoy hasta el último cálculo (cada pocos minutos).
"""

from datetime import date

import pymysql

from common import dias

# Un camión "sin reportar" no tiene reportes ni ayer ni hoy.
SIN_REPORTES_DIAS = 2


def _catalogo(cursor):
    cursor.execute(
        """SELECT v.asset_id FROM vehiculos AS v INNER JOIN tipos_vehiculo AS t ON t.id_tipo = v.id_tipo
           WHERE t.nombre = 'camion' ORDER BY v.asset_id"""
    )
    return [fila["asset_id"] for fila in cursor.fetchall()]


def _mes(fecha):
    return fecha.strftime("%Y-%m")


def _meses(desde, hasta):
    meses, actual = [], date(desde.year, desde.month, 1)
    while actual <= hasta:
        meses.append(_mes(actual))
        actual = date(actual.year + (actual.month == 12), actual.month % 12 + 1, 1)
    return meses


def fleet_dashboard(cursor, hoy=None):
    """None mientras no exista ningún día procesado (la API responde "pendiente")."""
    hoy = hoy or dias.hoy()
    try:
        cursor.execute(
            """SELECT DATE_FORMAT(fecha, '%Y-%m') AS mes, COUNT(*) AS dias,
                      SUM(estado = 'cerrado') AS cerrados, SUM(reportes) AS reportes,
                      SUM(descartados) AS descartados, MIN(fecha) AS desde, MAX(fecha) AS hasta
               FROM reporte_diario_estado GROUP BY mes ORDER BY mes"""
        )
    except pymysql.err.ProgrammingError:
        return None
    calidad = cursor.fetchall()
    if not calidad:
        return None
    desde = min(fila["desde"] for fila in calidad)
    meses = _meses(desde, hoy)
    posicion = {mes: k for k, mes in enumerate(meses)}
    catalogo = _catalogo(cursor)
    cursor.execute(
        """SELECT DATE_FORMAT(fecha, '%Y-%m') AS mes, asset_id, MAX(asset_name) AS asset_name,
                  SUM(reportes) AS reportes, SUM(km_observados) AS km,
                  SUM(reportes > 0) AS dias_activos, MAX(CASE WHEN reportes > 0 THEN fecha END) AS ultimo
           FROM reporte_diario GROUP BY mes, asset_id"""
    )
    camiones = {asset_id: {"patente": asset_id, "nombre": None, "reportes": [0] * len(meses),
                           "km": [0.0] * len(meses),
                           "dias_activos": [0] * len(meses), "ultimo_dia": None}
                for asset_id in catalogo}
    for fila in cursor.fetchall():
        camion = camiones.get(fila["asset_id"])
        k = posicion.get(fila["mes"])
        if camion is None or k is None:  # no es camión del catálogo, o fuera del rango
            continue
        camion["reportes"][k] = int(fila["reportes"] or 0)
        camion["km"][k] = round(float(fila["km"] or 0), 1)
        camion["dias_activos"][k] = int(fila["dias_activos"] or 0)
        if fila["asset_name"]:
            camion["nombre"] = fila["asset_name"]
        if fila["ultimo"] and (camion["ultimo_dia"] is None or fila["ultimo"] > camion["ultimo_dia"]):
            camion["ultimo_dia"] = fila["ultimo"]

    lista = []
    for camion in camiones.values():
        camion["total"] = sum(camion["reportes"])
        ultimo = camion["ultimo_dia"]
        camion["dias_sin_reportes"] = (hoy - ultimo).days if ultimo else None
        camion["ultimo_dia"] = ultimo.isoformat() if ultimo else None
        lista.append(camion)
    lista.sort(key=lambda c: (-c["total"], c["patente"]))

    por_mes = {fila["mes"]: fila for fila in calidad}
    flota = []
    for k, mes in enumerate(meses):
        fila = por_mes.get(mes) or {}
        flota.append({
            "mes": mes,
            "reportes": sum(c["reportes"][k] for c in lista),
            "km": round(sum(c["km"][k] for c in lista), 1),
            "camiones_activos": sum(1 for c in lista if c["reportes"][k] > 0),
            "dias": int(fila.get("dias") or 0),
            "dias_cerrados": int(fila.get("cerrados") or 0),
            "descartados": int(fila.get("descartados") or 0),
        })
    sin_reportar = sorted(
        ({"patente": c["patente"], "nombre": c["nombre"], "ultimo_dia": c["ultimo_dia"],
          "dias_sin_reportes": c["dias_sin_reportes"]}
         for c in lista if c["dias_sin_reportes"] is None or c["dias_sin_reportes"] >= SIN_REPORTES_DIAS),
        key=lambda c: (c["dias_sin_reportes"] is not None, -(c["dias_sin_reportes"] or 0), c["patente"]),
    )
    return {
        "status": "ok", "zona": dias.ZONA, "hoy": hoy.isoformat(),
        "desde": desde.isoformat(), "meses": meses, "mes_en_curso": _mes(hoy),
        "flota": flota, "camiones": lista, "sin_reportar": sin_reportar,
        "sin_reportes_dias": SIN_REPORTES_DIAS,
        "totales": {
            "reportes": sum(m["reportes"] for m in flota), "km": round(sum(m["km"] for m in flota), 1),
            "camiones": len(lista),
            "dias": sum(m["dias"] for m in flota), "dias_cerrados": sum(m["dias_cerrados"] for m in flota),
            "descartados": sum(m["descartados"] for m in flota),
        },
    }


def truck_days(cursor, asset_id):
    """Reportes de un camión día por día desde el primer cierre: [[fecha, reportes, km]]."""
    try:
        cursor.execute(
            "SELECT fecha, reportes, km_observados FROM reporte_diario WHERE asset_id=%s ORDER BY fecha",
            (asset_id,),
        )
    except pymysql.err.ProgrammingError:
        return {"patente": asset_id, "dias": []}
    return {"patente": asset_id,
            "dias": [[f["fecha"].isoformat(), int(f["reportes"]), round(float(f["km_observados"]), 1)]
                     for f in cursor.fetchall()]}
