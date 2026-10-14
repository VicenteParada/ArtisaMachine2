"""Respuesta de /api/prediccion: la versión activa de machine_learning/prediccion.py.

Sin versión activa devuelve None y la API responde "pendiente".
"""

from collections import defaultdict
import json

import pymysql

ESCALA = 100  # celdas de 0,01° (igual que machine_learning/prediccion.py)
METRICAS_PUBLICAS = ("metodo", "motivo", "errores", "correlaciones", "mae_publicado", "mae_modelo",
                     "mae_referencia", "objetivo", "dias_historia", "historia_desde", "historia_hasta",
                     "dias_prueba", "celdas", "camiones", "error_por_dia", "dias_descartados",
                     "rutas_publicadas")


def _centro(c):
    return round((c + 0.5) / ESCALA, 4)


def _rutas(cursor, version_id):
    """{route_id: {"label", "lineas": [[[lat, lon], ...], ...]}}: línea real de cada ruta."""
    try:
        cursor.execute("SELECT route_id, label, geometria FROM prediccion_ruta_forma WHERE version_id=%s", (version_id,))
    except pymysql.err.ProgrammingError:
        return {}
    return {fila["route_id"]: {"label": fila["label"], "lineas": json.loads(fila["geometria"])}
            for fila in cursor.fetchall()}


def prediction(cursor, tipo="calor"):
    """tipo "calor": celdas [lat, lon, reportes]; tipo "rutas": rutas completas [route_id, camiones]
    y "rutas" con el nombre y la línea de cada una."""
    try:
        cursor.execute("SELECT * FROM prediccion_version WHERE activa=1 AND tipo=%s ORDER BY id DESC LIMIT 1",
                       (tipo,))
    except pymysql.err.ProgrammingError:
        return None  # tablas aún no creadas
    version = cursor.fetchone()
    if not version:
        return None
    metricas = json.loads(version.get("metricas_json") or "{}")
    por_dia = defaultdict(list)
    if tipo == "rutas":
        try:
            cursor.execute("SELECT fecha, route_id, valor FROM prediccion_ruta WHERE version_id=%s ORDER BY fecha",
                           (version["id"],))
        except pymysql.err.ProgrammingError:
            return None
        for fila in cursor.fetchall():
            por_dia[fila["fecha"]].append([fila["route_id"], round(float(fila["valor"]), 1)])
    else:
        cursor.execute(
            "SELECT fecha, celda_lat, celda_lon, valor FROM prediccion_celda WHERE version_id=%s ORDER BY fecha",
            (version["id"],),
        )
        for fila in cursor.fetchall():
            por_dia[fila["fecha"]].append([_centro(fila["celda_lat"]), _centro(fila["celda_lon"]),
                                           round(float(fila["valor"]), 1)])
    respuesta = {
        "status": "ok", "version": version["id"],
        "generado_en": version["creado_en"].isoformat() + "Z",
        "base_hasta": version["base_hasta"].isoformat(),
        "desde": version["desde"].isoformat(), "hasta": version["hasta"].isoformat(),
        "metricas": {k: metricas[k] for k in METRICAS_PUBLICAS if k in metricas},
        "tipo": tipo,
        "dias": [{"fecha": fecha.isoformat(), "total": round(sum(c[-1] for c in elementos)),
                  "elementos": len(elementos), "celdas": elementos}
                 for fecha, elementos in sorted(por_dia.items())],
    }
    if tipo == "rutas":
        respuesta["rutas"] = _rutas(cursor, version["id"])
    return respuesta
