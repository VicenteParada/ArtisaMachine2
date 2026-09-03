"""Rangos compartidos por el ETL y las solicitudes de predicción."""

import math


FEATURES = ("distancia_km", "carga_kg", "paradas")
LIMITS = {"distancia_km": (1, 2000), "carga_kg": (1, 40000), "paradas": (0, 30)}


def number(value, field, lower, upper, integer=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{field}: debe ser un numero")
    try:
        result = float(str(value).strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"{field}: debe ser un numero") from None
    if not math.isfinite(result) or not lower <= result <= upper:
        raise ValueError(f"{field}: debe estar entre {lower} y {upper}")
    if integer and not result.is_integer():
        raise ValueError(f"{field}: debe ser un entero")
    return int(result) if integer else result


def features(payload):
    return {
        key: number(payload.get(key), key, *LIMITS[key], integer=(key == "paradas"))
        for key in FEATURES
    }

