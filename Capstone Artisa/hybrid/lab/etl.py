from datetime import datetime, timezone
import math
import re

from psycopg.types.json import Jsonb

from lab.db import connect, initialize_local
from lab.fixtures import demo_events, road_features


def normalize(row):
    if not isinstance(row, dict):
        raise ValueError("registro no es un objeto")
    for key in ("event_id", "vehicle"):
        if not isinstance(row.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",row[key]):
            raise ValueError(f"{key} invalido")
    numbers = {}
    for key,limit in (("latitude",90),("longitude",180)):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value)>limit:
            raise ValueError(f"{key} fuera de rango")
        numbers[key] = float(value)
    try:
        timestamp = datetime.fromisoformat(row["recorded_at"].replace("Z","+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError
    except (KeyError,ValueError,AttributeError,TypeError):
        raise ValueError("recorded_at requiere fecha ISO con zona horaria") from None
    speed = row.get("speed_kph")
    if speed is not None and (isinstance(speed,bool) or not isinstance(speed,(int,float)) or not math.isfinite(speed) or not 0<=speed<=250):
        raise ValueError("speed_kph invalido")
    return {"event_id":row["event_id"],"vehicle":row["vehicle"],"recorded_at":timestamp.astimezone(timezone.utc),
            **numbers,"speed_kph":speed}


def setup():
    initialize_local()
    # Solo en este laboratorio: la base origen se crea con datos sintéticos conocidos.
    with connect("SOURCE") as source:
        source.execute("CREATE TABLE IF NOT EXISTS raw_events(seq BIGSERIAL PRIMARY KEY,payload JSONB NOT NULL)")
        if source.execute("SELECT count(*) AS n FROM raw_events").fetchone()["n"] == 0:
            with source.cursor() as cursor:
                cursor.executemany("INSERT INTO raw_events(payload) VALUES(%s)", [(Jsonb(row),) for row in demo_events()])
    with connect() as db:
        for feature in road_features()["features"]:
            props = feature["properties"]
            db.execute("""INSERT INTO roads(id,start_node,end_node,name,geom)
                       VALUES(%s,%s,%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)) ON CONFLICT(id) DO NOTHING""",
                       (props["id"],props["start"],props["end"],props["name"],Jsonb(feature["geometry"])))


def cycle():
    with connect() as db:
        checkpoint = db.execute("SELECT * FROM etl_checkpoint WHERE id=1 FOR UPDATE").fetchone()
        with connect("SOURCE") as source:
            batch = source.execute("SELECT seq,payload FROM raw_events WHERE seq>%s ORDER BY seq LIMIT 500",(checkpoint["cursor"],)).fetchall()
        accepted,rejected,duplicates = 0,0,0
        for raw in batch:
            try:
                row = normalize(raw["payload"])
            except ValueError as error:
                db.execute("INSERT INTO rejected_events(source_seq,reason) VALUES(%s,%s) ON CONFLICT DO NOTHING",(raw["seq"],str(error)))
                rejected += 1
                continue
            inserted = db.execute("""INSERT INTO gps(event_id,source_seq,vehicle,recorded_at,geom,speed_kph)
                VALUES(%s,%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),%s)
                ON CONFLICT(event_id) DO NOTHING RETURNING event_id""",
                (row["event_id"],raw["seq"],row["vehicle"],row["recorded_at"],row["longitude"],row["latitude"],row["speed_kph"])).fetchone()
            accepted += int(inserted is not None)
            duplicates += int(inserted is None)
        cursor = batch[-1]["seq"] if batch else checkpoint["cursor"]
        db.execute("""UPDATE etl_checkpoint SET cursor=%s,accepted=accepted+%s,rejected=rejected+%s,
                   duplicates=duplicates+%s,updated_at=now() WHERE id=1""", (cursor,accepted,rejected,duplicates))
        db.execute("""INSERT INTO worker_health(name,detail) VALUES('etl',%s)
                   ON CONFLICT(name) DO UPDATE SET updated_at=now(),detail=excluded.detail""",(Jsonb({"cursor":cursor}),))
    return {"cursor":cursor,"accepted":accepted,"rejected":rejected,"duplicates":duplicates}
