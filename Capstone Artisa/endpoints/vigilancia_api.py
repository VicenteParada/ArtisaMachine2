"""Respuesta de /api/vigilancia: cómo se movió la flota en los últimos 7 días y alertas.

Sin rutas predefinidas: el mapa se divide en celdas de ~500 m y para cada una se cuenta
cuántos camiones distintos pasaron por ella en los últimos 7 días (hasta este minuto). El
porcentaje de la flota que pasó por una zona dice qué tan habitual es.

- Huella: entre dos reportes seguidos de un camión (5 min o menos y bajo 150 km/h) se
  marcan también las celdas intermedias, así una carretera queda continua aunque cada
  camión reporte en puntos distintos. Los saltos de GPS (más de 150 km/h) se descartan.
- Alerta: un camión en movimiento, fuera de las faenas, que durante al menos
  VIGILANCIA_REPORTES reportes anda por zonas donde pasó menos del VIGILANCIA_UMBRAL_PCT % de
  la flota (se mira la celda y sus vecinas, ~1,5 km, para no alertar por el borde de una
  carretera). La alerta termina cuando el camión vuelve a una zona habitual o entra a una
  faena. Es un comportamiento inusual para revisar, no una falta comprobada.

Solo camiones del catálogo. Se calcula con los reportes del histórico (raw_gps) y de la API
de la empresa (telemetria_api); no escribe nada en la base.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os

import pymysql

from endpoints import rutas_api

VENTANA_DIAS = 7
ESCALA = 200                 # celda de 0,005° (~500 m): celda = floor(lat * 200)
PASO = 0.5 / ESCALA          # muestreo de los tramos: media celda
MAX_TRAMO_MIN = 5            # tramos de la huella: reportes seguidos a 5 min o menos
VEL_MAX_KMH = 150            # más rápido es un salto de GPS
VEL_MOVIMIENTO = 5           # km/h
UMBRAL_PCT = float(os.environ.get("VIGILANCIA_UMBRAL_PCT", "5"))
REPORTES = int(os.environ.get("VIGILANCIA_REPORTES", "3"))
ACTIVA_MIN = 30              # una alerta sin reportes nuevos en 30 min queda "sin señal"
CORTE_HORAS = 6              # más de 6 h sin reportes separa dos episodios
RADIO_BUSQUEDA = 12          # celdas de 0,1° alrededor para la distancia (~130 km)
MAX_ALERTAS = 200
MAX_CELDAS = 120_000         # más celdas que esto se dibujan al doble de tamaño (solo el mapa)
MAX_ESTELA = 160


def _gps_valido(lat, lon):
    return (lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
            and not (lat == 0 and lon == 0))


def distancia_km(lat0, lon0, lat1, lon1):
    dlat = math.radians(lat1 - lat0)
    dlon = math.radians(lon1 - lon0)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat0)) * math.cos(math.radians(lat1)) * math.sin(dlon / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(a)))


def celda(lat, lon):
    return math.floor(lat * ESCALA), math.floor(lon * ESCALA)


def _centro(i):
    return (i + 0.5) / ESCALA


def _iso(value):
    return value.isoformat() + "Z" if isinstance(value, datetime) else value


# --- Lectura ---------------------------------------------------------------------------------

def _catalogo(cursor):
    cursor.execute(
        """SELECT v.asset_id FROM vehiculos AS v INNER JOIN tipos_vehiculo AS t ON t.id_tipo = v.id_tipo
           WHERE t.nombre = 'camion'"""
    )
    return {fila["asset_id"] for fila in cursor.fetchall()}


def leer_reportes(cursor, desde, hasta, catalogo):
    """{asset_id: [(report_date, lat, lon, speed), ...]} ordenados, sin repetidos ni saltos."""
    filas = []
    for tabla, indice in (("raw_gps", "idx_raw_gps_fecha"), ("telemetria_api", "idx_telemetria_api_fecha_recibido")):
        try:
            cursor.execute(
                f"""SELECT asset_id, report_date, latitude, longitude, speed FROM {tabla} USE INDEX ({indice})
                    WHERE report_date >= %s AND report_date <= %s""",
                (desde, hasta),
            )
        except pymysql.err.ProgrammingError:
            continue
        filas.extend(cursor.fetchall())
    por_camion = defaultdict(dict)
    for fila in filas:
        if fila["asset_id"] not in catalogo:
            continue
        lat = None if fila["latitude"] is None else float(fila["latitude"])
        lon = None if fila["longitude"] is None else float(fila["longitude"])
        if not _gps_valido(lat, lon):
            continue
        speed = None if fila["speed"] is None else float(fila["speed"])
        por_camion[fila["asset_id"]][fila["report_date"]] = (fila["report_date"], lat, lon, speed)
    limpios = {}
    for asset_id, por_hora in por_camion.items():
        puntos = []
        for punto in sorted(por_hora.values()):
            if puntos:
                t0, la0, lo0, _ = puntos[-1]
                horas = (punto[0] - t0).total_seconds() / 3600
                if horas <= 0:
                    continue
                if horas < 1 and distancia_km(la0, lo0, punto[1], punto[2]) / horas > VEL_MAX_KMH:
                    continue  # salto de GPS
            puntos.append(punto)
        if puntos:
            limpios[asset_id] = puntos
    return limpios


def leer_faenas(cursor):
    """[(nombre, (min_lon, min_lat, max_lon, max_lat), [anillos])] con coordenadas [lon, lat]."""
    try:
        cursor.execute("SELECT nombre, ST_AsGeoJSON(geocerca) AS geojson FROM faenas")
    except pymysql.err.ProgrammingError:
        return []
    faenas = []
    for fila in cursor.fetchall():
        if not fila.get("geojson"):
            continue
        geometria = json.loads(fila["geojson"])
        poligonos = geometria["coordinates"] if geometria["type"] == "MultiPolygon" else [geometria["coordinates"]]
        for anillos in poligonos:
            exterior = anillos[0]
            lons = [p[0] for p in exterior]
            lats = [p[1] for p in exterior]
            faenas.append((fila["nombre"], (min(lons), min(lats), max(lons), max(lats)), anillos))
    return faenas


def _dentro(lon, lat, anillo):
    dentro = False
    for (x0, y0), (x1, y1) in zip(anillo, anillo[1:] + anillo[:1]):
        if (y0 > lat) != (y1 > lat) and lon < (x1 - x0) * (lat - y0) / (y1 - y0) + x0:
            dentro = not dentro
    return dentro


def en_faena(faenas, lat, lon):
    for nombre, (x0, y0, x1, y1), anillos in faenas:
        if x0 <= lon <= x1 and y0 <= lat <= y1 and _dentro(lon, lat, anillos[0]) \
                and not any(_dentro(lon, lat, hueco) for hueco in anillos[1:]):
            return nombre
    return None


# --- Huella de 7 días -----------------------------------------------------------------------

def celdas_tramo(la0, lo0, la1, lo1):
    pasos = max(1, math.ceil(max(abs(la1 - la0), abs(lo1 - lo0)) / PASO))
    return {celda(la0 + (la1 - la0) * k / pasos, lo0 + (lo1 - lo0) * k / pasos) for k in range(pasos + 1)}


def huella(por_camion):
    """({celda: camiones distintos que pasaron por ella}, {camión: sus celdas})."""
    conteo, propias = defaultdict(int), {}
    for asset_id, puntos in por_camion.items():
        vistas = {celda(p[1], p[2]) for p in puntos}
        for (t0, la0, lo0, _), (t1, la1, lo1, _) in zip(puntos, puntos[1:]):
            if (t1 - t0).total_seconds() <= MAX_TRAMO_MIN * 60:
                vistas |= celdas_tramo(la0, lo0, la1, lo1)
        for c in vistas:
            conteo[c] += 1
        propias[asset_id] = vistas
    return conteo, propias


class Zonas:
    """Qué tan habitual es cada lugar: % de la flota en la celda y sus vecinas. Al revisar un
    camión se le pasan sus propias celdas para compararlo solo con el resto de la flota."""

    def __init__(self, conteo, camiones):
        self.conteo = conteo
        self.camiones = max(1, camiones)
        self.minimo = UMBRAL_PCT / 100 * self.camiones
        # Celdas habituales agrupadas en cuadros de 0,1° para medir distancias rápido.
        self.indice = defaultdict(list)
        for (i, j), n in conteo.items():
            if n >= self.minimo:
                lat, lon = _centro(i), _centro(j)
                self.indice[(math.floor(lat * 10), math.floor(lon * 10))].append((lat, lon, (i, j)))

    def pct(self, lat, lon, propias=frozenset()):
        i, j = celda(lat, lon)
        vecinas = [(i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)]
        n = max(self.conteo.get(c, 0) - (c in propias) for c in vecinas)
        otros = self.camiones - 1 if propias else self.camiones
        return n / max(1, otros) * 100

    def habitual(self, lat, lon, propias=frozenset()):
        return self.pct(lat, lon, propias) >= UMBRAL_PCT

    def distancia(self, lat, lon, propias=frozenset()):
        """Km a la zona habitual (para el resto de la flota) más cercana; None si no hay
        ninguna a ~130 km."""
        return (self.cercana(lat, lon, propias) or (None, None))[0]

    def cercana(self, lat, lon, propias=frozenset()):
        """(km, [lat, lon]) de la zona habitual más cercana; None si no hay a ~130 km."""
        otros = max(1, self.camiones - 1 if propias else self.camiones)
        bi, bj = math.floor(lat * 10), math.floor(lon * 10)
        mejor, punto = None, None
        for radio in range(RADIO_BUSQUEDA + 1):
            if mejor is not None and (radio - 1) * 0.1 * 111 > mejor:
                break
            for di in range(-radio, radio + 1):
                for dj in range(-radio, radio + 1):
                    if max(abs(di), abs(dj)) != radio:
                        continue
                    for la, lo, c in self.indice.get((bi + di, bj + dj), ()):
                        if c in propias and (self.conteo[c] - 1) / otros * 100 < UMBRAL_PCT:
                            continue  # solo la usó este camión
                        d = distancia_km(lat, lon, la, lo)
                        if mejor is None or d < mejor:
                            mejor, punto = d, [round(la, 5), round(lo, 5)]
        return None if mejor is None else (mejor, punto)


# --- Alertas ----------------------------------------------------------------------------------

def _en_movimiento(punto, anterior):
    if punto[3] is not None:
        return punto[3] >= VEL_MOVIMIENTO
    if anterior is None:
        return False
    horas = (punto[0] - anterior[0]).total_seconds() / 3600
    return horas > 0 and distancia_km(anterior[1], anterior[2], punto[1], punto[2]) / horas >= VEL_MOVIMIENTO


def episodios(puntos, zonas, faenas, propias=frozenset()):
    """Tramos del camión fuera de las zonas habituales: [(puntos, cerrado, movimientos)]."""
    resultado, actual, movimientos = [], None, 0
    anterior = None
    for punto in puntos:
        if anterior is not None and actual and (punto[0] - anterior[0]).total_seconds() > CORTE_HORAS * 3600:
            resultado.append((actual, True, movimientos))
            actual, movimientos = None, 0
        _, lat, lon, _ = punto
        if en_faena(faenas, lat, lon) or zonas.habitual(lat, lon, propias):
            if actual:
                resultado.append((actual, True, movimientos))
            actual, movimientos = None, 0
        else:
            moviendo = _en_movimiento(punto, anterior)
            if actual is None and moviendo:
                actual, movimientos = [], 0
            if actual is not None:
                actual.append(punto)
                movimientos += moviendo
        anterior = punto
    if actual:
        resultado.append((actual, False, movimientos))
    return [(e, cerrado, n) for e, cerrado, n in resultado if n >= REPORTES]


def _identidad(cursor, asset_id, hasta):
    """Nombre, conductor y ubicación informada en el último reporte hasta ese momento."""
    for tabla in ("telemetria_api", "raw_gps"):
        try:
            cursor.execute(
                f"""SELECT asset_name, driver_name, driver_last_name, location FROM {tabla}
                    WHERE asset_id=%s AND report_date <= %s ORDER BY report_date DESC LIMIT 1""",
                (asset_id, hasta),
            )
        except pymysql.err.ProgrammingError:
            continue
        fila = cursor.fetchone()
        if fila:
            conductor = " ".join(filter(None, (fila.get("driver_name"), fila.get("driver_last_name")))).strip()
            return {"nombre": fila.get("asset_name"), "conductor": conductor or None, "ubicacion": fila.get("location")}
    return {"nombre": None, "conductor": None, "ubicacion": None}


def _estela(puntos):
    paso = max(1, math.ceil(len(puntos) / MAX_ESTELA))
    elegidos = puntos[::paso]
    if elegidos[-1] is not puntos[-1]:
        elegidos.append(puntos[-1])
    return [[round(p[1], 5), round(p[2], 5)] for p in elegidos]


def alertas(cursor, por_camion, propias, zonas, faenas, ahora):
    lista = []
    for asset_id, puntos in por_camion.items():
        for episodio, cerrado, movimientos in episodios(puntos, zonas, faenas, propias[asset_id]):
            inicio, fin = episodio[0][0], episodio[-1][0]
            if cerrado:
                estado = "terminada"
            else:
                estado = "activa" if (ahora - fin).total_seconds() <= ACTIVA_MIN * 60 else "sin_senal"
            muestra = episodio[::max(1, len(episodio) // 40)] + [episodio[-1]]
            distancias = [zonas.distancia(p[1], p[2], propias[asset_id]) for p in muestra]
            lejos = None if any(d is None for d in distancias) else max(distancias)
            recorrido = sum(distancia_km(a[1], a[2], b[1], b[2]) for a, b in zip(episodio, episodio[1:]))
            ultimo = episodio[-1]
            # Desde la última posición hasta el corredor habitual más cercano (para dibujarlo).
            cercana = zonas.cercana(ultimo[1], ultimo[2], propias[asset_id])
            lista.append({
                "patente": asset_id, "estado": estado,
                "inicio": _iso(inicio), "fin": _iso(fin),
                "minutos": round((fin - inicio).total_seconds() / 60),
                "reportes": len(episodio), "reportes_en_movimiento": movimientos,
                "km_recorridos": round(recorrido, 1),
                "km_al_corredor": None if lejos is None else round(lejos, 1),
                "pct_zona": round(min(zonas.pct(p[1], p[2], propias[asset_id]) for p in episodio), 1),
                "posicion": [round(ultimo[1], 5), round(ultimo[2], 5)],
                "velocidad": ultimo[3], "estela": _estela(episodio),
                "corredor": None if cercana is None else {"km": round(cercana[0], 1), "punto": cercana[1]},
            })
    # Primero las activas, después las sin señal y las terminadas; dentro, las más recientes.
    orden = {"activa": 0, "sin_senal": 1, "terminada": 2}
    lista.sort(key=lambda a: a["fin"], reverse=True)
    lista = sorted(lista, key=lambda a: orden[a["estado"]])[:MAX_ALERTAS]
    for alerta in lista:
        alerta.update(_identidad(cursor, alerta["patente"], datetime.fromisoformat(alerta["fin"][:-1])))
    return lista


# --- Respuesta ------------------------------------------------------------------------------

def celdas_mapa(conteo):
    """(escala, [[i, j, camiones]]) para dibujar; si son demasiadas, celdas el doble de grandes
    con el mayor conteo de las que agrupa. Las alertas siempre usan las celdas finas."""
    escala, celdas = ESCALA, conteo
    while len(celdas) > MAX_CELDAS and escala > 25:
        escala //= 2
        gruesas = defaultdict(int)
        for (i, j), n in celdas.items():
            clave = (i // 2, j // 2)
            gruesas[clave] = max(gruesas[clave], n)
        celdas = gruesas
    return escala, [[i, j, n] for (i, j), n in celdas.items()]


def vigilancia(cursor, ahora=None):
    """None si no hay reportes de camiones en los últimos 7 días (la API responde "pendiente")."""
    ahora = ahora or datetime.now(timezone.utc).replace(tzinfo=None)
    desde = ahora - timedelta(days=VENTANA_DIAS)
    catalogo = _catalogo(cursor)
    por_camion = leer_reportes(cursor, desde, ahora, catalogo)
    if not por_camion:
        return None
    conteo, propias = huella(por_camion)
    zonas = Zonas(conteo, len(por_camion))
    faenas = leer_faenas(cursor)
    lista = alertas(cursor, por_camion, propias, zonas, faenas, ahora)
    en_alerta = {a["patente"] for a in lista if a["estado"] == "activa"}
    vehiculos = rutas_api.latest_positions(cursor, {})
    escala, celdas = celdas_mapa(conteo)
    for vehiculo in vehiculos:
        vehiculo["en_alerta"] = vehiculo["vehicle_id"] in en_alerta
    return {
        "status": "ok", "generado_en": _iso(ahora), "desde": _iso(desde), "hasta": _iso(ahora),
        "escala": escala, "camiones": len(por_camion), "catalogo": len(catalogo),
        "reportes": sum(len(p) for p in por_camion.values()),
        "umbral_pct": UMBRAL_PCT, "reportes_alerta": REPORTES, "refresh_seconds": rutas_api.REFRESH_SECONDS,
        # [i, j, camiones]: la celda va de i/escala a (i+1)/escala en latitud (j, en longitud).
        "celdas": celdas,
        "alertas": lista,
        "resumen": {
            "activas": sum(a["estado"] == "activa" for a in lista),
            "sin_senal": sum(a["estado"] == "sin_senal" for a in lista),
            "terminadas": sum(a["estado"] == "terminada" for a in lista),
            "camiones_con_alertas": len({a["patente"] for a in lista}),
        },
        "vehiculos": vehiculos,
    }
