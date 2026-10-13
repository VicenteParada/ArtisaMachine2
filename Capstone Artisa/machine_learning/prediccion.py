"""Predicción de actividad por zona para los próximos días.

Versión conectada a MariaDB del cuaderno guias_ml/prediccion_actividad.ipynb (LightGBM):
predice cuántos reportes de camiones en movimiento habrá en cada celda del mapa, día por
día, desde el día siguiente al último cierre hasta hoy + PRED_DIAS (por defecto 7).

Cambios respecto del cuaderno (detectados al auditarlo):
- Los datos salen de MariaDB (histórico + API en vivo), solo camiones del catálogo y por
  día de Chile (00:00 a 00:00), no de un CSV de abril en UTC.
- La grilla es fija (celdas de 0,01° desde el 0,0), no depende del mínimo de cada CSV:
  una celda es la misma zona en todos los entrenamientos.
- Se quita "dia_idx" (número de día desde el inicio del CSV): en la predicción siempre cae
  fuera del rango entrenado y el árbol la trata como constante. Se agregan "lag7" (mismo
  día de la semana anterior) y "media7" (última semana), con la misma regla sin fuga de
  datos: cada día solo ve días anteriores.
- En el cuaderno el early stopping se quedó en 1 árbol y el modelo no superó a la
  referencia ingenua (promedio histórico de la celda). Aquí compiten tres métodos en días
  de prueba que ninguno vio, y se publica el de menor error, diciendo cuál es:
    * lightgbm: el modelo (se prueban dos objetivos y la validación elige el mejor);
    * promedio_dia_semana: promedio del mismo día de la semana en las últimas 4 semanas;
    * promedio_historico: promedio de la celda en toda la ventana (referencia del cuaderno).
  Nunca se presenta como "modelo" una predicción peor que un promedio.

Dos modelos con la misma maquinaria (TIPOS):
- calor (cuaderno maching.ipynb): reportes de camiones en movimiento por celda de ~1 km.
- rutas (cuaderno maching_ruta.ipynb): camiones distintos por tramo de ruta. Un tramo une
  dos celdas vecinas que un camión cruzó entre dos reportes seguidos (5 min o menos y bajo
  150 km/h, igual que el cuaderno). En el cuaderno el tramo era el par de celdas de los dos
  reportes, que cambia según dónde cayó cada reporte (la misma calle quedaba repartida en
  miles de tramos distintos); aquí se trazan las celdas que cruza el segmento, así una calle
  es siempre la misma cadena de tramos. Igual que en el cuaderno, el entrenamiento se quedó
  en 1 árbol y no superó al promedio del tramo (MAE 0,59 contra 0,57).

Regla de camiones nuevos: la actividad cuenta solo camiones del catálogo. Cuando el
catálogo de camiones cambia (por ejemplo, la API registra un camión nuevo), se vuelven a
contar los días de la ventana con la lista nueva y se reentrena.
"""

from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import json
import math
import os
import time

import numpy as np

from common import dias

VENTANA_DIAS = int(os.environ.get("PRED_VENTANA_DIAS", "112"))  # 16 semanas de historia
DIAS_ADELANTE = int(os.environ.get("PRED_DIAS", "14"))          # hoy + 14: 15 días en total
MIN_DIAS = 21                       # como el cuaderno: al menos 3 semanas para entrenar
VEL_MOVIMIENTO = 2                  # km/h: igual que el cuaderno (speed > 2)
ESCALA = 100                        # celda de 0,01°: celda = floor(lat * 100)
CONSERVAR_VERSIONES = 3
MAX_GAP_MIN = 5                     # tramos: reportes seguidos a 5 min o menos (cuaderno)
VEL_MAX_KMH = 150                   # tramos: más rápido es un salto de GPS (cuaderno)
MAX_FILAS_ENTRENAMIENTO = 1_200_000 # se submuestrean zonas/tramos para no agotar memoria
# lag2 y tendencia (lag1 - lag2) vienen del cuaderno de rutas.
FEATURES = ["dia_semana", "lag1", "lag2", "tendencia", "lag7", "media7", "media_dia_semana",
            "media_historica"]
