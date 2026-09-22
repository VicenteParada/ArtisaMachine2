"""Analiza telemetria GPS y construye rutas completas para el mapa."""

from collections import defaultdict
from datetime import datetime
from hashlib import sha1
from math import asin, cos, floor, radians, sin, sqrt


GRID_DEGREES = 0.05
STOP_EVENT = "inicio de detenido"
STOP_SPEED_KMH = 3.0
STOP_SPLIT_SECONDS = 10 * 60
JOURNEY_GAP_SECONDS = 20 * 60
MAX_REASONABLE_SPEED_KMH = 160.0
MIN_ROUTE_DISTANCE_KM = 0.25
ROUTE_SIMPLIFY_DEGREES = 0.00008
MAX_GEOMETRY_POINTS = 500
ROUTE_COMPARISON_SAMPLES = 21
ROUTE_ENDPOINT_MIN_KM = 2.0
ROUTE_ENDPOINT_MAX_KM = 8.0
ROUTE_ENDPOINT_LENGTH_RATIO = 0.15
ROUTE_CORRIDOR_TOLERANCE_KM = 2.0
ROUTE_SHARED_GEOMETRY_RATIO = 0.60
ROUTE_DIRECTION_COSINE_MIN = 0.50
ROUTE_LEVEL_RATIOS = {"MEDIUM": 1 / 3, "HIGH": 2 / 3}
ROUTE_STYLES = {
    "HIGH": {"color": "#14532d", "label": "Uso alto"},
    "MEDIUM": {"color": "#f97316", "label": "Uso medio"},
    "LOW": {"color": "#dc2626", "label": "Uso bajo"},
}


def _cell(value, size=GRID_DEGREES):
    return round(floor(float(value) / size) * size + size / 2, 5)


def _point(row):
    return _cell(row["latitude"]), _cell(row["longitude"])


def _label(point):
    return f"{point[0]:.3f}, {point[1]:.3f}"


def _coordinates(row):
    return float(row["latitude"]), float(row["longitude"])


def _speed(row):
    value = row.get("gps_speed")
    if value is None:
        value = row.get("speed")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _seconds_between(first, second):
    if not isinstance(first, datetime) or not isinstance(second, datetime):
        return 0.0
    return max(0.0, (second - first).total_seconds())


def _distance_km(first, second):
    lat1, lon1 = first
    lat2, lon2 = second
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * asin(min(1.0, sqrt(value)))


def _valid_journey(rows):
    if len(rows) < 2:
        return False
    travelled = sum(
        _distance_km(_coordinates(previous), _coordinates(current))
        for previous, current in zip(rows, rows[1:])
    )
    return travelled >= MIN_ROUTE_DISTANCE_KM


def _detect_journeys(track):
    journeys = []
    current = []
    stopped_since = None

    for row in track:
        report_date = row.get("report_date")
        split = False
        if current:
            previous = current[-1]
            elapsed = _seconds_between(previous.get("report_date"), report_date)
            distance = _distance_km(_coordinates(previous), _coordinates(row))
            allowed_distance = 1.0 + MAX_REASONABLE_SPEED_KMH * elapsed / 3600
            split = elapsed > JOURNEY_GAP_SECONDS or distance > allowed_distance

            if (
                not split
                and stopped_since is not None
                and _speed(row) > STOP_SPEED_KMH
                and _seconds_between(stopped_since, report_date) >= STOP_SPLIT_SECONDS
            ):
                split = True

        if split:
            if _valid_journey(current):
                journeys.append(current)
            current = []
            stopped_since = None

        current.append(row)
        if _speed(row) <= STOP_SPEED_KMH:
            if stopped_since is None:
                stopped_since = report_date
        else:
            stopped_since = None

    if _valid_journey(current):
        journeys.append(current)
    return journeys


