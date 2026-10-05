"""Preprocesamiento de raw_gps: limpieza, imputacion, encoding y escalado.

No se conecta todavia a telemetry.py/worker.py. El DBSCAN de zonas frecuentes
sigue usando latitude/longitude sin escalar (la metrica haversine necesita
coordenadas reales en radianes). Este modulo prepara datos para futuros
modelos (deteccion de anomalias de velocidad, prediccion de ETA).
"""

import statistics
from datetime import datetime


NUMERIC_FEATURES = ("latitude", "longitude", "speed", "cartography_limit_speed",
                     "exceso_velocidad", "hora_dia")
SPEED_MIN_KMH = 0.0
SPEED_MAX_KMH = 200.0


def clean_rows(rows, speed_min=SPEED_MIN_KMH, speed_max=SPEED_MAX_KMH):
    """Descarta reportes con velocidad fuera de un rango fisico razonable."""
    cleaned = []
    for row in rows:
        speed = row.get("speed")
        if speed is not None and not (speed_min <= float(speed) <= speed_max):
            continue
        cleaned.append(row)
    return cleaned


def impute_missing(rows):
    """Rellena speed y cartography_limit_speed nulos con la mediana del lote."""
    speeds = [float(row["speed"]) for row in rows if row.get("speed") is not None]
    limits = [float(row["cartography_limit_speed"]) for row in rows
              if row.get("cartography_limit_speed") is not None]
    speed_median = statistics.median(speeds) if speeds else 0.0
    limit_median = statistics.median(limits) if limits else 0.0

    imputed = []
    for row in rows:
        row = dict(row)
        if row.get("speed") is None:
            row["speed"] = speed_median
        if row.get("cartography_limit_speed") is None:
            row["cartography_limit_speed"] = limit_median
        imputed.append(row)
    return imputed


def derive_features(rows):
    """Agrega hora_dia (0-23) y exceso_velocidad a cada fila."""
    derived = []
    for row in rows:
        row = dict(row)
        report_date = row.get("report_date")
        row["hora_dia"] = report_date.hour if isinstance(report_date, datetime) else 0
        row["exceso_velocidad"] = float(row["speed"]) - float(row["cartography_limit_speed"])
        derived.append(row)
    return derived


def encode_categorical(rows, column="event_name"):
    """One-hot encoding dinamico: una columna nueva por cada categoria vista en el lote."""
    categories = sorted({row.get(column) or "desconocido" for row in rows})
    category_columns = [f"{column}_{category}" for category in categories]

    encoded = []
    for row in rows:
        row = dict(row)
        value = row.get(column) or "desconocido"
        for category, category_column in zip(categories, category_columns):
            row[category_column] = 1.0 if value == category else 0.0
        encoded.append(row)
    return encoded, category_columns


def scale_numeric(rows, columns):
    """Estandarizacion z-score: (valor - media) / desviacion estandar."""
    scaled = [dict(row) for row in rows]
    for column in columns:
        values = [float(row[column]) for row in scaled]
        mean = statistics.fmean(values)
        stdev = statistics.pstdev(values) or 1.0
        for row in scaled:
            row[column] = (float(row[column]) - mean) / stdev
    return scaled


def build_dataset(rows, categorical_column="event_name"):
    """Orquesta limpieza -> imputacion -> features derivadas -> encoding -> escalado.

    Devuelve (filas_transformadas, columnas_numericas_finales).
    """
    rows = clean_rows(rows)
    rows = impute_missing(rows)
    rows = derive_features(rows)
    rows, category_columns = encode_categorical(rows, categorical_column)
    rows = scale_numeric(rows, list(NUMERIC_FEATURES))
    return rows, list(NUMERIC_FEATURES) + category_columns