TIPOS = {
    "calor": {"tabla": "actividad_celda_dia", "estado": "actividad_dia_estado",
              "claves": ("celda_lat", "celda_lon"), "valor": "conteo", "salida": "prediccion_celda",
              "min_valor": 0.5, "max_por_dia": None, "min_dias_activos": 1},
    "rutas": {"tabla": "actividad_tramo_dia", "estado": "actividad_tramo_estado",
              "claves": ("a_lat", "a_lon", "b_lat", "b_lon"), "valor": "camiones", "salida": "prediccion_tramo",
              "min_valor": 0.3, "max_por_dia": 8000, "min_dias_activos": 3},
}
PARAMS = {"metric": "l1", "learning_rate": 0.05, "num_leaves": 15,
          "min_data_in_leaf": 30, "seed": 42, "verbose": -1, "num_threads": 1}  # 1 hilo: no acapara la CPU
# "regression_l1" minimiza directamente el error absoluto (el que se compara); "poisson"
# modela conteos. La validación elige cuál usar.
OBJETIVOS = ("regression_l1", "poisson")
SEMANAS_DIA = 4  # promedio del mismo día de la semana: últimas 4 semanas
# Días que se publican: hasta el último día cuyo error al predecir esa distancia hacia
# adelante no supera en más de 30 % al error del primer día (confianza alta o media).
# Siempre se publican al menos MIN_DIAS_PUBLICADOS (hoy + 6).
TOLERANCIA_ERROR = 1.3
MIN_DIAS_PUBLICADOS = 7
COLUMNA_PROMEDIO = {"promedio_historico": "media_historica", "promedio_dia_semana": "media_dia_semana"}


def celda(lat, lon):
    return math.floor(lat * ESCALA), math.floor(lon * ESCALA)


def centro(c):
    return round((c + 0.5) / ESCALA, 4)


def gps_valido(lat, lon):
    return (lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
            and not (lat == 0 and lon == 0))


# --- Camiones y conteo diario ------------------------------------------------------------

def camiones(cursor):
    cursor.execute(
        """SELECT v.asset_id FROM vehiculos AS v
           INNER JOIN tipos_vehiculo AS t ON t.id_tipo = v.id_tipo
           WHERE t.nombre = 'camion' ORDER BY v.asset_id"""
    )
    return [fila["asset_id"] for fila in cursor.fetchall()]


def huella(lista):
    return hashlib.sha1(",".join(sorted(lista)).encode()).hexdigest()[:16]


def contar_filas(filas):
    """Reportes en movimiento por celda, sin repetir (mismo camión y misma hora)."""
    vistos, conteo = set(), Counter()
    for fila in filas:
        lat, lon, vel = fila.get("latitude"), fila.get("longitude"), fila.get("speed")
        lat = None if lat is None else float(lat)
        lon = None if lon is None else float(lon)
        clave = (fila["asset_id"], fila["report_date"])
        if not gps_valido(lat, lon) or vel is None or float(vel) <= VEL_MOVIMIENTO or clave in vistos:
            continue
        vistos.add(clave)
        conteo[celda(lat, lon)] += 1
    return conteo