def _point_line_distance(point, start, end):
    if start == end:
        return sqrt((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2)
    delta_lat = end[0] - start[0]
    delta_lon = end[1] - start[1]
    ratio = (
        (point[0] - start[0]) * delta_lat + (point[1] - start[1]) * delta_lon
    ) / (delta_lat ** 2 + delta_lon ** 2)
    ratio = max(0.0, min(1.0, ratio))
    projected = start[0] + ratio * delta_lat, start[1] + ratio * delta_lon
    return sqrt((point[0] - projected[0]) ** 2 + (point[1] - projected[1]) ** 2)


def _simplify(points, tolerance=ROUTE_SIMPLIFY_DEGREES):
    if len(points) <= 2:
        return points
    keep = {0, len(points) - 1}
    pending = [(0, len(points) - 1)]
    while pending:
        start_index, end_index = pending.pop()
        start = points[start_index]
        end = points[end_index]
        furthest_index = None
        furthest_distance = 0.0
        for index in range(start_index + 1, end_index):
            distance = _point_line_distance(points[index], start, end)
            if distance > furthest_distance:
                furthest_distance = distance
                furthest_index = index
        if furthest_index is not None and furthest_distance > tolerance:
            keep.add(furthest_index)
            pending.append((start_index, furthest_index))
            pending.append((furthest_index, end_index))
    return [points[index] for index in sorted(keep)]


def _limit_points(points, limit=MAX_GEOMETRY_POINTS):
    if len(points) <= limit:
        return points
    indexes = {
        round(position * (len(points) - 1) / (limit - 1))
        for position in range(limit)
    }
    return [points[index] for index in sorted(indexes)]


def _route_geometry(journey):
    points = []
    for row in journey:
        point = _coordinates(row)
        if not points or _distance_km(points[-1], point) >= 0.005:
            points.append(point)
    return _limit_points(_simplify(points))


def _path_length_km(points):
    return sum(_distance_km(first, second) for first, second in zip(points, points[1:]))


def _resample_geometry(points, samples=ROUTE_COMPARISON_SAMPLES):
    """Muestrea una traza por distancia, independientemente de su frecuencia GPS."""
    if len(points) < 2:
        return list(points)
    segment_lengths = [
        _distance_km(first, second) for first, second in zip(points, points[1:])
    ]
    total = sum(segment_lengths)
    if total <= 0:
        return [points[0], points[-1]]

    targets = [total * index / (samples - 1) for index in range(samples)]
    result = []
    segment_index = 0
    segment_start_distance = 0.0
    for target in targets:
        while (
            segment_index < len(segment_lengths) - 1
            and segment_start_distance + segment_lengths[segment_index] < target
        ):
            segment_start_distance += segment_lengths[segment_index]
            segment_index += 1
        segment_length = segment_lengths[segment_index]
        ratio = 0.0 if segment_length <= 0 else (
            (target - segment_start_distance) / segment_length
        )
        segment_start = points[segment_index]
        segment_end = points[segment_index + 1]
        result.append((
            segment_start[0] + (segment_end[0] - segment_start[0]) * ratio,
            segment_start[1] + (segment_end[1] - segment_start[1]) * ratio,
        ))
    return result


def _point_segment_distance_km(point, start, end):
    """Calcula distancia local punto-segmento sin exigir coordenadas exactas."""
    latitude_scale = 110.574
    longitude_scale = 111.320 * cos(radians(point[0]))
    start_xy = (
        (start[1] - point[1]) * longitude_scale,
        (start[0] - point[0]) * latitude_scale,
    )
    end_xy = (
        (end[1] - point[1]) * longitude_scale,
        (end[0] - point[0]) * latitude_scale,
    )
    delta_x = end_xy[0] - start_xy[0]
    delta_y = end_xy[1] - start_xy[1]
    denominator = delta_x ** 2 + delta_y ** 2
    if denominator <= 0:
        return sqrt(start_xy[0] ** 2 + start_xy[1] ** 2)
    ratio = max(0.0, min(1.0, -(
        start_xy[0] * delta_x + start_xy[1] * delta_y
    ) / denominator))
    closest_x = start_xy[0] + ratio * delta_x
    closest_y = start_xy[1] + ratio * delta_y
    return sqrt(closest_x ** 2 + closest_y ** 2)


def _distance_to_geometry(point, geometry):
    return min(
        _point_segment_distance_km(point, start, end)
        for start, end in zip(geometry, geometry[1:])
    )


def _direction_vector(points):
    sampled = _resample_geometry(points, 5)
    start = sampled[0]
    end = sampled[-1]
    if _distance_km(start, end) < max(0.25, _path_length_km(points) * 0.10):
        end = sampled[1]
    latitude_scale = 110.574
    longitude_scale = 111.320 * cos(radians((start[0] + end[0]) / 2))
    return (
        (end[1] - start[1]) * longitude_scale,
        (end[0] - start[0]) * latitude_scale,
    )


def _direction_cosine(first, second):
    first_length = sqrt(first[0] ** 2 + first[1] ** 2)
    second_length = sqrt(second[0] ** 2 + second[1] ** 2)
    if first_length <= 0 or second_length <= 0:
        return 1.0
    return (
        first[0] * second[0] + first[1] * second[1]
    ) / (first_length * second_length)


def _shared_geometry_ratio(first, second):
    first_samples = _resample_geometry(first)
    second_samples = _resample_geometry(second)
    first_shared = sum(
        _distance_to_geometry(point, second) <= ROUTE_CORRIDOR_TOLERANCE_KM
        for point in first_samples
    ) / len(first_samples)
    second_shared = sum(
        _distance_to_geometry(point, first) <= ROUTE_CORRIDOR_TOLERANCE_KM
        for point in second_samples
    ) / len(second_samples)
    return min(first_shared, second_shared), first_samples, second_samples


def _route_similarity(first, second):
    """Devuelve un puntaje si dos trips representan la misma ruta y sentido."""
    first_length = _path_length_km(first)
    second_length = _path_length_km(second)
    endpoint_tolerance = min(
        ROUTE_ENDPOINT_MAX_KM,
        max(
            ROUTE_ENDPOINT_MIN_KM,
            min(first_length, second_length) * ROUTE_ENDPOINT_LENGTH_RATIO,
        ),
    )
    start_distance = _distance_km(first[0], second[0])
    end_distance = _distance_km(first[-1], second[-1])
    if start_distance > endpoint_tolerance or end_distance > endpoint_tolerance:
        return None

    direction_cosine = _direction_cosine(
        _direction_vector(first), _direction_vector(second)
    )
    if direction_cosine < ROUTE_DIRECTION_COSINE_MIN:
        return None

    shared_ratio, first_samples, second_samples = _shared_geometry_ratio(first, second)
    if shared_ratio < ROUTE_SHARED_GEOMETRY_RATIO:
        return None
    aligned_distance = sum(
        _distance_km(first_point, second_point)
        for first_point, second_point in zip(first_samples, second_samples)
    ) / len(first_samples)
    if aligned_distance > ROUTE_CORRIDOR_TOLERANCE_KM * 2:
        return None
    return (
        (start_distance + end_distance) / max(endpoint_tolerance, 0.001)
        + (1 - shared_ratio) * 2
        + aligned_distance / ROUTE_CORRIDOR_TOLERANCE_KM
    )


def _route_fingerprint(points):
    """Genera un ID después de agrupar; el hash no decide la equivalencia."""
    cells = []
    for latitude, longitude in _resample_geometry(points, 9):
        cell = (round(latitude, 2), round(longitude, 2))
        if not cells or cells[-1] != cell:
            cells.append(cell)
    return tuple(cells)


def _usage_level(usage_count, distinct_usage_counts):
    if len(distinct_usage_counts) == 1:
        return "HIGH" if usage_count > 1 else "LOW"
    usage_position = distinct_usage_counts.index(usage_count)
    usage_ratio = usage_position / (len(distinct_usage_counts) - 1)
    if usage_ratio >= ROUTE_LEVEL_RATIOS["HIGH"]:
        return "HIGH"
    if usage_ratio >= ROUTE_LEVEL_RATIOS["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


def _representative_journey(journeys):
    """Elige una traza central y detallada sin promediar coordenadas inventadas."""
    ordered_lengths = sorted(journey["distance_km"] for journey in journeys)
    median_length = ordered_lengths[len(ordered_lengths) // 2]
    return min(
        journeys,
        key=lambda journey: (
            abs(journey["distance_km"] - median_length),
            -journey["reading_count"],
            journey["trip_identity"],
        ),
    )


def _route_features(tracks):
    """Registra trips y los asocia a rutas equivalentes por similitud espacial."""
    journey_records = []
    for asset, track in tracks.items():
        for journey in _detect_journeys(track):
            geometry = _route_geometry(journey)
            if len(geometry) < 2:
                continue
            identity = "|".join((
                asset,
                str(journey[0].get("report_date")),
                str(journey[-1].get("report_date")),
                str(journey[0].get("report_id") or ""),
                str(journey[-1].get("report_id") or ""),
            ))
            journey_records.append({
                "trip_identity": identity,
                "trip_id": "TRIP_" + sha1(identity.encode("utf-8")).hexdigest()[:12].upper(),
                "vehicle_id": asset,
                "started_at": journey[0].get("report_date"),
                "ended_at": journey[-1].get("report_date"),
                "reading_count": len(journey),
                "observed_points": len(journey),
                "distance_km": _path_length_km(geometry),
                "geometry": geometry,
            })

    journey_records.sort(key=lambda journey: (
        journey["started_at"], journey["vehicle_id"], journey["trip_id"],
    ))

    route_registry = []
    for journey in journey_records:
        candidates = []
        for route in route_registry:
            similarity = _route_similarity(journey["geometry"], route["prototype"])
            if similarity is not None:
                candidates.append((similarity, route["registry_order"], route))
        if candidates:
            route = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]
        else:
            route = {
                "registry_order": len(route_registry),
                "prototype": journey["geometry"],
                "journeys": [],
                "vehicles": set(),
                "observed_points": 0,
            }
            route_registry.append(route)

        route["journeys"].append(journey)
        route["vehicles"].add(journey["vehicle_id"])
        route["observed_points"] += journey["observed_points"]

        representative = _representative_journey(route["journeys"])
        route["prototype"] = representative["geometry"]

    used_route_ids = set()
    groups = []
    for route in route_registry:
        representative = _representative_journey(route["journeys"])
        fingerprint = _route_fingerprint(representative["geometry"])
        digest_source = repr(fingerprint)
        route_id = "RUTA_" + sha1(digest_source.encode("utf-8")).hexdigest()[:8].upper()
        if route_id in used_route_ids:
            digest_source += "|" + route["journeys"][0]["trip_id"]
            route_id = "RUTA_" + sha1(digest_source.encode("utf-8")).hexdigest()[:10].upper()
        used_route_ids.add(route_id)
        groups.append({
            "route_id": route_id,
            "usage_count": len(route["journeys"]),
            "vehicles": route["vehicles"],
            "geometry": representative["geometry"],
            "observed_points": route["observed_points"],
            "journeys": route["journeys"],
        })

    groups.sort(key=lambda value: (
        -value["usage_count"], -len(value["vehicles"]),
        -value["observed_points"], value["route_id"],
    ))
    distinct_usage_counts = sorted({group["usage_count"] for group in groups})

    features = []
    trips = []
    for group in groups:
        level = _usage_level(group["usage_count"], distinct_usage_counts)
        style = ROUTE_STYLES[level]
        geometry = group["geometry"]
        start = geometry[0]
        end = geometry[-1]
        features.append({
            "type": "Feature",
            "properties": {
                "route_id": group["route_id"],
                "label": f"{group['route_id']} | {_label(start)} → {_label(end)}",
                "usage_count": group["usage_count"],
                "usage_level": level,
                "usage_label": style["label"],
                "color": style["color"],
                "vehicle_count": len(group["vehicles"]),
                "unique_vehicle_count": len(group["vehicles"]),
                "observed_points": group["observed_points"],
                "geometry_points": len(geometry),
                "geometry_source": "GPS_TRACE_FILTERED",
                "map_matching": False,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[lon, lat] for lat, lon in geometry],
            },
        })
        for journey in group["journeys"]:
            trips.append({
                "trip_id": journey["trip_id"],
                "route_id": group["route_id"],
                "vehicle_id": journey["vehicle_id"],
                "started_at": journey["started_at"],
                "ended_at": journey["ended_at"],
                "reading_count": journey["reading_count"],
                "distance_km": round(journey["distance_km"], 3),
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [longitude, latitude]
                        for latitude, longitude in journey["geometry"]
                    ],
                },
                "status": "OBSERVED",
            })

    trips.sort(key=lambda trip: (
        trip["started_at"], trip["vehicle_id"], trip["trip_id"],
    ))
    return features, trips


