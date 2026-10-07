"""Ejecuta la clasificación de rutas sobre los viajes de los días cerrados y la publica.

Lee `viajes_camion` (escritos por el cierre diario), empalma los viajes partidos en la
medianoche, agrupa en rutas (machine_learning/rutas.py) y guarda una versión nueva en
`rutas_version` / `rutas_clasificadas` / `rutas_vehiculo`. La versión se activa en la misma
transacción, así la API nunca ve una clasificación a medio escribir. Se conservan las
últimas CONSERVAR_VERSIONES versiones (son datos derivados, recalculables).
"""

from datetime import datetime, timedelta, timezone
import json
import os
import time

from machine_learning.rutas import ViajeRuta, construir_rutas
from machine_learning.viajes import VIAJE_MIN_KM, VIAJE_MIN_PUNTOS, Viaje, empalmar

VENTANA_DIAS = int(os.environ.get("RUTAS_VENTANA_DIAS", "90"))
CONSERVAR_VERSIONES = 3


def cargar_faenas(cursor):
    cursor.execute("SELECT id, nombre, ST_AsGeoJSON(geocerca) AS geojson FROM faenas WHERE geocerca IS NOT NULL")
    faenas = []
    for fila in cursor.fetchall():
        geo = fila["geojson"]
        faenas.append({"id": fila["id"], "nombre": fila["nombre"],
                       "geojson": geo if isinstance(geo, dict) else json.loads(geo)})
    return faenas


def ventana(cursor, dias=VENTANA_DIAS):
    """(desde, hasta) entre los días cerrados más recientes."""
    cursor.execute("SELECT MAX(fecha) AS hasta FROM reporte_diario_estado WHERE estado='cerrado'")
    fila = cursor.fetchone() or {}
    hasta = fila.get("hasta")
    if hasta is None:
        return None, None
    return hasta - timedelta(days=dias - 1), hasta


def cargar_viajes(cursor, desde, hasta):
    cursor.execute(
        """SELECT v.* FROM viajes_camion AS v
           INNER JOIN reporte_diario_estado AS e ON e.fecha = v.fecha AND e.estado = 'cerrado'
           WHERE v.fecha >= %s AND v.fecha <= %s ORDER BY v.asset_id, v.inicio""",
        (desde, hasta),
    )
    viajes = []
    for fila in cursor.fetchall():
        tramos = [[(lat, lon) for lon, lat in tramo] for tramo in json.loads(fila["geometria"])]
        origen = (fila["origen_lat"], fila["origen_lon"]) if fila["origen_lat"] is not None else None
        destino = (fila["destino_lat"], fila["destino_lon"]) if fila["destino_lat"] is not None else None
        viajes.append(Viaje(asset_id=fila["asset_id"], inicio=fila["inicio"], fin=fila["fin"], origen=origen,
                            destino=destino, tramos=tramos, puntos=fila["puntos"], km=fila["km"],
                            con_saltos=bool(fila["con_saltos"]), abierto_inicio=bool(fila["abierto_inicio"]),
                            abierto_fin=bool(fila["abierto_fin"])))
    return viajes


def comuna(location):
    """'S/N, Calama, Provincia de El Loa, Región de Antofagasta, Chile' -> 'Calama'."""
    partes = [p.strip() for p in str(location or "").split(",") if p.strip()]
    for i, parte in enumerate(partes):
        if parte.lower().startswith(("provincia", "región", "region")) and i > 0:
            return partes[i - 1]
    utiles = [p for p in partes if p.upper() != "S/N" and not p[:1].isdigit() and p.lower() != "chile"]
    return utiles[1] if len(utiles) > 1 else (utiles[0] if utiles else None)


def nombrador(cursor):
    """Devuelve una función que agrega 'comuna' a cada lugar frecuente, leyendo la dirección
    del GPS en el momento de una de sus paradas (consultas por el índice camión+fecha)."""
    def nombrar(lugares):
        for lugar in lugares.values():
            if lugar["tipo"] != "lugar":
                continue
            for asset_id, momento in lugar.get("muestras", [])[:3]:
                encontrado = None
                for tabla in ("telemetria_api", "raw_gps"):
                    try:
                        cursor.execute(
                            f"""SELECT location FROM {tabla}
                                WHERE asset_id=%s AND report_date BETWEEN %s AND %s
                                  AND location IS NOT NULL AND location <> '' LIMIT 1""",
                            (asset_id, momento - timedelta(minutes=10), momento + timedelta(minutes=10)),
                        )
                    except Exception:
                        continue
                    fila = cursor.fetchone()
                    if fila and comuna(fila["location"]):
                        encontrado = comuna(fila["location"])
                        break
                if encontrado:
                    lugar["comuna"] = encontrado
                    break
    return nombrar


