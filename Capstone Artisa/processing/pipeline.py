"""Genera y persiste viajes, encadenamientos, paradas y rutas desde raw_gps.

Es una tarea explícita: no se ejecuta desde una petición HTTP ni con cada bloque ETL.
"""

from collections import defaultdict
from datetime import datetime
from hashlib import sha256
import json
import os

from common.db import connect_db, initialize_db
from common.map_matching import match_linestring
from common.telemetry_map import analyze_telemetry


STOP_SPEED_KMH = float(os.environ.get("STOP_SPEED_KMH", "1"))
STOP_MINUTES = float(os.environ.get("STOP_MINUTES", "5"))
CHAIN_GAP_MINUTES = float(os.environ.get("CHAIN_GAP_MINUTES", "120"))
ANALYTICS_MAX_REPORTS = int(os.environ.get("ANALYTICS_MAX_REPORTS", "100000"))


def _version(state):
    identity = "|".join((
        str(state.get("ultimo_mongo_id") or ""),
        str(state.get("validos") or 0),
        str(state.get("leidos") or 0),
    ))
    return sha256(identity.encode("utf-8")).hexdigest()


def _load_rows(cursor):
    cursor.execute(
        """SELECT report_id,asset_id,asset_name,report_date,latitude,longitude,
        location,speed,gps_speed,heading,odometer,ignition,event_name,driver_name,
        driver_last_name,driver_document_number FROM raw_gps
        ORDER BY report_date DESC,report_id DESC LIMIT %s""",
        (ANALYTICS_MAX_REPORTS,),
    )
    rows = cursor.fetchall()
    rows.reverse()
    return rows


def _trip_rows(rows_by_asset, trip):
    return [row for row in rows_by_asset.get(trip["vehicle_id"], [])
            if trip["started_at"] <= row["report_date"] <= trip["ended_at"]]


def _speed(row):
    value = row.get("gps_speed")
    if value is None:
        value = row.get("speed")
    return float(value or 0)


def _trip_details(trip, rows):
    duration = max(0.0, (trip["ended_at"] - trip["started_at"]).total_seconds() / 60)
    speeds = [_speed(row) for row in rows]
    driver = next((row for row in reversed(rows) if row.get("driver_document_number")), rows[-1])
    origin = rows[0].get("location") or f"{rows[0]['latitude']:.5f}, {rows[0]['longitude']:.5f}"
    destination = rows[-1].get("location") or f"{rows[-1]['latitude']:.5f}, {rows[-1]['longitude']:.5f}"
    return {
        "viaje_id": trip["trip_id"],
        "route_id": trip["route_id"],
        "asset_id": trip["vehicle_id"],
        "asset_name": rows[-1].get("asset_name"),
        "driver_document_number": driver.get("driver_document_number"),
        "driver_name": driver.get("driver_name"),
        "driver_last_name": driver.get("driver_last_name"),
        "inicio": trip["started_at"],
        "termino": trip["ended_at"],
        "origen": origin[:160],
        "destino": destination[:160],
        "distancia_km": float(trip["distance_km"]),
        "duracion_minutos": duration,
        "velocidad_promedio": sum(speeds) / len(speeds) if speeds else 0,
        "velocidad_maxima": max(speeds, default=0),
        "puntos_gps": len(rows),
        "geometria_json": json.dumps(trip["geometry"], separators=(",", ":")),
        "estado": "completado",
    }


def _detect_stops(trip, rows):
    stops = []
    start_index = None
    for index in range(len(rows) + 1):
        stopped = index < len(rows) and _speed(rows[index]) <= STOP_SPEED_KMH
        if stopped and start_index is None:
            start_index = index
        if (not stopped or index == len(rows)) and start_index is not None:
            end_index = index - 1
            start = rows[start_index]
            end = rows[end_index]
            minutes = max(0.0, (end["report_date"] - start["report_date"]).total_seconds() / 60)
            if minutes >= STOP_MINUTES:
                identity = f"{trip['trip_id']}|{start['report_date']}|{end['report_date']}"
                stops.append({
                    "parada_id": "STOP_" + sha256(identity.encode("utf-8")).hexdigest()[:24].upper(),
                    "viaje_id": trip["trip_id"],
                    "asset_id": trip["vehicle_id"],
                    "driver_document_number": start.get("driver_document_number"),
                    "inicio": start["report_date"],
                    "termino": end["report_date"],
                    "duracion_minutos": minutes,
                    "latitude": sum(float(row["latitude"]) for row in rows[start_index:end_index + 1])
                                / (end_index - start_index + 1),
                    "longitude": sum(float(row["longitude"]) for row in rows[start_index:end_index + 1])
                                 / (end_index - start_index + 1),
                    "zona": (start.get("location") or "Zona sin nombre")[:160],
                    "tipo": "detencion",
                    "anomala": minutes >= 60,
                })
            start_index = None
    return stops