def _empty_result():
    return {
        "resumen": {
            "reportes": 0,
            "filas_ventana": 0,
            "vehiculos": 0,
            "paradas": 0,
            "zonas": 0,
            "rutas": 0,
            "recorridos": 0,
            "categorias_ruta": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
        },
        "limites": None,
        "celdas": [],
        "rutas": [],
        "recorridos": [],
        "paradas_geograficas": [],
        "vehiculos_actuales": [],
        "ultima_posicion": None,
        "ranking": {"mas_usado": None, "menos_usado": None},
        "paradas_por_vehiculo": [],
        "metodo": _method(),
    }


def analyze_telemetry(rows, current_rows=None):
    """Procesa GPS sin borrar el histórico; current_rows puede cubrir toda la flota."""
    if not rows:
        return _empty_result()

    ordered = sorted(rows, key=lambda row: (
        str(row.get("asset_id") or ""),
        row.get("report_date"),
        str(row.get("report_id") or ""),
    ))
    position_rows = current_rows if current_rows is not None else rows
    current_ordered = sorted(position_rows, key=lambda row: (
        str(row.get("asset_id") or ""),
        row.get("report_date"),
        str(row.get("report_id") or ""),
    ))
    unique_observations = []
    seen_observations = set()
    stop_keys = set()
    stops_by_vehicle = defaultdict(int)
    names = {}

    for row in [*ordered, *current_ordered]:
        asset = str(row.get("asset_id") or "")
        names[asset] = row.get("asset_name") or asset

    for row in ordered:
        asset = str(row.get("asset_id") or "")
        observation_key = (
            asset,
            row.get("report_date"),
            round(float(row["latitude"]), 5),
            round(float(row["longitude"]), 5),
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
                str(row.get("asset_id") or ""),
                row.get("report_date"),
                round(float(row["latitude"]), 5),
                round(float(row["longitude"]), 5),
                STOP_EVENT,
            )
            if stop_key in stop_keys:
                cells[_point(row)]["paradas"] += 1
                stop_keys.remove(stop_key)

    tracks = defaultdict(list)
    for row in unique_observations:
        tracks[str(row["asset_id"])].append(row)

    routes, trips = _route_features(tracks)
    latest_trip_by_vehicle = {}
    for trip in trips:
        previous = latest_trip_by_vehicle.get(trip["vehicle_id"])
        if previous is None or trip["ended_at"] > previous["ended_at"]:
            latest_trip_by_vehicle[trip["vehicle_id"]] = trip

    current_by_asset = {}
    for row in current_ordered:
        current_by_asset[str(row.get("asset_id") or "")] = row
    current_vehicles = []
    for asset, latest in current_by_asset.items():
        speed = latest.get("gps_speed")
        if speed is None:
            speed = latest.get("speed")
        recent_trip = latest_trip_by_vehicle.get(asset)
        current_vehicles.append({
            "asset_id": asset,
            "asset_name": names.get(asset, asset),
            "route_id": recent_trip["route_id"] if recent_trip else None,
            "report_date": latest.get("report_date"),
            "latitud": float(latest["latitude"]),
            "longitud": float(latest["longitude"]),
            "velocidad": float(speed) if speed is not None else None,
            "rumbo": float(latest["heading"]) if latest.get("heading") is not None else None,
            "encendido": latest.get("ignition"),
            "evento": latest.get("event_name"),
            "driver_name": latest.get("driver_name"),
            "driver_last_name": latest.get("driver_last_name"),
            "driver_document_number": latest.get("driver_document_number"),
        })
    current_vehicles.sort(key=lambda value: (str(value["asset_name"]), value["asset_id"]))
    latest_position = max(
        (vehicle["report_date"] for vehicle in current_vehicles if vehicle["report_date"] is not None),
        default=None,
    )

    max_reports = max(value["reportes"] for value in cells.values())
    cell_rows = [{
        "latitud": point[0],
        "longitud": point[1],
        "reportes": value["reportes"],
        "vehiculos": len(value["vehiculos"]),
        "paradas": value["paradas"],
        "intensidad": round(value["reportes"] / max_reports, 4),
    } for point, value in cells.items()]
    cell_rows.sort(key=lambda value: value["reportes"], reverse=True)

    stop_locations = []
    for cell in sorted(
        (cell for cell in cell_rows if cell["paradas"] > 0),
        key=lambda value: (-value["paradas"], -value["vehiculos"], -value["reportes"]),
    ):
        identity = f"{cell['latitud']:.5f}|{cell['longitud']:.5f}"
        stop_locations.append({
            "stop_id": "STOP_" + sha1(identity.encode("utf-8")).hexdigest()[:10].upper(),
            "label": f"Zona de detención {cell['latitud']:.3f}, {cell['longitud']:.3f}",
            "route_id": None,
            "latitude": cell["latitud"],
            "longitude": cell["longitud"],
            "reading_count": cell["reportes"],
            "vehicle_count": cell["vehiculos"],
            "stop_event_count": cell["paradas"],
            "duration_minutes": None,
        })

    category_counts = {
        level: sum(route["properties"]["usage_level"] == level for route in routes)
        for level in ROUTE_STYLES
    }
    latitudes = [float(row["latitude"]) for row in unique_observations]
    longitudes = [float(row["longitude"]) for row in unique_observations]
    stop_rows = [{
        "asset_id": asset,
        "asset_name": names.get(asset, asset),
        "paradas": count,
    } for asset, count in stops_by_vehicle.items()]
    stop_rows.sort(key=lambda value: (-value["paradas"], value["asset_name"]))

    least = min(
        routes,
        key=lambda route: (
            route["properties"]["usage_count"],
            route["properties"]["vehicle_count"],
            route["properties"]["route_id"],
        ),
        default=None,
    )
    return {
        "resumen": {
            "reportes": len(unique_observations),
            "filas_ventana": len(rows),
            "vehiculos": len(current_vehicles),
            "paradas": sum(stops_by_vehicle.values()),
            "zonas": len(cell_rows),
            "rutas": len(routes),
            "recorridos": len(trips),
            "categorias_ruta": category_counts,
        },
        "limites": {
            "latitud_min": min(latitudes),
            "latitud_max": max(latitudes),
            "longitud_min": min(longitudes),
            "longitud_max": max(longitudes),
        },
        "celdas": cell_rows[:250],
        "rutas": routes,
        "recorridos": trips,
        "paradas_geograficas": stop_locations[:250],
        "vehiculos_actuales": current_vehicles,
        "ultima_posicion": latest_position,
        "ranking": {
            "mas_usado": routes[0] if routes else None,
            "menos_usado": least,
        },
        "paradas_por_vehiculo": stop_rows[:10],
        "metodo": _method(),
    }

