"""raw_gps -> clustering DBSCAN -> modelo_telemetria_estado."""

import json
import os
from collections import defaultdict

import numpy as np
from sklearn.cluster import DBSCAN

from common.db import connect_db
from common.vehiculos import sql_es_camion


EARTH_RADIUS_KM = 6371.0088


def train_hotspots(rows, radius_km=0.8, min_samples=5):
    """Agrupa posiciones cercanas sin requerir rutas etiquetadas."""
    if len(rows) < max(10, min_samples * 2):
        raise ValueError("Se necesitan al menos 10 posiciones para entrenar telemetria")
    coordinates = np.radians([[float(row["latitude"]), float(row["longitude"])] for row in rows])
    labels = DBSCAN(
        eps=float(radius_km) / EARTH_RADIUS_KM,
        min_samples=int(min_samples),
        metric="haversine",
        algorithm="ball_tree",
    ).fit_predict(coordinates)

    grouped = defaultdict(list)
    noise = 0
    for row, label in zip(rows, labels):
        if int(label) == -1:
            noise += 1
        else:
            grouped[int(label)].append(row)

    zones = []
    for label, members in grouped.items():
        speeds = [float(row["speed"]) for row in members if row.get("speed") is not None]
        zones.append({
            "zona_id": label,
            "latitud": round(sum(float(row["latitude"]) for row in members) / len(members), 6),
            "longitud": round(sum(float(row["longitude"]) for row in members) / len(members), 6),
            "reportes": len(members),
            "vehiculos": len({str(row["asset_id"]) for row in members}),
            "velocidad_promedio": round(sum(speeds) / len(speeds), 2) if speeds else None,
        })
    zones.sort(key=lambda zone: (-zone["reportes"], zone["zona_id"]))
    metrics = {
        "reportes_entrenamiento": len(rows),
        "vehiculos": len({str(row["asset_id"]) for row in rows}),
        "zonas_detectadas": len(zones),
        "puntos_ruido": noise,
        "cobertura_pct": round((len(rows) - noise) * 100 / len(rows), 2),
        "radio_km": float(radius_km),
        "min_muestras": int(min_samples),
        "alcance": "Clustering geoespacial no supervisado; identifica zonas frecuentes, no rutas etiquetadas.",
    }
    return metrics, zones[:250]


def last_trained_version():
    with connect_db() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT dataset_version FROM modelo_telemetria_estado WHERE id=1")
        row = cursor.fetchone()
        return row["dataset_version"] if row else None


def cycle(previous_version, radius_km, min_samples, max_rows):
    """Reentrena solo cuando el cursor del ETL de telemetria avanzo."""
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT ultimo_mongo_id, validos FROM raw_gps_estado WHERE id=1")
        state = cursor.fetchone()
        current_version = state["ultimo_mongo_id"] if state else None
        if not current_version:
            connection.commit()
            raise ValueError("Esperando la primera carga del ETL de telemetria")
        if current_version == previous_version:
            cursor.execute("UPDATE modelo_telemetria_estado SET ultima_revision=UTC_TIMESTAMP(6) WHERE id=1")
            connection.commit()
            return previous_version
        cursor.execute(
            "SELECT gps.asset_id, gps.latitude, gps.longitude, gps.speed FROM raw_gps AS gps "
            f"WHERE {sql_es_camion('gps.asset_id')} "
            "ORDER BY gps.report_date DESC LIMIT %s",
            (max_rows,),
        )
        rows = cursor.fetchall()
        connection.commit()

    metrics, zones = train_hotspots(rows, radius_km=radius_km, min_samples=min_samples)
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO modelo_telemetria_estado
            (id,dataset_version,algoritmo,metricas_json,zonas_json,entrenado_en,ultima_revision)
            VALUES (1,%s,'DBSCAN',%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE dataset_version=VALUES(dataset_version),
            algoritmo=VALUES(algoritmo),metricas_json=VALUES(metricas_json),
            zonas_json=VALUES(zonas_json),entrenado_en=VALUES(entrenado_en),
            ultima_revision=VALUES(ultima_revision)""",
            (current_version, json.dumps(metrics, ensure_ascii=False), json.dumps(zones, ensure_ascii=False)),
        )
        connection.commit()
    print(
        f"Machine: {metrics['zonas_detectadas']} zonas detectadas de {metrics['reportes_entrenamiento']} "
        f"reportes ({metrics['cobertura_pct']}% cobertura)",
        flush=True,
    )
    return current_version


class ModelConfig:
    def __init__(self, radius_km, min_samples, max_rows):
        self.radius_km = radius_km
        self.min_samples = min_samples
        self.max_rows = max_rows

    @classmethod
    def from_env(cls):
        return cls(
            radius_km=float(os.environ.get("TELEMETRY_MODEL_RADIUS_KM", "0.8")),
            min_samples=int(os.environ.get("TELEMETRY_MODEL_MIN_SAMPLES", "5")),
            max_rows=int(os.environ.get("TELEMETRY_MODEL_MAX_ROWS", "20000")),
        )