def distancia_km(lat0, lon0, lat1, lon1):
    p0, p1 = math.radians(lat0), math.radians(lat1)
    a = math.sin((p1 - p0) / 2) ** 2 + math.cos(p0) * math.cos(p1) * math.sin(math.radians(lon1 - lon0) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def trazar(lat0, lon0, lat1, lon1):
    """Celdas que cruza el segmento, en orden (vecinas, también en diagonal)."""
    pasos = int(max(abs(lat1 - lat0), abs(lon1 - lon0)) * ESCALA * 2) + 1
    celdas = []
    for k in range(pasos + 1):
        c = celda(lat0 + (lat1 - lat0) * k / pasos, lon0 + (lon1 - lon0) * k / pasos)
        if not celdas or celdas[-1] != c:
            celdas.append(c)
    return celdas


def contar_tramos(filas):
    """Camiones distintos por tramo (par de celdas vecinas, sin sentido) en el día."""
    por_camion = defaultdict(dict)
    for fila in filas:
        lat, lon, vel = fila.get("latitude"), fila.get("longitude"), fila.get("speed")
        lat = None if lat is None else float(lat)
        lon = None if lon is None else float(lon)
        if not gps_valido(lat, lon) or vel is None or float(vel) <= VEL_MOVIMIENTO:
            continue
        por_camion[fila["asset_id"]].setdefault(fila["report_date"], (lat, lon))
    conteo = Counter()
    for puntos in por_camion.values():
        orden = sorted(puntos.items())
        tramos = set()
        for (t0, (la0, lo0)), (t1, (la1, lo1)) in zip(orden, orden[1:]):
            minutos = (t1 - t0).total_seconds() / 60
            if not 0 < minutos <= MAX_GAP_MIN:
                continue
            if distancia_km(la0, lo0, la1, lo1) / (minutos / 60) > VEL_MAX_KMH:
                continue
            cadena = trazar(la0, lo0, la1, lo1)
            for a, b in zip(cadena, cadena[1:]):
                tramos.add(a + b if a < b else b + a)
        conteo.update(tramos)
    return conteo


CONTADORES = {"calor": contar_filas, "rutas": contar_tramos}


def leer_dia(cursor, dia, lista_camiones):
    """Reportes en movimiento del día local, con el índice (camión, fecha) como el cierre diario."""
    inicio, fin = dias.limites_utc(dia)
    filas = []
    for asset_id in lista_camiones:
        for tabla, indice in (("raw_gps", "idx_raw_gps_asset_fecha"),
                              ("telemetria_api", "idx_telemetria_api_asset_fecha")):
            cursor.execute(
                f"""SELECT asset_id, report_date, latitude, longitude, speed
                    FROM {tabla} USE INDEX ({indice})
                    WHERE asset_id = %s AND report_date >= %s AND report_date < %s AND speed > %s""",
                (asset_id, inicio, fin, VEL_MOVIMIENTO),
            )
            filas.extend(cursor.fetchall())
    return filas


def contar_dia(cursor, dia, lista_camiones):
    return contar_filas(leer_dia(cursor, dia, lista_camiones))


def actualizar_actividad(connection, desde, hasta, lista_camiones, recontar=(), tipos=tuple(TIPOS)):
    """Cuenta los días cerrados de la ventana que faltan o que el cierre diario recalculó.
    Cada día se lee una sola vez y se cuentan todos los tipos que lo necesiten.
    Devuelve ({tipo: días contados}, lista de días cerrados de la ventana)."""
    pendientes = defaultdict(set)
    calculado = {}
    lista_dias = []
    for tipo in tipos:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT e.fecha, e.calculado_en, a.calculado_en AS contado_en
                    FROM reporte_diario_estado AS e
                    LEFT JOIN {TIPOS[tipo]['estado']} AS a ON a.fecha = e.fecha
                    WHERE e.estado = 'cerrado' AND e.fecha >= %s AND e.fecha <= %s ORDER BY e.fecha""",
                (desde, hasta),
            )
            filas = cursor.fetchall()
        connection.commit()
        lista_dias = [f["fecha"] for f in filas]
        for f in filas:
            calculado[f["fecha"]] = f["calculado_en"]
            if tipo in recontar or f["contado_en"] is None or f["contado_en"] < f["calculado_en"]:
                pendientes[f["fecha"]].add(tipo)
    contados = Counter()
    for fecha in sorted(pendientes):
        with connection.cursor() as cursor:
            filas = leer_dia(cursor, fecha, lista_camiones)
            for tipo in sorted(pendientes[fecha]):
                cfg = TIPOS[tipo]
                conteo = CONTADORES[tipo](filas)
                cursor.execute(f"DELETE FROM {cfg['tabla']} WHERE fecha=%s", (fecha,))
                columnas = ",".join(cfg["claves"])
                marcas = ",".join(["%s"] * (len(cfg["claves"]) + 2))
                datos = [(fecha, *clave, n) for clave, n in conteo.items()]
                for k in range(0, len(datos), 5000):
                    cursor.executemany(
                        f"INSERT INTO {cfg['tabla']} (fecha,{columnas},{cfg['valor']}) VALUES ({marcas})",
                        datos[k:k + 5000],
                    )
                cursor.execute(
                    f"""INSERT INTO {cfg['estado']} (fecha,calculado_en,reportes) VALUES (%s,%s,%s)
                        ON DUPLICATE KEY UPDATE calculado_en=VALUES(calculado_en), reportes=VALUES(reportes)""",
                    (fecha, calculado[fecha], sum(conteo.values())),
                )
                contados[tipo] += 1
        connection.commit()
    return dict(contados), lista_dias


def cargar_matriz(cursor, lista_dias, tipo="calor"):
    """Matriz (zonas o tramos) x días de los días indicados."""
    if not lista_dias:
        return [], np.zeros((0, 0))
    cfg = TIPOS[tipo]
    cursor.execute(
        f"SELECT fecha, {','.join(cfg['claves'])}, {cfg['valor']} AS valor FROM {cfg['tabla']} "
        "WHERE fecha >= %s AND fecha <= %s",
        (lista_dias[0], lista_dias[-1]),
    )
    posicion = {d: k for k, d in enumerate(lista_dias)}
    celdas, indice, valores = [], {}, []
    for fila in cursor.fetchall():
        if fila["fecha"] not in posicion:
            continue
        clave = tuple(fila[c] for c in cfg["claves"])
        if clave not in indice:
            indice[clave] = len(celdas)
            celdas.append(clave)
        valores.append((indice[clave], posicion[fila["fecha"]], fila["valor"]))
    matriz = np.zeros((len(celdas), len(lista_dias)), dtype=np.float32)
    for i, k, n in valores:
        matriz[i, k] = n
    # Rutas: se descartan los tramos usados en muy pocos días (ruido, desvíos puntuales).
    activos = (matriz > 0).sum(axis=1) >= cfg["min_dias_activos"]
    if not activos.all():
        celdas = [c for c, ok in zip(celdas, activos) if ok]
        matriz = matriz[activos]
    return celdas, matriz


# --- Modelo ------------------------------------------------------------------------------

def features(matriz, d, dia_semana):
    """Variables del día d usando solo días anteriores (sin fuga de datos)."""
    previos = matriz[:, :d]
    mismos = [d - 7 * k for k in range(1, SEMANAS_DIA + 1) if d - 7 * k >= 0]
    return np.column_stack([
        np.full(matriz.shape[0], dia_semana, dtype=np.float32),
        matriz[:, d - 1],
        matriz[:, d - 2],
        matriz[:, d - 1] - matriz[:, d - 2],
        matriz[:, d - 7],
        matriz[:, d - 7:d].mean(axis=1),
        matriz[:, mismos].mean(axis=1),
        previos.mean(axis=1),
    ])


def tabla(matriz, lista_dias, indices, filas=None):
    """Filas (zona o tramo, día) para los días indicados; `filas` limita las zonas/tramos."""
    X = [features(matriz, d, lista_dias[d].weekday()) for d in indices]
    y = [matriz[:, d] for d in indices]
    if filas is not None:
        X = [x[filas] for x in X]
        y = [v[filas] for v in y]
    return np.vstack(X).astype(np.float32), np.concatenate(y)


def mae(pred, real):
    return float(np.abs(pred - real).mean()) if len(real) else float("nan")


def correlacion(pred, real):
    if len(real) < 2 or np.std(pred) == 0 or np.std(real) == 0:
        return None
    return round(float(np.corrcoef(pred, real)[0, 1]), 3)


def entrenar(matriz, lista_dias):
    """Días ordenados en el tiempo: 60 % entrenamiento, 20 % validación (early stopping y
    elección del objetivo) y 20 % prueba. En la prueba compiten LightGBM y los dos promedios;
    gana el de menor error absoluto medio. Devuelve (predictor, métricas)."""
    indices = list(range(7, len(lista_dias)))
    n = len(indices)
    corte_train, corte_val = indices[int(n * 0.6)], indices[int(n * 0.8)]
    train = [d for d in indices if d <= corte_train]
    val = [d for d in indices if corte_train < d <= corte_val]
    test = [d for d in indices if d > corte_val]
    X_test, y_test = tabla(matriz, lista_dias, test)
    candidatos = {m: X_test[:, FEATURES.index(c)] for m, c in COLUMNA_PROMEDIO.items()}
    metricas = {"dias_entrenamiento": len(train), "dias_validacion": len(val), "dias_prueba": len(test),
                "celdas": int(matriz.shape[0])}
    modelo = None
    try:
        import lightgbm as lgb
    except ImportError:
        metricas["nota"] = "LightGBM no está instalado"
        lgb = None
    if lgb is not None:
        # Con muchos tramos se entrena con una muestra fija de ellos (la prueba usa todos).
        muestra = None
        if matriz.shape[0] * (len(train) + len(val)) > MAX_FILAS_ENTRENAMIENTO:
            cantidad = max(1000, MAX_FILAS_ENTRENAMIENTO // (len(train) + len(val)))
            muestra = np.sort(np.random.default_rng(42).choice(matriz.shape[0], cantidad, replace=False))
            metricas["muestra_entrenamiento"] = int(cantidad)
        X_train, y_train = tabla(matriz, lista_dias, train, muestra)
        X_val, y_val = tabla(matriz, lista_dias, val, muestra)
        mejor = None
        for objetivo in OBJETIVOS:
            ds_train = lgb.Dataset(X_train, label=y_train, feature_name=FEATURES)
            ds_val = lgb.Dataset(X_val, label=y_val, reference=ds_train)
            prueba = lgb.train(dict(PARAMS, objective=objetivo), ds_train, num_boost_round=2000,
                               valid_sets=[ds_val], callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)])
            rondas = max(1, prueba.best_iteration or 1)
            error_val = mae(np.clip(prueba.predict(X_val, num_iteration=rondas), 0, None), y_val)
            if mejor is None or error_val < mejor[0]:
                mejor = (error_val, objetivo, rondas, prueba)
        error_val, objetivo, rondas, modelo = mejor
        candidatos["lightgbm"] = np.clip(modelo.predict(X_test, num_iteration=rondas), 0, None)
        metricas.update(objetivo=objetivo, rondas=rondas, mae_validacion=round(error_val, 3),
                        importancia=dict(zip(FEATURES, (int(v) for v in modelo.feature_importance()))))
    errores = {m: round(mae(p, y_test), 3) for m, p in candidatos.items()}
    # Gana el menor error; ante empate, el método más simple.
    simpleza = {"promedio_historico": 0, "promedio_dia_semana": 1, "lightgbm": 2}
    metodo = min(candidatos, key=lambda m: (errores[m], simpleza[m]))
    metricas.update(
        metodo=metodo, errores=errores,
        correlaciones={m: correlacion(p, y_test) for m, p in candidatos.items()},
        mae_publicado=errores[metodo], mae_referencia=errores["promedio_historico"],
    )
    if "lightgbm" in errores:
        metricas["mae_modelo"] = errores["lightgbm"]
    # Confianza por día: error real al predecir 1, 2, … días hacia adelante en la prueba.
    de_prueba = {"metodo": metodo, "modelo": modelo if metodo == "lightgbm" else None}
    metricas["error_por_dia"] = error_por_horizonte(de_prueba, matriz, lista_dias, test, DIAS_ADELANTE + 1)
    if metodo != "lightgbm":
        metricas["motivo"] = ("LightGBM no superó a los promedios en los días de prueba"
                              if "lightgbm" in errores else metricas.get("nota", ""))
        return {"metodo": metodo, "modelo": None}, metricas
    # Se reentrena con todos los días (incluida la prueba) y las rondas que eligió la validación.
    X_all, y_all = tabla(matriz, lista_dias, indices, muestra)
    final = lgb.train(dict(PARAMS, objective=metricas["objetivo"]),
                      lgb.Dataset(X_all, label=y_all, feature_name=FEATURES), num_boost_round=metricas["rondas"])
    return {"metodo": "lightgbm", "modelo": final}, metricas


def error_por_horizonte(predictor, matriz, lista_dias, dias_prueba, horizonte, max_origenes=7):
    """Error medio al predecir k días hacia adelante (k = 1..horizonte), simulando el
    pronóstico encadenado desde varios días de la prueba como si fueran "hoy"."""
    if not dias_prueba:
        return []
    paso = max(1, len(dias_prueba) // max_origenes)
    errores = [[] for _ in range(horizonte)]
    for origen in dias_prueba[::paso][:max_origenes]:
        largo = min(horizonte, matriz.shape[1] - origen)
        fechas = lista_dias[origen:origen + largo]
        pronostico = pronosticar(predictor, matriz[:, :origen], lista_dias[:origen], fechas)
        for k, fecha in enumerate(fechas):
            errores[k].append(mae(pronostico[fecha], matriz[:, origen + k]))
    return [round(float(np.mean(e)), 3) if e else None for e in errores]


def dias_confiables(error_por_dia, maximo):
    """Cuántos días publicar: los de menos fallos (ver TOLERANCIA_ERROR)."""
    errores = [e for e in (error_por_dia or [])[:maximo]]
    if not errores or errores[0] in (None, 0):
        return maximo
    dias_ok = 0
    for k, error in enumerate(errores, start=1):
        if error is None or error > errores[0] * TOLERANCIA_ERROR:
            break
        dias_ok = k
    return min(maximo, max(MIN_DIAS_PUBLICADOS, dias_ok))


def pronosticar(predictor, matriz, lista_dias, fechas_futuras):
    """Predice día por día; cada predicción pasa a ser la historia del día siguiente
    (igual que el cuaderno: la predicción de hoy es el lag1 de mañana)."""
    extendida = matriz
    resultado = {}
    for fecha in fechas_futuras:
        d = extendida.shape[1]
        X = features(extendida, d, fecha.weekday())
        if predictor["metodo"] == "lightgbm":
            pred = np.clip(predictor["modelo"].predict(X), 0, None)
        else:  # los promedios son columnas de las variables
            pred = X[:, FEATURES.index(COLUMNA_PROMEDIO[predictor["metodo"]])]
        resultado[fecha] = pred
        extendida = np.column_stack([extendida, pred.astype(np.float32)])
    return resultado


# --- Publicación -------------------------------------------------------------------------

def guardar_version(connection, base_hasta, celdas, pronostico, metricas, n_camiones, tipo="calor"):
    cfg = TIPOS[tipo]
    fechas = sorted(pronostico)
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO prediccion_version (creado_en,tipo,base_hasta,desde,hasta,camiones,activa,metricas_json)
               VALUES (UTC_TIMESTAMP(6),%s,%s,%s,%s,%s,0,%s)""",
            (tipo, base_hasta, fechas[0], fechas[-1], n_camiones,
             json.dumps(metricas, ensure_ascii=False, default=str)),
        )
        version = cursor.lastrowid
        filas = []
        for fecha in fechas:
            valores = pronostico[fecha]
            orden = np.argsort(-valores)
            if cfg["max_por_dia"]:
                orden = orden[:cfg["max_por_dia"]]
            filas.extend((version, fecha, *celdas[i], round(float(valores[i]), 2))
                         for i in orden if valores[i] >= cfg["min_valor"])
        columnas = ",".join(cfg["claves"])
        marcas = ",".join(["%s"] * (len(cfg["claves"]) + 3))
        for k in range(0, len(filas), 5000):
            cursor.executemany(
                f"INSERT INTO {cfg['salida']} (version_id,fecha,{columnas},valor) VALUES ({marcas})",
                filas[k:k + 5000],
            )
        cursor.execute("UPDATE prediccion_version SET activa = (id = %s) WHERE tipo = %s", (version, tipo))
        cursor.execute("SELECT id FROM prediccion_version WHERE tipo = %s ORDER BY id DESC "
                       "LIMIT 18446744073709551615 OFFSET %s", (tipo, CONSERVAR_VERSIONES))
        for vieja in [fila["id"] for fila in cursor.fetchall()]:
            cursor.execute(f"DELETE FROM {cfg['salida']} WHERE version_id=%s", (vieja,))
            cursor.execute("DELETE FROM prediccion_version WHERE id=%s", (vieja,))
    connection.commit()
    return version


