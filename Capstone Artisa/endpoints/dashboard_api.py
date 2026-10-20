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

# Un camión sin reportes ni ayer ni hoy se marca en la columna "Última señal".
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
    return {
        "status": "ok", "zona": dias.ZONA, "hoy": hoy.isoformat(),
        "desde": desde.isoformat(), "meses": meses, "mes_en_curso": _mes(hoy),
        "flota": flota, "camiones": lista,
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


# --- Nivel de reporte (cuaderno procesamientodatos.ipynb) ---------------------------
# Cada camión del catálogo queda en un nivel según cuántos reportes GPS válidos envió en
# el rango, comparado con el resto de la flota (cuartiles): A el 25 % que más reporta,
# D el 25 % que menos. Es relativo: siempre hay camiones en D aunque toda la flota reporte
# bien. Un día con falla es un día cerrado en que la flota envió menos de la mitad de la
# mediana diaria (caída de la plataforma GPS): no se descarta, solo se avisa.
NIVELES = "DCBA"
FALLA_FRACCION = 0.5


def _cuantil(valores, q):
    """Igual que numpy.quantile (interpolación lineal)."""
    orden = sorted(valores)
    posicion = (len(orden) - 1) * q
    abajo = int(posicion)
    arriba = min(abajo + 1, len(orden) - 1)
    return orden[abajo] + (orden[arriba] - orden[abajo]) * (posicion - abajo)


def nivel(reportes, cortes):
    """Igual que numpy.searchsorted(cortes, reportes, side="left"): un empate baja de nivel."""
    return NIVELES[sum(1 for corte in cortes if corte < reportes)]


def report_levels(cursor, desde, hasta, hoy=None):
    """Nivel A–D de cada camión entre desde y hasta (ambos incluidos). None si no hay cierres."""
    hoy = hoy or dias.hoy()
    try:
        cursor.execute("SELECT fecha, estado, reportes FROM reporte_diario_estado ORDER BY fecha")
    except pymysql.err.ProgrammingError:
        return None
    estados = cursor.fetchall()
    if not estados:
        return None
    cerrados = [int(fila["reportes"]) for fila in estados if fila["estado"] == "cerrado"]
    mediana = _cuantil(cerrados, 0.5) if cerrados else 0
    dias_falla = [{"fecha": fila["fecha"].isoformat(), "reportes": int(fila["reportes"])}
                  for fila in estados
                  if fila["estado"] == "cerrado" and desde <= fila["fecha"] <= hasta
                  and fila["reportes"] < FALLA_FRACCION * mediana]

    catalogo = _catalogo(cursor)
    cursor.execute(
        """SELECT asset_id, MAX(asset_name) AS asset_name, SUM(reportes) AS reportes
           FROM reporte_diario WHERE fecha BETWEEN %s AND %s GROUP BY asset_id""",
        (desde, hasta),
    )
    filas = {fila["asset_id"]: fila for fila in cursor.fetchall()}
    camiones = [{"patente": asset_id,
                 "nombre": (filas.get(asset_id) or {}).get("asset_name"),
                 "reportes": int((filas.get(asset_id) or {}).get("reportes") or 0)}
                for asset_id in catalogo]
    if not camiones:
        return None
    cortes = [round(_cuantil([c["reportes"] for c in camiones], q), 2) for q in (0.25, 0.5, 0.75)]
    for camion in camiones:
        camion["nivel"] = nivel(camion["reportes"], cortes)
    camiones.sort(key=lambda c: (-c["reportes"], c["patente"]))
    return {
        "status": "ok", "zona": dias.ZONA, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "incluye_hoy": desde <= hoy <= hasta,
        "cortes": {"d_hasta": cortes[0], "c_hasta": cortes[1], "b_hasta": cortes[2]},
        "conteo": {letra: sum(1 for c in camiones if c["nivel"] == letra) for letra in "ABCD"},
        "dias_falla": dias_falla, "mediana_diaria": round(mediana),
        "camiones": camiones,
    }
