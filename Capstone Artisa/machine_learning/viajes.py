"""Viajes de cada camión: recorridos observados entre dos paradas.

Una parada es un lugar donde el camión permanece al menos PARADA_MIN_MIN minutos dentro
de un radio de PARADA_RADIO_M metros (carga, descarga, base, espera). Un viaje es lo
que ocurre entre dos paradas consecutivas. Reglas para no inventar recorrido:

- Solo se unen reportes seguidos con pausa <= MAX_GAP_MIN y velocidad implícita
  <= VEL_MAX_KMH (las mismas reglas del cuaderno). Un salto de GPS no se dibuja: la
  geometría del viaje queda en tramos y el viaje se marca con_saltos.
- Una pausa sin reportes mayor a GAP_VIAJE_MIN corta el viaje: su destino queda
  desconocido (y el siguiente empieza sin origen conocido).
- El corte del día (00:00 hora Chile) puede partir un viaje en dos: se marcan como
  abierto_fin / abierto_inicio y la clasificación los vuelve a empalmar.

Las geometrías son puntos GPS reales simplificados (Douglas-Peucker, tolerancia
SIMPLIFICACION_M): no se agregan puntos que el camión no reportó.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math

PARADA_RADIO_M = 200
PARADA_MIN_MIN = 10
MAX_GAP_MIN = 5
VEL_MAX_KMH = 150
GAP_VIAJE_MIN = 30
VIAJE_MIN_KM = 1.0
VIAJE_MIN_PUNTOS = 5
SIMPLIFICACION_M = 20
METROS_POR_GRADO = 111_000


def distancia_m(lat0, lon0, lat1, lon1):
    return math.hypot(lat1 - lat0, (lon1 - lon0) * math.cos(math.radians((lat0 + lat1) / 2))) * METROS_POR_GRADO


def unible(p0, p1):
    """¿Se puede trazar una línea entre dos reportes seguidos sin inventar recorrido?"""
    horas = (p1[0] - p0[0]).total_seconds() / 3600
    if not 0 < horas <= MAX_GAP_MIN / 60:
        return False
    return distancia_m(p0[1], p0[2], p1[1], p1[2]) / 1000 <= VEL_MAX_KMH * horas


@dataclass
class Viaje:
    asset_id: str
    inicio: datetime
    fin: datetime
    origen: tuple            # (lat, lon) de la parada de origen, o None si se desconoce
    destino: tuple           # (lat, lon) de la parada de destino, o None
    tramos: list             # [[(lat, lon), ...], ...] puntos reales, sin unir saltos
    puntos: int
    km: float
    con_saltos: bool
    abierto_inicio: bool = False
    abierto_fin: bool = False
    extra: dict = field(default_factory=dict)


def detectar_paradas(puntos):
    """Índices [(i, j)] de los tramos donde el camión estuvo detenido (puntos i..j)."""
    paradas, i, n = [], 0, len(puntos)
    while i < n:
        j = i + 1
        while j < n and (puntos[j][0] - puntos[j - 1][0]).total_seconds() <= GAP_VIAJE_MIN * 60 \
                and distancia_m(puntos[i][1], puntos[i][2], puntos[j][1], puntos[j][2]) <= PARADA_RADIO_M:
            j += 1
        if (puntos[j - 1][0] - puntos[i][0]).total_seconds() >= PARADA_MIN_MIN * 60:
            paradas.append((i, j - 1))
            i = j
        else:
            i += 1
    return paradas


def _centro(puntos, i, j):
    sub = puntos[i:j + 1]
    return (sum(p[1] for p in sub) / len(sub), sum(p[2] for p in sub) / len(sub))


def _simplificar(tramo, tolerancia=SIMPLIFICACION_M):
    """Douglas-Peucker iterativo sobre puntos reales (lat, lon)."""
    if len(tramo) <= 2:
        return list(tramo)
    lat0 = math.radians(sum(p[0] for p in tramo) / len(tramo))
    xy = [(p[1] * METROS_POR_GRADO * math.cos(lat0), p[0] * METROS_POR_GRADO) for p in tramo]
    conservar = [False] * len(tramo)
    conservar[0] = conservar[-1] = True
    pila = [(0, len(tramo) - 1)]
    while pila:
        a, b = pila.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        largo = math.hypot(dx, dy) or 1e-9
        peor, indice = -1.0, None
        for k in range(a + 1, b):
            px, py = xy[k]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / largo
            if d > peor:
                peor, indice = d, k
        if indice is not None and peor > tolerancia:
            conservar[indice] = True
            pila.append((a, indice))
            pila.append((indice, b))
    return [p for p, keep in zip(tramo, conservar) if keep]


def _armar_viaje(asset_id, puntos, i, j, origen, destino, abierto_inicio, abierto_fin):
    tramos, actual, km, saltos = [], [(puntos[i][1], puntos[i][2])], 0.0, False
    for k in range(i + 1, j + 1):
        if unible(puntos[k - 1], puntos[k]):
            km += distancia_m(puntos[k - 1][1], puntos[k - 1][2], puntos[k][1], puntos[k][2]) / 1000
            actual.append((puntos[k][1], puntos[k][2]))
        else:
            saltos = True
            if len(actual) >= 2:
                tramos.append(actual)
            actual = [(puntos[k][1], puntos[k][2])]
    if len(actual) >= 2:
        tramos.append(actual)
    return Viaje(asset_id=asset_id, inicio=puntos[i][0], fin=puntos[j][0], origen=origen, destino=destino,
                 tramos=[_simplificar(t) for t in tramos], puntos=j - i + 1, km=round(km, 3),
                 con_saltos=saltos, abierto_inicio=abierto_inicio, abierto_fin=abierto_fin)


def extraer_viajes(asset_id, puntos, inicio_ventana=None, fin_ventana=None):
    """puntos: [(fecha_utc, lat, lon), ...] ordenados y limpios de un camión.
    inicio_ventana / fin_ventana: límites del día procesado, para marcar viajes cortados."""
    if len(puntos) < 2:
        return []
    paradas = detectar_paradas(puntos)
    # Segmentos de movimiento: entre paradas, y cortados por pausas largas sin reportes.
    limites = []  # (i, j, origen, destino, abierto_inicio, abierto_fin)
    anterior_fin, origen = 0, None
    if paradas and paradas[0][0] == 0:
        origen = _centro(puntos, *paradas[0])
        anterior_fin = paradas[0][1]
        paradas = paradas[1:]
    else:
        origen = None
    marcas = [(i, j) for i, j in paradas] + [(len(puntos), len(puntos))]
    for i_parada, j_parada in marcas:
        destino = _centro(puntos, i_parada, j_parada) if i_parada < len(puntos) else None
        fin_mov = i_parada if i_parada < len(puntos) else len(puntos) - 1
        a = anterior_fin
        # cortar por pausas largas dentro del movimiento
        corte = a
        for k in range(a + 1, fin_mov + 1):
            if (puntos[k][0] - puntos[k - 1][0]).total_seconds() > GAP_VIAJE_MIN * 60:
                limites.append((corte, k - 1, origen, None, origen is None, True))
                corte, origen = k, None
        limites.append((corte, fin_mov, origen, destino, origen is None, destino is None))
        if i_parada < len(puntos):
            origen = destino
            anterior_fin = j_parada
    viajes = []
    for i, j, ori, des, sin_origen, sin_destino in limites:
        if j - i + 1 < 2:
            continue
        cerca_inicio = inicio_ventana is not None and (puntos[i][0] - inicio_ventana) <= timedelta(minutes=GAP_VIAJE_MIN)
        cerca_fin = fin_ventana is not None and (fin_ventana - puntos[j][0]) <= timedelta(minutes=GAP_VIAJE_MIN)
        viaje = _armar_viaje(asset_id, puntos, i, j, ori, des,
                             abierto_inicio=sin_origen and cerca_inicio,
                             abierto_fin=sin_destino and cerca_fin)
        # Los fragmentos en el borde del día se guardan aunque sean cortos: se empalman después.
        if viaje.abierto_inicio or viaje.abierto_fin or (
                viaje.km >= VIAJE_MIN_KM and viaje.puntos >= VIAJE_MIN_PUNTOS):
            viajes.append(viaje)
    return viajes


def empalmar(viajes):
    """Une los fragmentos de un mismo viaje partido por el corte de medianoche.
    `viajes` debe venir ordenado por (asset_id, inicio)."""
    resultado = []
    for viaje in viajes:
        previo = resultado[-1] if resultado else None
        if (previo and previo.asset_id == viaje.asset_id and previo.abierto_fin and viaje.abierto_inicio
                and timedelta(0) <= viaje.inicio - previo.fin <= timedelta(minutes=GAP_VIAJE_MIN)):
            union = (previo.tramos[-1][-1], viaje.tramos[0][0]) if previo.tramos and viaje.tramos else None
            continua = union is not None and unible((previo.fin, *union[0]), (viaje.inicio, *union[1]))
            tramos = list(previo.tramos)
            km_union = distancia_m(*union[0], *union[1]) / 1000 if continua else 0.0
            if continua:
                tramos[-1] = tramos[-1] + viaje.tramos[0]
                tramos += viaje.tramos[1:]
            else:
                tramos += viaje.tramos
            resultado[-1] = Viaje(
                asset_id=viaje.asset_id, inicio=previo.inicio, fin=viaje.fin, origen=previo.origen,
                destino=viaje.destino, tramos=tramos, puntos=previo.puntos + viaje.puntos,
                km=round(previo.km + viaje.km + km_union, 3), con_saltos=previo.con_saltos or viaje.con_saltos or not continua,
                abierto_inicio=previo.abierto_inicio, abierto_fin=viaje.abierto_fin)
        else:
            resultado.append(viaje)
    return resultado
