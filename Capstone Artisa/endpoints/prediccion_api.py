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
                     "tramos_publicados", "tramos_con_camino")


def _centro(c):
    return round((c + 0.5) / ESCALA, 4)


def _formas(cursor, version_id):
    """{"lat1,lon1,lat2,lon2": [[lat, lon], ...]}: el camino real de cada tramo. La clave es la
    misma que arma la web con los cuatro primeros valores del tramo."""
    try:
        cursor.execute("SELECT a_lat, a_lon, b_lat, b_lon, puntos FROM prediccion_trazo WHERE version_id=%s",
                       (version_id,))
    except pymysql.err.ProgrammingError:
        return {}  # versión anterior a los trazos: la web dibuja el tramo recto
    return {",".join(repr(_centro(fila[c])) for c in ("a_lat", "a_lon", "b_lat", "b_lon")): json.loads(fila["puntos"])
            for fila in cursor.fetchall()}


def prediction(cursor, tipo="calor"):
    """tipo "calor": celdas [lat, lon, reportes]; tipo "rutas": tramos [lat1, lon1, lat2, lon2, camiones]
    y "trazos" con la forma real de cada tramo."""
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
        cursor.execute(
            "SELECT fecha, a_lat, a_lon, b_lat, b_lon, valor FROM prediccion_tramo WHERE version_id=%s ORDER BY fecha",
            (version["id"],),
        )
        for fila in cursor.fetchall():
            por_dia[fila["fecha"]].append([_centro(fila["a_lat"]), _centro(fila["a_lon"]), _centro(fila["b_lat"]),
                                           _centro(fila["b_lon"]), round(float(fila["valor"]), 1)])
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
        respuesta["trazos"] = _formas(cursor, version["id"])
    return respuesta