def preparar(viajes):
    """Empalma y separa los viajes clasificables de los que no tienen origen o destino."""
    completos, sin_extremos, cortos = [], 0, 0
    for v in empalmar(viajes):
        if v.origen is None or v.destino is None or not v.tramos:
            sin_extremos += 1
        elif v.km < VIAJE_MIN_KM or v.puntos < VIAJE_MIN_PUNTOS:
            cortos += 1
        else:
            completos.append(ViajeRuta(asset_id=v.asset_id, inicio=v.inicio, fin=v.fin, origen=v.origen,
                                       destino=v.destino, km=v.km, puntos=v.puntos, con_saltos=v.con_saltos,
                                       tramos=v.tramos))
    return completos, {"viajes_sin_origen_o_destino": sin_extremos, "viajes_cortos": cortos}


def guardar_version(connection, desde, hasta, rutas, lugares, metricas):
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO rutas_version (creado_en,desde,hasta,activa,metricas_json,lugares_json)
               VALUES (UTC_TIMESTAMP(6),%s,%s,0,%s,%s)""",
            (desde, hasta, json.dumps(metricas, ensure_ascii=False, default=str),
             json.dumps(lugares, ensure_ascii=False)),
        )
        version = cursor.lastrowid
        if rutas:
            cursor.executemany(
                """INSERT INTO rutas_clasificadas
                (version_id,route_id,label,origen,destino,usage_count,unique_vehicle_count,observed_points,
                 km_mediana,usage_level,geometry_source,geometria,primer_viaje,ultimo_viaje,vehiculos_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(version, r["route_id"], r["label"][:255], r["origen"], r["destino"], r["usage_count"],
                  r["unique_vehicle_count"], r["observed_points"], r["km_mediana"], r["usage_level"],
                  r["geometry_source"], json.dumps(r["geometry"], separators=(",", ":")),
                  r["primer_viaje"], r["ultimo_viaje"], json.dumps(r["vehiculos"])) for r in rutas],
            )
        ultimo = {}
        for r in rutas:
            for v in r["miembros"]:
                if v.asset_id not in ultimo or v.fin > ultimo[v.asset_id][1]:
                    ultimo[v.asset_id] = (r["route_id"], v.fin)
        if ultimo:
            cursor.executemany(
                "INSERT INTO rutas_vehiculo (version_id,asset_id,route_id,ultimo_viaje) VALUES (%s,%s,%s,%s)",
                [(version, asset, route, fin) for asset, (route, fin) in ultimo.items()],
            )
        cursor.execute("UPDATE rutas_version SET activa = (id = %s)", (version,))
        cursor.execute("SELECT id FROM rutas_version ORDER BY id DESC LIMIT 18446744073709551615 OFFSET %s",
                       (CONSERVAR_VERSIONES,))
        viejas = [fila["id"] for fila in cursor.fetchall()]
        for vieja in viejas:
            cursor.execute("DELETE FROM rutas_clasificadas WHERE version_id=%s", (vieja,))
            cursor.execute("DELETE FROM rutas_vehiculo WHERE version_id=%s", (vieja,))
            cursor.execute("DELETE FROM rutas_version WHERE id=%s", (vieja,))
    connection.commit()
    return version


def ejecutar(connection, dias=VENTANA_DIAS):
    comienzo = time.monotonic()
    with connection.cursor() as cursor:
        desde, hasta = ventana(cursor, dias)
        if desde is None:
            return None
        viajes = cargar_viajes(cursor, desde, hasta)
        faenas = cargar_faenas(cursor)
    connection.commit()
    completos, excluidos = preparar(viajes)
    with connection.cursor() as cursor:
        rutas, lugares, metricas = construir_rutas(completos, faenas, nombrar=nombrador(cursor))
    connection.commit()
    for lugar in lugares.values():
        lugar.pop("muestras", None)  # solo se usaban para nombrar
    metricas.update(excluidos)
    metricas["viajes_leidos"] = len(viajes)
    metricas["camiones"] = len({v.asset_id for v in completos})
    metricas["segundos"] = round(time.monotonic() - comienzo, 1)
    version = guardar_version(connection, desde, hasta, rutas, lugares, metricas)
    print(f"Rutas: versión {version} · {desde}..{hasta} · {metricas['viajes_clasificados']} viajes -> "
          f"{metricas['rutas']} rutas {metricas.get('por_nivel', {})} en {metricas['segundos']} s", flush=True)
    return metricas


def necesita_recalculo(cursor, horas_minimas=2):
    """Hay días cerrados calculados después de la última versión (y pasó el mínimo)."""
    cursor.execute("SELECT MAX(creado_en) AS ultima FROM rutas_version")
    ultima = (cursor.fetchone() or {}).get("ultima")
    cursor.execute("SELECT MAX(calculado_en) AS cambio FROM reporte_diario_estado WHERE estado='cerrado'")
    cambio = (cursor.fetchone() or {}).get("cambio")
    if cambio is None:
        return False
    if ultima is None:
        return True
    return cambio > ultima and datetime.now(timezone.utc).replace(tzinfo=None) - ultima >= timedelta(hours=horas_minimas)
