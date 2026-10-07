"""Clasificación de rutas completas: agrupa viajes comparables y mide su uso.

Método (todo no supervisado y auditable):
1. Lugares: cada extremo de viaje (parada de origen o destino) se asigna a la faena
   cuya geocerca lo contiene; si no cae en ninguna, se agrupa con DBSCAN (radio
   LUGAR_RADIO_M). Los extremos aislados quedan como lugares propios.
2. Pares origen-destino: los viajes se agrupan por par de lugares sin importar el
   sentido (ida y vuelta por el mismo camino es la misma ruta).
3. Trayectorias: dentro de cada par, cada viaje se resume en MUESTRAS puntos a igual
   distancia a lo largo del recorrido y se compara con la distancia media entre puntos.
   Clustering jerárquico (enlace promedio, umbral RUTA_DIST_M): los viajes que siguen el
   mismo camino forman una ruta; un desvío real forma otra ruta.
4. Geometría: el viaje medoide (el más parecido a todos los demás de la ruta). Es un
   recorrido real observado: no se promedian ni inventan líneas.
5. Nivel de uso: por cantidad de recorridos, con percentiles sobre todas las rutas:
   HIGH sobre el percentil 66,7, LOW hasta el 33,3, MEDIUM entre ambos. Los umbrales
   quedan guardados en cada versión.

Los viajes sin origen o destino conocido (cortados por falta de reportes) no se
clasifican y se informan aparte.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import math

import numpy as np

LUGAR_RADIO_M = 500
LUGAR_MIN_VIAJES = 3
RUTA_DIST_M = 750
MUESTRAS = 32
MAX_GRUPO = 2500
MIN_RECORRIDOS = 5   # una ruta de la operación se repite...
MIN_VEHICULOS = 2    # ...y la usa más de un camión
METROS_POR_GRADO = 111_000
NIVELES = {"HIGH": ("Más usada", "#14532d"), "MEDIUM": ("Uso medio", "#f97316"), "LOW": ("Poco usada", "#dc2626")}


@dataclass
class ViajeRuta:
    asset_id: str
    inicio: datetime
    fin: datetime
    origen: tuple
    destino: tuple
    km: float
    puntos: int
    con_saltos: bool
    tramos: list            # [[(lat, lon), ...], ...]
    lugar_origen: str = None
    lugar_destino: str = None
    invertido: bool = False
    extra: dict = field(default_factory=dict)


# --- Lugares ----------------------------------------------------------------------

def _dentro_anillo(lat, lon, anillo):
    dentro = False
    for (x0, y0), (x1, y1) in zip(anillo, anillo[1:] + anillo[:1]):
        if (y0 > lat) != (y1 > lat) and lon < (x1 - x0) * (lat - y0) / ((y1 - y0) or 1e-12) + x0:
            dentro = not dentro
    return dentro


def dentro_geojson(lat, lon, geometria):
    poligonos = [geometria["coordinates"]] if geometria["type"] == "Polygon" else geometria["coordinates"]
    for poligono in poligonos:
        if _dentro_anillo(lat, lon, poligono[0]) and not any(_dentro_anillo(lat, lon, h) for h in poligono[1:]):
            return True
    return False


def asignar_lugares(viajes, faenas):
    """Asigna lugar_origen/lugar_destino a cada viaje. Devuelve {clave: info del lugar}."""
    from sklearn.cluster import DBSCAN

    extremos = []  # (viaje, "origen"/"destino", lat, lon)
    for v in viajes:
        extremos.append((v, "origen", *v.origen))
        extremos.append((v, "destino", *v.destino))
    lugares, libres = {}, []
    for k, (v, lado, lat, lon) in enumerate(extremos):
        faena = next((f for f in faenas if dentro_geojson(lat, lon, f["geojson"])), None)
        if faena:
            clave = f"F{faena['id']}"
            lugar = lugares.setdefault(clave, {"clave": clave, "nombre": faena["nombre"], "tipo": "faena",
                                               "puntos": [], "muestras": []})
            lugar["puntos"].append((lat, lon))
            if len(lugar["muestras"]) < 5:
                lugar["muestras"].append((v.asset_id, v.inicio if lado == "origen" else v.fin))
            setattr(v, f"lugar_{lado}", clave)
        else:
            libres.append(k)
    if libres:
        coords = np.radians([[extremos[k][2], extremos[k][3]] for k in libres])
        etiquetas = DBSCAN(eps=LUGAR_RADIO_M / 6_371_000, min_samples=LUGAR_MIN_VIAJES,
                           metric="haversine", algorithm="ball_tree").fit_predict(coords)
        for k, etiqueta in zip(libres, etiquetas):
            v, lado, lat, lon = extremos[k]
            clave = f"L{int(etiqueta)}" if etiqueta >= 0 else f"P{lat:.3f},{lon:.3f}"
            tipo = "lugar" if etiqueta >= 0 else "punto"
            lugar = lugares.setdefault(clave, {"clave": clave, "nombre": None, "tipo": tipo,
                                               "puntos": [], "muestras": []})
            lugar["puntos"].append((lat, lon))
            if len(lugar["muestras"]) < 5:
                lugar["muestras"].append((v.asset_id, v.inicio if lado == "origen" else v.fin))
            setattr(v, f"lugar_{lado}", clave)
    numerados = sorted((l for l in lugares.values() if l["tipo"] != "faena"),
                       key=lambda l: (-len(l["puntos"]), l["clave"]))
    for n, lugar in enumerate(numerados, start=1):
        lugar["nombre"] = f"Lugar {n}" if lugar["tipo"] == "lugar" else f"Punto aislado {n}"
    for lugar in lugares.values():
        pts = lugar.pop("puntos")
        lugar["latitud"] = round(sum(p[0] for p in pts) / len(pts), 6)
        lugar["longitud"] = round(sum(p[1] for p in pts) / len(pts), 6)
        lugar["extremos"] = len(pts)
    return lugares


# --- Trayectorias -------------------------------------------------------------------

def firma(tramos, lat_ref, n=MUESTRAS):
    """n puntos a igual distancia a lo largo del recorrido, en metros (plano local)."""
    coseno = math.cos(math.radians(lat_ref))
    xy = np.array([(lon * METROS_POR_GRADO * coseno, lat * METROS_POR_GRADO)
                   for tramo in tramos for lat, lon in tramo], dtype=float)
    if len(xy) == 1:
        return np.repeat(xy, n, axis=0)
    pasos = np.hypot(*np.diff(xy, axis=0).T)
    acumulado = np.concatenate([[0.0], np.cumsum(pasos)])
    if acumulado[-1] == 0:
        return np.repeat(xy[:1], n, axis=0)
    objetivo = np.linspace(0, acumulado[-1], n)
    return np.column_stack([np.interp(objetivo, acumulado, xy[:, 0]), np.interp(objetivo, acumulado, xy[:, 1])])


def distancias(firmas, otras=None):
    """Distancia media entre puntos homólogos (metros), por bloques para acotar memoria."""
    otras = firmas if otras is None else otras
    resultado = np.empty((len(firmas), len(otras)))
    for i in range(0, len(firmas), 256):
        bloque = firmas[i:i + 256]
        resultado[i:i + 256] = np.linalg.norm(bloque[:, None] - otras[None], axis=-1).mean(axis=-1)
    return resultado


def _agrupar(firmas, mismo_lugar):
    """Etiquetas de ruta para las firmas de un par origen-destino."""
    from sklearn.cluster import AgglomerativeClustering

    m = len(firmas)
    if m == 1:
        return np.zeros(1, dtype=int), np.zeros((1, 1))
    d = distancias(firmas)
    if mismo_lugar:  # circuitos que salen y vuelven al mismo lugar: se compara en ambos sentidos
        d = np.minimum(d, distancias(firmas, firmas[:, ::-1]))
        d = np.minimum(d, d.T)
    etiquetas = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                        distance_threshold=RUTA_DIST_M).fit_predict(d)
    return etiquetas, d


def _orientar(v):
    """Orienta el viaje desde el lugar de clave menor, para comparar ida y vuelta juntas."""
    if v.lugar_origen > v.lugar_destino:
        v.invertido = True
        v.tramos = [list(reversed(t)) for t in reversed(v.tramos)]
        v.lugar_origen, v.lugar_destino = v.lugar_destino, v.lugar_origen
    return v


def _route_id(lugar_a, lugar_b, medoide):
    base = f"{lugar_a}|{lugar_b}|{medoide.asset_id}|{medoide.inicio.isoformat()}"
    return "R-" + hashlib.sha1(base.encode()).hexdigest()[:6].upper()


def clasificar_niveles(conteos):
    """Umbrales por percentiles sobre la cantidad de recorridos de cada ruta."""
    valores = np.array(conteos, dtype=float)
    if len(valores) == 0:
        return {}, {"percentil_33": None, "percentil_67": None}
    p33, p67 = np.percentile(valores, [100 / 3, 200 / 3])
    if valores.max() == valores.min():
        niveles = ["MEDIUM"] * len(valores)
    else:
        niveles = ["HIGH" if c > p67 else "LOW" if c <= p33 else "MEDIUM" for c in valores]
        if "MEDIUM" not in niveles and p33 == p67:
            # Empate en el corte (p. ej. muchas rutas con el mismo conteo): el empate queda
            # como uso medio, así no desaparece el nivel intermedio.
            niveles = ["HIGH" if c > p67 else "MEDIUM" if c == p67 else "LOW" for c in valores]
    return niveles, {"percentil_33": float(p33), "percentil_67": float(p67),
                     "regla": "HIGH > p67, LOW <= p33, MEDIUM entre ambos (recorridos por ruta)"}


def nombres_unicos(lugares):
    """'Calama', 'Calama 2'... cuando varios lugares caen en la misma comuna."""
    vistos = Counter()
    for lugar in sorted(lugares.values(), key=lambda l: (-l["extremos"], l["clave"])):
        if lugar["tipo"] == "faena" or not lugar.get("comuna"):
            continue
        vistos[lugar["comuna"]] += 1
        n = vistos[lugar["comuna"]]
        lugar["nombre"] = lugar["comuna"] if n == 1 else f"{lugar['comuna']} {n}"


def construir_rutas(viajes, faenas, nombrar=None):
    """nombrar(lugares): opcional, agrega lugar['comuna'] a partir de los datos GPS."""
    """viajes: ViajeRuta con origen y destino conocidos. Devuelve (rutas, lugares, métricas)."""
    if not viajes:
        return [], {}, {"viajes_clasificados": 0, "rutas": 0}
    lugares = asignar_lugares(viajes, faenas)
    if nombrar:
        nombrar(lugares)
        nombres_unicos(lugares)
    lat_ref = float(np.mean([v.origen[0] for v in viajes]))
    grupos = defaultdict(list)
    for v in viajes:
        _orientar(v)
        grupos[(v.lugar_origen, v.lugar_destino)].append(v)

    rutas = []
    rng = np.random.default_rng(42)
    for (a, b), miembros in sorted(grupos.items()):
        firmas_todas = np.stack([firma(v.tramos, lat_ref) for v in miembros])
        indices = np.arange(len(miembros))
        if len(miembros) > MAX_GRUPO:  # pares muy frecuentes: se agrupa una muestra y se asigna el resto
            indices = np.sort(rng.choice(len(miembros), MAX_GRUPO, replace=False))
        etiquetas, d = _agrupar(firmas_todas[indices], a == b)
        clusters = defaultdict(list)
        for pos, etiqueta in enumerate(etiquetas):
            clusters[int(etiqueta)].append(pos)
        medoides = {}
        for etiqueta, posiciones in clusters.items():
            sub = d[np.ix_(posiciones, posiciones)]
            orden = np.argsort(sub.sum(axis=1))
            limpios = [posiciones[k] for k in orden if not miembros[indices[posiciones[k]]].con_saltos]
            medoides[etiqueta] = limpios[0] if limpios else posiciones[orden[0]]
        asignacion = {int(indices[pos]): int(etiquetas[pos]) for pos in range(len(indices))}
        if len(indices) < len(miembros):
            claves = list(medoides)
            centros = firmas_todas[[indices[medoides[c]] for c in claves]]
            resto = [i for i in range(len(miembros)) if i not in asignacion]
            dist = distancias(firmas_todas[resto], centros)
            siguiente = max(claves) + 1
            for fila, i in enumerate(resto):
                mejor = int(np.argmin(dist[fila]))
                if dist[fila, mejor] <= RUTA_DIST_M:
                    asignacion[i] = claves[mejor]
                else:
                    asignacion[i] = siguiente
                    medoides[siguiente] = None
                    siguiente += 1
        por_ruta = defaultdict(list)
        for i, etiqueta in asignacion.items():
            por_ruta[etiqueta].append(miembros[i])
        for etiqueta, lista in por_ruta.items():
            pos = medoides.get(etiqueta)
            medoide = miembros[indices[pos]] if pos is not None else lista[0]
            tramos = medoide.tramos
            fuente = "viaje_real_medoide"
            if len(tramos) > 1:  # medoide con saltos de GPS: se muestra solo su tramo continuo más largo
                tramos = [max(tramos, key=len)]
                fuente = "viaje_real_parcial"
            rutas.append({
                "route_id": _route_id(a, b, medoide),
                "origen": a, "destino": b,
                "label": f"{lugares[a]['nombre']} ↔ {lugares[b]['nombre']}" if a != b
                         else f"Circuito en {lugares[a]['nombre']}",
                "usage_count": len(lista),
                "unique_vehicle_count": len({v.asset_id for v in lista}),
                "observed_points": sum(v.puntos for v in lista),
                "km_mediana": round(float(np.median([v.km for v in lista])), 2),
                "primer_viaje": min(v.inicio for v in lista),
                "ultimo_viaje": max(v.fin for v in lista),
                "vehiculos": sorted(Counter(v.asset_id for v in lista).items(), key=lambda x: -x[1])[:20],
                "geometry": {"type": "LineString",
                             "coordinates": [[round(lon, 6), round(lat, 6)] for lat, lon in tramos[0]]},
                "geometry_source": fuente,
                "miembros": lista,
            })
    # Un camino recorrido pocas veces o por un solo camión no es una ruta de la operación sino
    # un viaje esporádico (desvío, traslado puntual): no se muestra ni entra en los niveles.
    esporadicas = [r for r in rutas if r["usage_count"] < MIN_RECORRIDOS or r["unique_vehicle_count"] < MIN_VEHICULOS]
    rutas = [r for r in rutas if r["usage_count"] >= MIN_RECORRIDOS and r["unique_vehicle_count"] >= MIN_VEHICULOS]
    niveles, umbrales = clasificar_niveles([r["usage_count"] for r in rutas])
    for ruta, nivel in zip(rutas, niveles):
        ruta["usage_level"] = nivel
        ruta["usage_label"], ruta["color"] = NIVELES[nivel]
    rutas.sort(key=lambda r: (-r["usage_count"], r["route_id"]))
    metricas = {
        "viajes_clasificados": sum(r["usage_count"] for r in rutas),
        "viajes_analizados": len(viajes),
        "rutas": len(rutas),
        "pares_origen_destino": len(grupos),
        "lugares": len(lugares),
        "por_nivel": dict(Counter(r["usage_level"] for r in rutas)),
        "caminos_esporadicos": len(esporadicas),
        "viajes_esporadicos": sum(r["usage_count"] for r in esporadicas),
        "umbrales": umbrales,
        "parametros": {"lugar_radio_m": LUGAR_RADIO_M, "lugar_min_viajes": LUGAR_MIN_VIAJES,
                       "min_recorridos": MIN_RECORRIDOS, "min_vehiculos": MIN_VEHICULOS,
                       "ruta_distancia_m": RUTA_DIST_M, "muestras": MUESTRAS},
    }
    return rutas, lugares, metricas
