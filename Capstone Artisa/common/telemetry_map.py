"""Agrega telemetría real para el mapa de calor sin depender de servicios externos."""

from collections import defaultdict
from math import floor


GRID_DEGREES = 0.05
STOP_EVENT = "inicio de detenido"


def _cell(value, size=GRID_DEGREES):
    return round(floor(float(value) / size) * size + size / 2, 5)


def _point(row):
    return _cell(row["latitude"]), _cell(row["longitude"])


def _segment_key(first, second):
    return (first, second) if first <= second else (second, first)


def _label(point):
    return f"{point[0]:.3f}, {point[1]:.3f}"


def analyze_telemetry(rows):
    """Calcula densidad, cruces de tramos y paradas explícitas de una muestra GPS."""
    if not rows:
        return {
            "resumen": {"reportes": 0, "vehiculos": 0, "paradas": 0,
                        "zonas": 0, "tramos": 0},
            "limites": None,
            "celdas": [],
            "tramos": [],
            "ranking": {"mas_usado": None, "menos_usado": None},
            "paradas_por_vehiculo": [],
            "metodo": _method(),
        }

    ordered = sorted(rows, key=lambda row: (
        str(row.get("asset_id") or ""), row.get("report_date"), str(row.get("report_id") or "")
    ))
    unique_observations = []
    seen_observations = set()
    stop_keys = set()
    stops_by_vehicle = defaultdict(int)
    names = {}

    for row in ordered:
        asset = str(row.get("asset_id") or "")
        names[asset] = row.get("asset_name") or asset
        observation_key = (
            asset, row.get("report_date"), round(float(row["latitude"]), 5),
            round(float(row["longitude"]), 5)
        )
        if observation_key not in seen_observations:
            seen_observations.add(observation_key)
            unique_observations.append(row)
        if str(row.get("event_name") or "").strip().casefold() == STOP_EVENT:
            stop_key = observation_key + (STOP_EVENT,)
            if stop_key not in stop_keys:
                stop_keys.add(stop_key)
                stops_by_vehicle[asset] += 1

    cells = defaultdict(lambda: {"reportes": 0, "vehiculos": set(), "paradas": 0})
    for row in unique_observations:
        point = _point(row)
        cells[point]["reportes"] += 1
        cells[point]["vehiculos"].add(str(row["asset_id"]))
    for row in ordered:
        if str(row.get("event_name") or "").strip().casefold() == STOP_EVENT:
            stop_key = (
                str(row.get("asset_id") or ""), row.get("report_date"),
                round(float(row["latitude"]), 5), round(float(row["longitude"]), 5), STOP_EVENT
            )
            if stop_key in stop_keys:
                cells[_point(row)]["paradas"] += 1
                stop_keys.remove(stop_key)

    tracks = defaultdict(list)
    for row in unique_observations:
        tracks[str(row["asset_id"])].append(row)
    segments = defaultdict(lambda: {"pasos": 0, "vehiculos": set()})
    for asset, track in tracks.items():
        previous = None
        for row in track:
            current = _point(row)
            if previous is not None and current != previous:
                segment = segments[_segment_key(previous, current)]
                segment["pasos"] += 1
                segment["vehiculos"].add(asset)
            previous = current

    max_reports = max(value["reportes"] for value in cells.values())
    cell_rows = [{
        "latitud": point[0], "longitud": point[1],
        "reportes": value["reportes"], "vehiculos": len(value["vehiculos"]),
        "paradas": value["paradas"],
        "intensidad": round(value["reportes"] / max_reports, 4),
    } for point, value in cells.items()]
    cell_rows.sort(key=lambda value: value["reportes"], reverse=True)

    max_passes = max((value["pasos"] for value in segments.values()), default=1)
    segment_rows = []
    for (origin, destination), value in segments.items():
        segment_rows.append({
            "origen": {"latitud": origin[0], "longitud": origin[1]},
            "destino": {"latitud": destination[0], "longitud": destination[1]},
            "etiqueta": f"{_label(origin)} → {_label(destination)}",
            "pasos": value["pasos"], "vehiculos": len(value["vehiculos"]),
            "intensidad": round(value["pasos"] / max_passes, 4),
        })
    segment_rows.sort(key=lambda value: (-value["pasos"], -value["vehiculos"], value["etiqueta"]))
    least = min(segment_rows, key=lambda value: (value["pasos"], value["vehiculos"], value["etiqueta"]), default=None)

    latitudes = [float(row["latitude"]) for row in unique_observations]
    longitudes = [float(row["longitude"]) for row in unique_observations]
    stop_rows = [{"asset_id": asset, "asset_name": names.get(asset, asset), "paradas": count}
                 for asset, count in stops_by_vehicle.items()]
    stop_rows.sort(key=lambda value: (-value["paradas"], value["asset_name"]))
    return {
        "resumen": {
            "reportes": len(unique_observations), "vehiculos": len(tracks),
            "paradas": sum(stops_by_vehicle.values()), "zonas": len(cell_rows),
            "tramos": len(segment_rows),
        },
        "limites": {
            "latitud_min": min(latitudes), "latitud_max": max(latitudes),
            "longitud_min": min(longitudes), "longitud_max": max(longitudes),
        },
        "celdas": cell_rows[:250],
        "tramos": segment_rows[:180],
        "ranking": {"mas_usado": segment_rows[0] if segment_rows else None,
                    "menos_usado": least},
        "paradas_por_vehiculo": stop_rows[:10],
        "metodo": _method(),
    }


def _method():
    return {
        "resolucion_grados": GRID_DEGREES,
        "descripcion_tramo": "Cruce entre celdas consecutivas de 0,05° (aprox. 5 km).",
        "descripcion_parada": "Evento de origen 'Inicio de detenido', sin duplicar posición y fecha.",
    }
