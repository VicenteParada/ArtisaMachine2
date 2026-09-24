"""Recorrido GPS de un camión en el día de su última lectura o una fecha pedida."""

from datetime import date, datetime, timedelta

from common.telemetry_map import _distance_km, _limit_points, _simplify

MAX_REPORTS = 20000
MAX_GAP_SECONDS = 45 * 60
MAX_SPEED_KMH = 160


def _valid(row):
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
        return -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon)
    except (TypeError, ValueError):
        return False


def load_vehicle_journey(cursor, asset_id, day=None):
    if not asset_id or len(asset_id) > 16:
        raise ValueError("asset_id inválido")
    if day:
        selected_day = date.fromisoformat(day)
    else:
        cursor.execute(
            "SELECT report_date FROM raw_gps WHERE asset_id=%s "
            "ORDER BY report_date DESC LIMIT 1", (asset_id,))
        last = cursor.fetchone()
        if not last:
            return None
        selected_day = last["report_date"].date()
    start = datetime.combine(selected_day, datetime.min.time())
    end = start + timedelta(days=1)
    cursor.execute(
        """SELECT report_id,report_date,asset_id,asset_name,latitude,longitude,
           speed,ignition,event_name,driver_name,driver_last_name
           FROM raw_gps WHERE asset_id=%s AND report_date >= %s
           AND report_date < %s ORDER BY report_date DESC LIMIT %s""",
        (asset_id, start, end, MAX_REPORTS + 1),
    )
    rows = cursor.fetchall()
    if not rows:
        return None
    complete = len(rows) <= MAX_REPORTS
    rows = list(reversed(rows[:MAX_REPORTS]))
    segments = []
    current = []
    distance = 0.0
    moving_seconds = 0.0
    stops = []
    slow = []

    def finish_stop():
        if len(slow) < 2:
            slow.clear()
            return
        duration = (slow[-1]["report_date"] - slow[0]["report_date"]).total_seconds() / 60
        lat = sum(float(row["latitude"]) for row in slow) / len(slow)
        lon = sum(float(row["longitude"]) for row in slow) / len(slow)
        radius = max(_distance_km((lat, lon),
                    (float(row["latitude"]), float(row["longitude"]))) for row in slow)
        if duration >= 5 and radius <= 0.2:
            stops.append({
                "latitude": lat, "longitude": lon,
                "inicio": slow[0]["report_date"].isoformat(),
                "fin": slow[-1]["report_date"].isoformat(),
                "duracion_minutos": round(duration, 1),
            })
        slow.clear()

    def finish_segment():
        if len(current) > 1:
            coordinates = _limit_points(_simplify(current), 1200)
            segments.append([[lon, lat] for lat, lon in coordinates])
        current.clear()

    previous = None
    valid_rows = []
    for row in rows:
        if not _valid(row):
            continue
        point = (float(row["latitude"]), float(row["longitude"]))
        if previous is not None:
            elapsed = (row["report_date"] - previous["report_date"]).total_seconds()
            step = _distance_km(
                (float(previous["latitude"]), float(previous["longitude"])), point)
            if elapsed <= 0 or elapsed > MAX_GAP_SECONDS or step / max(elapsed / 3600, 1e-6) > MAX_SPEED_KMH:
                finish_stop()
                finish_segment()
            else:
                distance += step
                if ((row["speed"] is not None and float(row["speed"]) > 3) or
                        (previous["speed"] is not None and float(previous["speed"]) > 3)):
                    moving_seconds += min(elapsed, 300)
        current.append(point)
        valid_rows.append(row)
        speed = row["speed"]
        if speed is not None and 0 <= float(speed) <= 3:
            slow.append(row)
        else:
            finish_stop()
        previous = row
    finish_stop()
    finish_segment()
    if not valid_rows:
        return None
    first, last = valid_rows[0], valid_rows[-1]
    now = datetime.utcnow()
    age_seconds = max(0, (now - last["report_date"]).total_seconds())
    if age_seconds > 30 * 60:
        status = "sin_senal"
    elif last["ignition"] is not None and int(last["ignition"]) == 0:
        status = "apagado"
    elif last["speed"] is not None and float(last["speed"]) > 3:
        status = "en_movimiento"
    else:
        status = "detenido"
    return {
        "asset_id": asset_id, "asset_name": last.get("asset_name") or asset_id,
        "patente": None, "driver_name": last.get("driver_name"),
        "driver_last_name": last.get("driver_last_name"),
        "fecha": selected_day.isoformat(), "inicio": first["report_date"].isoformat(),
        "termino_observado": last["report_date"].isoformat(),
        "estado": status, "ignition": last["ignition"],
        "completo": complete, "lecturas": len(valid_rows),
        "distancia_km": round(distance, 2),
        "tiempo_movimiento_minutos": round(moving_seconds / 60, 1),
        "viajes_encadenados": None, "paradas": stops,
        "geometry": {"type": "MultiLineString", "coordinates": segments},
        "nota": ("Recorrido observado del día; el tiempo en movimiento es una estimación "
                 "GPS. No equivale necesariamente a una jornada laboral ni confirma "
                 "viajes encadenados o el fin de un turno."),
    }