def _assign_chains(trips):
    grouped = defaultdict(list)
    for trip in trips:
        grouped[(trip["asset_id"], trip.get("driver_document_number"))].append(trip)
    chains = []
    for (asset_id, driver), values in grouped.items():
        values.sort(key=lambda item: (item["inicio"], item["viaje_id"]))
        current = []
        for trip in values:
            gap = None if not current else (trip["inicio"] - current[-1]["termino"]).total_seconds() / 60
            if current and gap is not None and gap > CHAIN_GAP_MINUTES:
                chains.append(current)
                current = []
            current.append(trip)
        if current:
            chains.append(current)
    records = []
    for group in chains:
        identity = f"{group[0]['asset_id']}|{group[0]['inicio']}|{group[-1]['termino']}"
        chain_id = "CHAIN_" + sha256(identity.encode("utf-8")).hexdigest()[:24].upper()
        for trip in group:
            trip["encadenamiento_id"] = chain_id
        records.append({
            "encadenamiento_id": chain_id,
            "asset_id": group[0]["asset_id"],
            "driver_document_number": group[0].get("driver_document_number"),
            "inicio": group[0]["inicio"],
            "termino": group[-1]["termino"],
            "cantidad_viajes": len(group),
            "distancia_km": sum(item["distancia_km"] for item in group),
            "duracion_minutos": (group[-1]["termino"] - group[0]["inicio"]).total_seconds() / 60,
            "tiempo_detenido_minutos": sum(item.get("tiempo_detenido_minutos", 0) for item in group),
            "estado": "completado",
        })
    return records


