"""Respuestas de /api/rutas-aprendidas y /api/vigilancia: las rutas que aprendió el modelo
y los camiones en vivo sobre ellas, con alertas.

Rutas aprendidas (machine_learning/modelo_rutas.py, versión activa): el modelo agrupa los viajes
reales en rutas y las clasifica por cuántos camiones distintos las usaron, de N1 (troncal)
a N5 (mínima: 1 o 2 camiones). Se reaprende con cada semana de datos nuevos.

En vivo: cada posición toma el nivel de la ruta aprendida que pasa por su celda de ~500 m o
una vecina (~1 km). Si ninguna pasa cerca, el camión está "fuera de ruta" (nivel 6).

Alerta: un camión en movimiento, fuera de las faenas, que anda por una ruta mínima (N5) o
fuera de ruta (N6) durante al menos VIGILANCIA_REPORTES reportes y VIGILANCIA_MIN_MINUTOS
minutos, alejándose VIGILANCIA_MIN_KM km o más de una ruta N1–N4. Las condiciones de tiempo
y distancia evitan alertas por desvíos cortos (entrar a un cliente cerca de la carretera).
La alerta termina cuando vuelve a una ruta N1–N4 o entra a una faena. Es un comportamiento
inusual para revisar, no una falta comprobada.

Solo camiones del catálogo. Los reportes salen del histórico (raw_gps) y de la API de la
empresa (telemetria_api); no escribe nada en la base.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os

import pymysql

from endpoints import rutas_api

VENTANA_DIAS = 7             # alertas de la semana
ESCALA = 200                 # celda de 0,005° (~500 m): celda = floor(lat * 200)
PASO = 0.5 / ESCALA          # muestreo de las líneas de las rutas: media celda
VEL_MAX_KMH = 150            # más rápido es un salto de GPS
VEL_MOVIMIENTO = 5           # km/h
REPORTES = int(os.environ.get("VIGILANCIA_REPORTES", "3"))
MIN_MINUTOS = float(os.environ.get("VIGILANCIA_MIN_MINUTOS", "10"))
MIN_KM = float(os.environ.get("VIGILANCIA_MIN_KM", "2"))
HABITUAL = 4                 # N1–N4: rutas por donde anda la operación
FUERA = 6                    # nivel de un camión que no está en ninguna ruta aprendida
ACTIVA_MIN = 30              # una alerta sin reportes nuevos en 30 min queda "sin señal"
CORTE_HORAS = 6              # más de 6 h sin reportes separa dos episodios
RADIO_BUSQUEDA = 12          # celdas de 0,1° alrededor para la distancia (~130 km)
MAX_ALERTAS = 200
MAX_ESTELA = 160
MAX_RUTAS_TRAMO = 12         # rutas que se nombran al tocar un tramo del mapa


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


def _numero(nivel):
    return int(str(nivel)[1:]) if str(nivel).startswith("N") else FUERA


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


# --- Rutas aprendidas -------------------------------------------------------------------------

def muestras_tramo(la0, lo0, la1, lo1):
    pasos = max(1, math.ceil(max(abs(la1 - la0), abs(lo1 - lo0)) / PASO))
    return [(la0 + (la1 - la0) * k / pasos, lo0 + (lo1 - lo0) * k / pasos) for k in range(pasos + 1)]


def _lineas(geometria):
    coordenadas = geometria["coordinates"]
    if geometria["type"] == "LineString":
        coordenadas = [coordenadas]
    return [[[round(lat, 5), round(lon, 5)] for lon, lat in linea] for linea in coordenadas if len(linea) >= 2]


class MapaRutas:
    """Dónde pasa cada ruta aprendida: por celda, el mejor nivel (el número más bajo) y su ruta.

    También arma la red para dibujar: muchas rutas comparten la misma carretera (una por cada
    par de lugares), y dibujarlas una encima de otra se ve como un manojo de líneas. La red tiene
    un solo tramo por par de celdas vecinas recorridas, con el mejor nivel que pasa por ahí y
    qué rutas lo usan; el punto de cada celda es el promedio de por dónde pasaron las rutas, así
    queda sobre el camino y no en el centro del cuadro."""

    def __init__(self, rutas):
        self.celdas = {}
        self.sumas = defaultdict(lambda: [0.0, 0.0, 0])
        self.tramos = {}  # (celda, celda) -> [mejor nivel, {índice de ruta}]
        for k, ruta in enumerate(rutas):
            numero = _numero(ruta["nivel"])
            for linea in ruta["lineas"]:
                anterior = None
                for (la0, lo0), (la1, lo1) in zip(linea, linea[1:]):
                    for lat, lon in muestras_tramo(la0, lo0, la1, lo1):
                        c = celda(lat, lon)
                        if c not in self.celdas or numero < self.celdas[c][0]:
                            self.celdas[c] = (numero, ruta["id"])
                        suma = self.sumas[c]
                        suma[0] += lat
                        suma[1] += lon
                        suma[2] += 1
                        if anterior is not None and c != anterior:
                            clave = (anterior, c) if anterior < c else (c, anterior)
                            tramo = self.tramos.setdefault(clave, [numero, set()])
                            tramo[0] = min(tramo[0], numero)
                            tramo[1].add(k)
                        anterior = c
        # Celdas de rutas N1–N4 agrupadas en cuadros de 0,1° para medir distancias rápido.
        self.indice = defaultdict(list)
        for (i, j), (numero, _) in self.celdas.items():
            if numero <= HABITUAL:
                lat, lon = _centro(i), _centro(j)
                self.indice[(math.floor(lat * 10), math.floor(lon * 10))].append((lat, lon))

    def red(self, rutas):
        """{"nodos": [[lat, lon], …], "tramos": [[a, b, nivel, total, [rutas…]], …]} con las rutas
        de cada tramo ordenadas de la más a la menos concurrida (a lo más MAX_RUTAS_TRAMO)."""
        indice, nodos = {}, []
        for c, (la, lo, n) in self.sumas.items():
            indice[c] = len(nodos)
            nodos.append([round(la / n, 5), round(lo / n, 5)])
        orden = lambda k: (_numero(rutas[k]["nivel"]), -rutas[k]["camiones"], k)  # noqa: E731
        tramos = [[indice[a], indice[b], numero, len(usan), sorted(usan, key=orden)[:MAX_RUTAS_TRAMO]]
                  for (a, b), (numero, usan) in self.tramos.items()]
        return {"nodos": nodos, "tramos": tramos}

    def en(self, lat, lon):
        """(nivel 1..6, route_id) de la mejor ruta en la celda o sus vecinas (~1 km)."""
        i, j = celda(lat, lon)
        mejor = (FUERA, None)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                encontrado = self.celdas.get((i + di, j + dj))
                if encontrado and encontrado[0] < mejor[0]:
                    mejor = encontrado
        return mejor

    def habitual(self, lat, lon):
        return self.en(lat, lon)[0] <= HABITUAL

    def cercana(self, lat, lon):
        """(km, [lat, lon]) de la ruta N1–N4 más cercana; None si no hay a ~130 km."""
        bi, bj = math.floor(lat * 10), math.floor(lon * 10)
        mejor, punto = None, None
        for radio in range(RADIO_BUSQUEDA + 1):
            if mejor is not None and (radio - 1) * 0.1 * 111 > mejor:
                break
            for di in range(-radio, radio + 1):
                for dj in range(-radio, radio + 1):
                    if max(abs(di), abs(dj)) != radio:
                        continue
                    for la, lo in self.indice.get((bi + di, bj + dj), ()):
                        d = distancia_km(lat, lon, la, lo)
                        if mejor is None or d < mejor:
                            mejor, punto = d, [round(la, 5), round(lo, 5)]
        return None if mejor is None else (mejor, punto)

    def distancia(self, lat, lon):
        return (self.cercana(lat, lon) or (None, None))[0]


_CACHE = {"version": None, "modelo": None, "mapa": None}


def leer_modelo(cursor):
    """Versión activa del modelo de rutas, sus rutas y el índice por celdas (se guarda en
    memoria mientras la versión no cambie). None si todavía no hay ninguna."""
    version = rutas_api.active_version(cursor)
    if not version:
        return None, None
    if _CACHE["version"] == (version["id"], version["creado_en"]):
        return _CACHE["modelo"], _CACHE["mapa"]
    cursor.execute(
        """SELECT route_id, label, usage_level, usage_count, unique_vehicle_count, km_mediana, geometria,
                  primer_viaje, ultimo_viaje FROM rutas_clasificadas WHERE version_id=%s
           ORDER BY unique_vehicle_count DESC, usage_count DESC, route_id""",
        (version["id"],),
    )
    rutas = [{"id": f["route_id"], "label": f["label"], "nivel": f["usage_level"],
              "camiones": int(f["unique_vehicle_count"]), "recorridos": int(f["usage_count"]),
              "km": f["km_mediana"], "ultimo_viaje": _iso(f["ultimo_viaje"]),
              "lineas": _lineas(json.loads(f["geometria"]))} for f in cursor.fetchall()]
    metricas = version["metricas"]
    cortes = (metricas.get("umbrales") or {}).get("cortes") or []
    por_nivel = Counter(r["nivel"] for r in rutas)
    niveles = []
    for nivel, (nombre, color) in rutas_api.NIVELES.items():
        corte = next((c for c in cortes if c["nivel"] == nivel), None)
        if nivel != "N6" and corte is None:
            continue  # pocos datos: el modelo aprendió menos grupos
        niveles.append({"nivel": nivel, "nombre": nombre, "color": color,
                        "desde": corte and corte["desde"], "hasta": corte and corte["hasta"],
                        "rutas": por_nivel.get(nivel, 0),
                        "recorridos": sum(r["recorridos"] for r in rutas if r["nivel"] == nivel),
                        "alerta": _numero(nivel) > HABITUAL})
    parametros = metricas.get("parametros") or {}
    cada = int(os.environ.get("RUTAS_CADA_DIAS", "7"))
    modelo = {
        "version": version["id"], "creado_en": _iso(version["creado_en"]),
        "datos_desde": version["desde"].isoformat(), "datos_hasta": version["hasta"].isoformat(),
        "proxima_con_datos_hasta": (version["hasta"] + timedelta(days=cada)).isoformat(),
        "cada_dias": cada,
        "viajes": metricas.get("viajes_clasificados", 0), "camiones": metricas.get("camiones", 0),
        "lugares": metricas.get("lugares", 0), "total_rutas": len(rutas),
        "parametros": parametros, "niveles": niveles,
    }
    mapa = MapaRutas(rutas)
    _CACHE.update(version=(version["id"], version["creado_en"]), modelo=dict(modelo, rutas=rutas, red=mapa.red(rutas)),
                  mapa=mapa)
    return _CACHE["modelo"], mapa


def rutas_aprendidas(cursor):
    """Las rutas del modelo con su nivel y línea (cambia una vez por semana). None: pendiente."""
    modelo, _ = leer_modelo(cursor)
    return None if modelo is None else dict(modelo, status="ok")


# --- Alertas ----------------------------------------------------------------------------------

def _en_movimiento(punto, anterior):
    if punto[3] is not None:
        return punto[3] >= VEL_MOVIMIENTO
    if anterior is None:
        return False
    horas = (punto[0] - anterior[0]).total_seconds() / 3600
    return horas > 0 and distancia_km(anterior[1], anterior[2], punto[1], punto[2]) / horas >= VEL_MOVIMIENTO


def episodios(puntos, mapa, faenas):
    """Tramos del camión fuera de las rutas N1–N4: [(puntos, cerrado, movimientos)]."""
    resultado, actual, movimientos = [], None, 0
    anterior = None
    for punto in puntos:
        if anterior is not None and actual and (punto[0] - anterior[0]).total_seconds() > CORTE_HORAS * 3600:
            resultado.append((actual, True, movimientos))
            actual, movimientos = None, 0
        _, lat, lon, _ = punto
        if en_faena(faenas, lat, lon) or mapa.habitual(lat, lon):
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


def alertas(cursor, por_camion, mapa, etiquetas, faenas, ahora):
    lista = []
    for asset_id, puntos in por_camion.items():
        for episodio, cerrado, movimientos in episodios(puntos, mapa, faenas):
            inicio, fin = episodio[0][0], episodio[-1][0]
            if cerrado:
                estado = "terminada"
            else:
                estado = "activa" if (ahora - fin).total_seconds() <= ACTIVA_MIN * 60 else "sin_senal"
            minutos = (fin - inicio).total_seconds() / 60
            if minutos < MIN_MINUTOS:
                continue  # desvío corto: no alcanza a ser alerta
            muestra = episodio[::max(1, len(episodio) // 40)] + [episodio[-1]]
            distancias = [mapa.distancia(p[1], p[2]) for p in muestra]
            lejos = None if any(d is None for d in distancias) else max(distancias)
            if lejos is not None and lejos < MIN_KM:
                continue  # anduvo a menos de MIN_KM de una ruta N1–N4: no es un desvío real
            recorrido = sum(distancia_km(a[1], a[2], b[1], b[2]) for a, b in zip(episodio, episodio[1:]))
            ultimo = episodio[-1]
            donde = [mapa.en(p[1], p[2]) for p in episodio]
            minimas = Counter(ruta for numero, ruta in donde if numero == 5)
            nivel = FUERA if any(numero == FUERA for numero, _ in donde) else 5
            ruta = minimas.most_common(1)[0][0] if minimas else None
            # Desde la última posición hasta la ruta N1–N4 más cercana (para dibujarlo).
            cercana = mapa.cercana(ultimo[1], ultimo[2])
            lista.append({
                "patente": asset_id, "estado": estado,
                "inicio": _iso(inicio), "fin": _iso(fin),
                "minutos": round(minutos),
                "reportes": len(episodio), "reportes_en_movimiento": movimientos,
                "km_recorridos": round(recorrido, 1),
                "km_al_corredor": None if lejos is None else round(lejos, 1),
                "nivel": f"N{nivel}", "ruta": ruta, "ruta_label": etiquetas.get(ruta),
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

def vigilancia(cursor, ahora=None):
    """Camiones en vivo sobre las rutas aprendidas y alertas de la semana. None mientras el
    modelo no haya aprendido ninguna ruta (la API responde "pendiente")."""
    ahora = ahora or datetime.now(timezone.utc).replace(tzinfo=None)
    modelo, mapa = leer_modelo(cursor)
    if modelo is None:
        return None
    desde = ahora - timedelta(days=VENTANA_DIAS)
    catalogo = _catalogo(cursor)
    por_camion = leer_reportes(cursor, desde, ahora, catalogo)
    etiquetas = {r["id"]: r["label"] for r in modelo["rutas"]}
    lista = alertas(cursor, por_camion, mapa, etiquetas, leer_faenas(cursor), ahora)
    en_alerta = {a["patente"] for a in lista if a["estado"] == "activa"}
    vehiculos = rutas_api.latest_positions(cursor, {})
    ahora_por_nivel = Counter()
    for vehiculo in vehiculos:
        vehiculo["en_alerta"] = vehiculo["vehicle_id"] in en_alerta
        vehiculo["nivel"] = vehiculo["ruta"] = vehiculo["ruta_label"] = None
        lat, lon = vehiculo.get("latitude"), vehiculo.get("longitude")
        momento = vehiculo.get("timestamp")
        reciente = momento and (ahora - datetime.fromisoformat(str(momento).rstrip("Z"))).total_seconds() <= ACTIVA_MIN * 60
        if reciente and _gps_valido(lat, lon):
            numero, ruta = mapa.en(float(lat), float(lon))
            vehiculo.update(nivel=f"N{numero}", ruta=ruta, ruta_label=etiquetas.get(ruta))
            ahora_por_nivel[f"N{numero}"] += 1
    return {
        "status": "ok", "generado_en": _iso(ahora), "desde": _iso(desde), "hasta": _iso(ahora),
        "modelo": {k: v for k, v in modelo.items() if k not in ("rutas", "red")},
        "camiones": len(por_camion), "catalogo": len(catalogo),
        "reportes": sum(len(p) for p in por_camion.values()),
        "reportes_alerta": REPORTES, "refresh_seconds": rutas_api.REFRESH_SECONDS,
        "min_minutos": MIN_MINUTOS, "min_km": MIN_KM,
        "alertas": lista,
        "resumen": {
            "activas": sum(a["estado"] == "activa" for a in lista),
            "sin_senal": sum(a["estado"] == "sin_senal" for a in lista),
            "terminadas": sum(a["estado"] == "terminada" for a in lista),
            "camiones_con_alertas": len({a["patente"] for a in lista}),
            "camiones_por_nivel": dict(ahora_por_nivel),
        },
        "vehiculos": vehiculos,
    }