def ultimo_cierre(cursor):
    cursor.execute("SELECT MAX(fecha) AS hasta FROM reporte_diario_estado WHERE estado='cerrado'")
    return (cursor.fetchone() or {}).get("hasta")


def version_activa(cursor, tipo="calor"):
    try:
        cursor.execute("SELECT * FROM prediccion_version WHERE activa=1 AND tipo=%s ORDER BY id DESC LIMIT 1",
                       (tipo,))
    except Exception:
        return None
    version = cursor.fetchone()
    if version:
        version["metricas"] = json.loads(version.pop("metricas_json") or "{}")
    return version


def necesita_calculo(cursor, utc_now):
    """Tipos que necesitan calcularse: hay un cierre nuevo, cambió el catálogo de camiones o
    el pronóstico ya no llega a hoy + DIAS_ADELANTE. Lista vacía = nada que hacer."""
    hasta = ultimo_cierre(cursor)
    if hasta is None:
        return []
    cursor.execute("SELECT COUNT(*) AS n FROM reporte_diario_estado WHERE estado='cerrado'")
    if (cursor.fetchone() or {}).get("n", 0) < MIN_DIAS:
        return []
    huella_actual = huella(camiones(cursor))
    tipos = []
    for tipo in TIPOS:
        version = version_activa(cursor, tipo)
        if (version is None or version["base_hasta"] < hasta
                or version["metricas"].get("huella_camiones") != huella_actual
                or version["hasta"] < dias.hoy(utc_now) + timedelta(days=DIAS_ADELANTE)):
            tipos.append(tipo)
    return tipos


