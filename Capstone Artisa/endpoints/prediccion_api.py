"""Respuesta de /api/prediccion: la versión activa de machine_learning/prediccion.py.

Sin versión activa devuelve None y la API responde "pendiente".
"""

from collections import defaultdict
import json

import pymysql

ESCALA = 100  # celdas de 0,01° (igual que machine_learning/prediccion.py)
METRICAS_PUBLICAS = ("metodo", "motivo", "errores", "correlaciones", "mae_publicado", "mae_modelo",
                     "mae_referencia", "objetivo", "dias_historia", "historia_desde", "historia_hasta",
                     "dias_prueba", "celdas", "camiones", "error_por_dia", "dias_descartados")


def _centro(c):
    return round((c + 0.5) / ESCALA, 4)


def prediction(cursor, tipo="calor"):
    """tipo "calor": celdas [lat, lon, reportes]; tipo "rutas": tramos [lat1, lon1, lat2, lon2, camiones]."""
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
    return {
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
