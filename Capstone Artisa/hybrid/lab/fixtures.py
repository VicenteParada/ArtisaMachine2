"""Red y GPS completamente sintéticos: no representan calles o viajes reales."""

from datetime import datetime, timedelta, timezone
import random


BASE_LON, BASE_LAT = -70.40, -23.65
METERS_LON, METERS_LAT = 102000.0, 111200.0
NODES = {"A": (0,0), "B": (300,0), "C": (600,0), "D": (900,0),
         "E": (300,300), "F": (600,300), "G": (900,300), "H": (0,300)}
EDGES = [("AB","A","B"), ("BC","B","C"), ("CD","C","D"), ("BE","B","E"),
         ("CF","C","F"), ("DG","D","G"), ("HE","H","E"), ("EF","E","F"),
         ("FG","F","G"), ("AH","A","H")]


def lonlat(x, y):
    return [BASE_LON+x/METERS_LON, BASE_LAT+y/METERS_LAT]


def road_features():
    return {"type":"FeatureCollection", "features":[
        {"type":"Feature", "properties":{"id":identifier,"start":start,"end":end,"name":f"Calle demo {identifier}"},
         "geometry":{"type":"LineString","coordinates":[lonlat(*NODES[start]),lonlat(*NODES[end])]}}
        for identifier,start,end in EDGES]}


def demo_events():
    result = []
    # Incluye parada real de 3 minutos, ruido, duplicados y pérdida de señal.
    scenarios = [("ART-101", ["A","B","E","F","G"], 4, False),
                 ("ART-202", ["H","E","B","C","D"], 10, True),
                 ("ART-303", ["A","B","C","F","G"], 6, False)]
    for vehicle,path,noise,gap in scenarios:
        rng = random.Random(vehicle)
        moment = datetime(2026,9,3,12,0,tzinfo=timezone.utc)
        positions = []
        for segment,(start,end) in enumerate(zip(path,path[1:])):
            sx,sy = NODES[start]; ex,ey = NODES[end]
            for step in range(10):
                positions.append((sx+(ex-sx)*step/10,sy+(ey-sy)*step/10,False))
            if segment == 1:
                positions.extend([(ex,ey,True)]*7)
        positions.append((*NODES[path[-1]],False))
        for index,(x,y,stopped) in enumerate(positions):
            if gap and index == 32:
                moment += timedelta(minutes=9)
            coordinates = lonlat(x+rng.gauss(0,noise),y+rng.gauss(0,noise))
            result.append({"event_id":f"{vehicle}-{index:03}","vehicle":vehicle,
                           "recorded_at":moment.isoformat(),"longitude":coordinates[0],"latitude":coordinates[1],
                           "speed_kph":0 if stopped else 18})
            moment += timedelta(seconds=30)
    # No se sustituye ningún evento válido; se añaden casos de rechazo explícitos.
    result.append(dict(result[0]))
    result.append({**result[1],"event_id":"INVALID-LAT","latitude":999})
    result.append({**result[2],"event_id":"INVALID-TIME","recorded_at":"ayer"})
    result.append({**result[3],"event_id":"OFF-ROAD","longitude":BASE_LON+0.03,"recorded_at":"2026-09-03T12:01:45+00:00"})
    # Orden de llegada distinto al tiempo GPS para comprobar procesamiento temporal.
    random.Random(11).shuffle(result)
    return result