def _method():
    return {
        "tipo_geometria": "GeoJSON LineString",
        "descripcion_ruta": (
            "Cada trip se compara con el registro de rutas por origen, destino, "
            "direccion y geometria compartida; la linea representativa conserva "
            "puntos GPS reales filtrados."
        ),
        "agrupacion_ruta": {
            "tolerancia_corredor_km": ROUTE_CORRIDOR_TOLERANCE_KM,
            "geometria_compartida_minima": ROUTE_SHARED_GEOMETRY_RATIO,
            "tolerancia_origen_destino_km": {
                "minima": ROUTE_ENDPOINT_MIN_KM,
                "maxima": ROUTE_ENDPOINT_MAX_KM,
                "proporcion_distancia": ROUTE_ENDPOINT_LENGTH_RATIO,
            },
            "misma_direccion": True,
            "coordenadas_exactas_como_id": False,
        },
        "clasificacion": (
            "La frecuencia se calcula por ruta completa. HIGH, MEDIUM y LOW usan "
            "umbrales centralizados sobre las frecuencias observadas."
        ),
        "map_matching_activo": False,
        "map_matching_pendiente": (
            "Para ajuste exacto a carretera se requiere OSRM local y un extracto "
            "OpenStreetMap de la zona operacional."
        ),
        "descripcion_parada": (
            "Evento de origen 'Inicio de detenido', sin duplicar posicion y fecha."
        ),
    }
