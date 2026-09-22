"""Adaptador opcional para map matching en un OSRM controlado por el proyecto."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


def _sample_coordinates(coordinates, maximum=100):
    if len(coordinates) <= maximum:
        return coordinates
    indexes = {round(index * (len(coordinates) - 1) / (maximum - 1)) for index in range(maximum)}
    return [coordinates[index] for index in sorted(indexes)]


def match_linestring(geometry, timeout_seconds=None):
    """Devuelve la geometría ajustada; sin URL conserva la traza GPS y marca pendiente."""
    coordinates = list((geometry or {}).get("coordinates") or [])
    fallback = {
        "geometry": geometry,
        "geometry_source": "GPS_TRACE_FILTERED",
        "map_matching": False,
        "map_matching_estado": "pendiente",
        "error": None,
    }
    base_url = os.environ.get("MAP_MATCHING_URL", "").strip().rstrip("/")
    if not base_url or len(coordinates) < 2:
        return fallback
    sampled = _sample_coordinates(coordinates)
    coordinate_path = ";".join(f"{float(lon):.6f},{float(lat):.6f}" for lon, lat in sampled)
    url = (
        f"{base_url}/match/v1/driving/{quote(coordinate_path, safe=',;')}"
        "?geometries=geojson&overview=full&tidy=true&gaps=split"
    )
    timeout = timeout_seconds or int(os.environ.get("MAP_MATCHING_TIMEOUT_SECONDS", "20"))
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
        matchings = payload.get("matchings") or []
        if payload.get("code") != "Ok" or not matchings:
            return fallback | {"map_matching_estado": "sin_coincidencia"}
        best = max(matchings, key=lambda item: float(item.get("confidence") or 0))
        matched = best.get("geometry")
        if not matched or matched.get("type") != "LineString":
            return fallback | {"map_matching_estado": "sin_coincidencia"}
        return {
            "geometry": matched,
            "geometry_source": "OSRM_MATCHED",
            "map_matching": True,
            "map_matching_estado": "completado",
            "error": None,
        }
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        return fallback | {
            "map_matching_estado": "error",
            "error": f"{type(error).__name__}: {error}"[:255],
        }