def ejecutar(connection, utc_now, tipos=None):
    """Calcula y publica los tipos indicados (por defecto, los que lo necesitan)."""
    comienzo = time.monotonic()
    with connection.cursor() as cursor:
        base_hasta = ultimo_cierre(cursor)
        lista_camiones = camiones(cursor)
        tipos = list(tipos) if tipos is not None else (necesita_calculo(cursor, utc_now) or list(TIPOS))
        anteriores = {tipo: version_activa(cursor, tipo) for tipo in tipos}
    connection.commit()
    if base_hasta is None:
        return None
    desde = base_hasta - timedelta(days=VENTANA_DIAS - 1)
    # Regla de camiones nuevos: si cambió el catálogo, se recuentan todos los días de ese tipo.
    recontar = {t for t, v in anteriores.items() if v and v["metricas"].get("huella_camiones") != huella(lista_camiones)}
    contados, lista_dias = actualizar_actividad(connection, desde, base_hasta, lista_camiones, recontar, tipos)
    if len(lista_dias) < MIN_DIAS:
        print(f"Predicción: faltan días cerrados ({len(lista_dias)} de {MIN_DIAS})", flush=True)
        return None
    hoy = dias.hoy(utc_now)
    futuras = []
    fecha = base_hasta + timedelta(days=1)
    while fecha <= hoy + timedelta(days=DIAS_ADELANTE):
        futuras.append(fecha)
        fecha += timedelta(days=1)
    resultados = {}
    for tipo in tipos:
        inicio_tipo = time.monotonic()
        with connection.cursor() as cursor:
            celdas, matriz = cargar_matriz(cursor, lista_dias, tipo)
        connection.commit()
        if not celdas:
            continue
        predictor, metricas = entrenar(matriz, lista_dias)
        publicar = futuras[:dias_confiables(metricas.get("error_por_dia"), len(futuras))]
        metricas["dias_descartados"] = len(futuras) - len(publicar)
        pronostico = pronosticar(predictor, matriz, lista_dias, publicar)
        metricas.update(
            huella_camiones=huella(lista_camiones), camiones=len(lista_camiones), dias_historia=len(lista_dias),
            historia_desde=lista_dias[0], historia_hasta=lista_dias[-1], dias_recontados=contados.get(tipo, 0),
            segundos=round(time.monotonic() - inicio_tipo, 1),
        )
        version = guardar_version(connection, base_hasta, celdas, pronostico, metricas, len(lista_camiones), tipo)
        print(f"Predicción {tipo}: versión {version} · {publicar[0]}..{publicar[-1]} · método {metricas['metodo']} · "
              f"errores en prueba {metricas['errores']} en {metricas['segundos']} s", flush=True)
        resultados[tipo] = metricas
    print(f"Predicción: listo en {round(time.monotonic() - comienzo, 1)} s", flush=True)
    return resultados or None


if __name__ == "__main__":
    # Ejecución manual de los dos modelos: python3 scripts/artisa.py prediccion
    from datetime import datetime, timezone
    from common.db import connect_db, initialize_db
    from machine_learning.diario_worker import _con_bloqueo
    initialize_db()
    with connect_db(autocommit=False, read_timeout=900) as conexion:
        print("Esperando a que el servicio termine lo que está calculando (si hay)…", flush=True)
        resultado = _con_bloqueo(conexion, lambda: ejecutar(conexion, datetime.now(timezone.utc).replace(tzinfo=None),
                                                            tipos=list(TIPOS)), espera=3600)
    raise SystemExit(0 if resultado else 1)