def build_analytics():
    initialize_db()
    with connect_db() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM raw_gps_estado WHERE id=1")
        state = cursor.fetchone()
        if not state or not int(state.get("validos") or 0):
            raise RuntimeError("MariaDB no contiene telemetria filtrada")
        rows = _load_rows(cursor)
    if len(rows) < 2:
        raise RuntimeError("No hay suficientes posiciones para reconstruir viajes")

    version = _version(state)
    analysis = analyze_telemetry(rows)
    rows_by_asset = defaultdict(list)
    for row in rows:
        rows_by_asset[row["asset_id"]].append(row)

    trips, stops = [], []
    for trip in analysis["recorridos"]:
        related = _trip_rows(rows_by_asset, trip)
        if len(related) < 2:
            continue
        record = _trip_details(trip, related)
        trip_stops = _detect_stops(trip, related)
        record["cantidad_paradas"] = len(trip_stops)
        record["tiempo_detenido_minutos"] = sum(item["duracion_minutos"] for item in trip_stops)
        trips.append(record)
        stops.extend(trip_stops)
    chains = _assign_chains(trips)

    trips_by_route = defaultdict(list)
    for trip in trips:
        trips_by_route[trip["route_id"]].append(trip)
    routes = []
    matching_errors = 0
    for feature in analysis["rutas"]:
        properties = feature["properties"]
        related = trips_by_route[properties["route_id"]]
        matched = match_linestring(feature["geometry"])
        if matched["map_matching_estado"] == "error":
            matching_errors += 1
        drivers = {item.get("driver_document_number") for item in related
                   if item.get("driver_document_number")}
        routes.append({
            "route_id": properties["route_id"],
            "nombre": properties["label"][:160],
            "origen": related[0]["origen"] if related else None,
            "destino": related[0]["destino"] if related else None,
            "geometria_json": json.dumps(matched["geometry"], separators=(",", ":")),
            "geometry_source": matched["geometry_source"],
            "map_matching_estado": matched["map_matching_estado"],
            "cantidad_viajes": len(related),
            "cantidad_vehiculos": len({item["asset_id"] for item in related}),
            "cantidad_conductores": len(drivers),
            "distancia_promedio_km": sum(item["distancia_km"] for item in related) / len(related) if related else 0,
            "duracion_promedio_minutos": sum(item["duracion_minutos"] for item in related) / len(related) if related else 0,
            "paradas_promedio": sum(item["cantidad_paradas"] for item in related) / len(related) if related else 0,
            "usage_level": properties["usage_level"],
            "color": properties["color"],
        })

    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM paradas_telemetria")
        cursor.execute("DELETE FROM viajes_telemetria")
        cursor.execute("DELETE FROM encadenamientos_viajes")
        cursor.execute("DELETE FROM rutas_inferidas")
        if routes:
            cursor.executemany(
                """INSERT INTO rutas_inferidas
                (route_id,nombre,origen,destino,geometria_json,geometry_source,
                 map_matching_estado,cantidad_viajes,cantidad_vehiculos,cantidad_conductores,
                 distancia_promedio_km,duracion_promedio_minutos,paradas_promedio,
                 usage_level,color,version_datos,actualizado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))""",
                [(item["route_id"],item["nombre"],item["origen"],item["destino"],
                  item["geometria_json"],item["geometry_source"],item["map_matching_estado"],
                  item["cantidad_viajes"],item["cantidad_vehiculos"],item["cantidad_conductores"],
                  item["distancia_promedio_km"],item["duracion_promedio_minutos"],
                  item["paradas_promedio"],item["usage_level"],item["color"],version)
                 for item in routes],
            )
        if chains:
            cursor.executemany(
                """INSERT INTO encadenamientos_viajes
                (encadenamiento_id,asset_id,driver_document_number,inicio,termino,
                 cantidad_viajes,distancia_km,duracion_minutos,tiempo_detenido_minutos,
                 estado,actualizado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))""",
                [(item["encadenamiento_id"],item["asset_id"],item["driver_document_number"],
                  item["inicio"],item["termino"],item["cantidad_viajes"],item["distancia_km"],
                  item["duracion_minutos"],item["tiempo_detenido_minutos"],item["estado"])
                 for item in chains],
            )
        if trips:
            cursor.executemany(
                """INSERT INTO viajes_telemetria
                (viaje_id,encadenamiento_id,route_id,asset_id,asset_name,
                 driver_document_number,driver_name,driver_last_name,inicio,termino,
                 origen,destino,distancia_km,duracion_minutos,velocidad_promedio,
                 velocidad_maxima,puntos_gps,cantidad_paradas,tiempo_detenido_minutos,
                 geometria_json,estado,version_datos,actualizado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        UTC_TIMESTAMP(6))""",
                [(item["viaje_id"],item["encadenamiento_id"],item["route_id"],item["asset_id"],
                  item["asset_name"],item["driver_document_number"],item["driver_name"],
                  item["driver_last_name"],item["inicio"],item["termino"],item["origen"],
                  item["destino"],item["distancia_km"],item["duracion_minutos"],
                  item["velocidad_promedio"],item["velocidad_maxima"],item["puntos_gps"],
                  item["cantidad_paradas"],item["tiempo_detenido_minutos"],
                  item["geometria_json"],item["estado"],version) for item in trips],
            )
        if stops:
            cursor.executemany(
                """INSERT INTO paradas_telemetria
                (parada_id,viaje_id,asset_id,driver_document_number,inicio,termino,
                 duracion_minutos,latitude,longitude,zona,tipo,anomala,actualizado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))""",
                [(item["parada_id"],item["viaje_id"],item["asset_id"],
                  item["driver_document_number"],item["inicio"],item["termino"],
                  item["duracion_minutos"],item["latitude"],item["longitude"],
                  item["zona"],item["tipo"],item["anomala"]) for item in stops],
            )
        metrics = {
            "filas_analizadas": len(rows), "viajes": len(trips), "rutas": len(routes),
            "paradas": len(stops), "encadenamientos": len(chains),
            "errores_map_matching": matching_errors,
            "alcance": "Linea base no supervisada sobre una ventana temporal configurable",
        }
        cursor.execute(
            """INSERT INTO modelo_rutas_estado
            (id,version_datos,version_modelo,algoritmo,parametros_json,metricas_json,
             rango_desde,rango_hasta,registros_entrenamiento,artefacto,estado,ultimo_error,
             entrenado_en,ultima_revision)
            VALUES (1,%s,'baseline-1','Agrupacion geoespacial de trayectorias',%s,%s,%s,%s,%s,
                    NULL,'completado',NULL,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE version_datos=VALUES(version_datos),
            version_modelo=VALUES(version_modelo),algoritmo=VALUES(algoritmo),
            parametros_json=VALUES(parametros_json),metricas_json=VALUES(metricas_json),
            rango_desde=VALUES(rango_desde),rango_hasta=VALUES(rango_hasta),
            registros_entrenamiento=VALUES(registros_entrenamiento),estado='completado',
            ultimo_error=NULL,entrenado_en=VALUES(entrenado_en),
            ultima_revision=VALUES(ultima_revision)""",
            (version, json.dumps({"stop_speed_kmh": STOP_SPEED_KMH,
                                  "stop_minutes": STOP_MINUTES,
                                  "chain_gap_minutes": CHAIN_GAP_MINUTES}),
             json.dumps(metrics, ensure_ascii=False), rows[0]["report_date"],
             rows[-1]["report_date"], len(rows)),
        )
        connection.commit()
    return metrics | {"version_datos": version}


if __name__ == "__main__":
    print(json.dumps(build_analytics(), ensure_ascii=False, indent=2))
