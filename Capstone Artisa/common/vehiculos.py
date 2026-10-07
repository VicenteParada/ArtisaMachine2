"""Clasificación de vehículos: una sola regla para ETL, API en vivo, endpoints y Machine.

Fuente de verdad: las tablas `vehiculos` y `tipos_vehiculo`. El catálogo inicial
(DB/vehiculos_data.sql) viene de patentes.csv. Un vehículo que aparece y no está
en el catálogo se registra automáticamente usando su asset_name; una clasificación
existente nunca se sobrescribe, así que una corrección manual en la tabla siempre
prevalece sobre la regla automática.

Nada de este módulo borra reportes: los vehículos que no son camión siguen en
MariaDB y simplemente se excluyen al leer.
"""

import re
import unicodedata


TIPO_CAMION = "camion"
TIPO_SIN_CLASIFICAR = "sin_clasificar"

# Orden importante: "camioneta" debe evaluarse antes que cualquier regla de camión.
_REGLAS_NOMBRE = (
    (re.compile(r"camioneta", re.IGNORECASE), "camioneta"),
    (re.compile(r"furgon", re.IGNORECASE), "furgon"),
    (re.compile(r"mantenc", re.IGNORECASE), "mantencion"),
    (re.compile(r"minibus", re.IGNORECASE), "minibus"),
)

# Condición SQL reutilizable: el asset_id pertenece a un camión según el catálogo.
# Uso: f"... WHERE {sql_es_camion('gps.asset_id')}" (sin parámetros adicionales).
def sql_es_camion(columna_asset_id):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", columna_asset_id):
        raise ValueError("nombre de columna inválido")
    return (
        f"{columna_asset_id} IN (SELECT v.asset_id FROM vehiculos AS v "
        "INNER JOIN tipos_vehiculo AS t ON t.id_tipo = v.id_tipo "
        f"WHERE t.nombre = '{TIPO_CAMION}')"
    )


def _normalizar(texto):
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def tipo_por_nombre(asset_name):
    """Clasificación automática a partir del nombre que entrega el GPS.

    Los camiones se nombran "<ubicación>-<número>" (ej. "Lampa-826"); el resto trae
    una palabra que los delata (ej. "716-Camioneta-CesarM"). Sin nombre no se puede
    confirmar que sea camión: queda sin clasificar y no se muestra como camión.
    """
    if not asset_name or not str(asset_name).strip():
        return TIPO_SIN_CLASIFICAR
    normalizado = _normalizar(str(asset_name))
    for patron, tipo in _REGLAS_NOMBRE:
        if patron.search(normalizado):
            return tipo
    return TIPO_CAMION


def es_camion(asset_name):
    return tipo_por_nombre(asset_name) == TIPO_CAMION


def registrar_nuevos(cursor, vehiculos):
    """Registra en el catálogo los asset_id que aún no existen.

    `vehiculos` es un dict {asset_id: asset_name}. Usa INSERT IGNORE: si el
    vehículo ya está clasificado (manualmente o antes), no se toca. Debe llamarse
    dentro de la transacción que guarda los reportes, para que nunca quede un
    reporte de un vehículo sin clasificar. Devuelve cuántos se registraron.
    """
    if not vehiculos:
        return 0
    ids = list(vehiculos)
    marcadores = ",".join(["%s"] * len(ids))
    cursor.execute(f"SELECT asset_id FROM vehiculos WHERE asset_id IN ({marcadores})", ids)
    existentes = {row["asset_id"] for row in cursor.fetchall()}
    nuevos = [(asset_id, tipo_por_nombre(nombre)) for asset_id, nombre in vehiculos.items()
              if asset_id not in existentes]
    if not nuevos:
        return 0
    tipos = sorted({tipo for _, tipo in nuevos})
    # Sin depender de un índice único en nombre (la tabla heredada podría no tenerlo).
    cursor.executemany(
        "INSERT INTO tipos_vehiculo (nombre) SELECT %s FROM DUAL "
        "WHERE NOT EXISTS (SELECT 1 FROM tipos_vehiculo WHERE nombre = %s)",
        [(t, t) for t in tipos],
    )
    cursor.executemany(
        """INSERT IGNORE INTO vehiculos (asset_id, id_tipo, actualizado_en)
        SELECT %s, id_tipo, UTC_TIMESTAMP(6) FROM tipos_vehiculo WHERE nombre = %s
        ORDER BY id_tipo LIMIT 1""",
        nuevos,
    )
    return len(nuevos)


def sincronizar_desde_raw_gps(cursor):
    """Registra los vehículos de raw_gps que todavía no están en el catálogo.

    Pensado para la base persistente que ya tiene 23 M de reportes: lista los
    asset_id con el índice (asset_id, report_date) y busca un nombre solo para
    los vehículos nuevos, de a uno y por índice, sin recorrer la tabla completa.
    """
    cursor.execute("SELECT DISTINCT asset_id FROM raw_gps")
    todos = [row["asset_id"] for row in cursor.fetchall()]
    if not todos:
        return 0
    cursor.execute("SELECT asset_id FROM vehiculos")
    existentes = {row["asset_id"] for row in cursor.fetchall()}
    faltantes = {}
    for asset_id in todos:
        if asset_id in existentes:
            continue
        cursor.execute(
            "SELECT asset_name FROM raw_gps WHERE asset_id=%s AND asset_name IS NOT NULL "
            "ORDER BY report_date DESC LIMIT 1",
            (asset_id,),
        )
        row = cursor.fetchone()
        faltantes[asset_id] = row["asset_name"] if row else None
    return registrar_nuevos(cursor, faltantes)
