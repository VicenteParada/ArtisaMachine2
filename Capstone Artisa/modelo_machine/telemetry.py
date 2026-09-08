"""Modelo no supervisado para descubrir zonas frecuentes en telemetría GPS."""

from collections import defaultdict

import numpy as np
from sklearn.cluster import DBSCAN


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
