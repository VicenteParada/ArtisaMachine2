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

Regla de camiones nuevos: la actividad cuenta solo camiones del catálogo. Cuando el
catálogo de camiones cambia (por ejemplo, la API registra un camión nuevo), se vuelven a
contar los días de la ventana con la lista nueva y se reentrena.
"""

from collections import Counter
from datetime import timedelta
import hashlib
import json
import math
import os
import time

import numpy as np

from common import dias

VENTANA_DIAS = int(os.environ.get("PRED_VENTANA_DIAS", "56"))   # 8 semanas de historia
DIAS_ADELANTE = int(os.environ.get("PRED_DIAS", "7"))           # hoy + 7 días
MIN_DIAS = 21                       # como el cuaderno: al menos 3 semanas para entrenar
VEL_MOVIMIENTO = 2                  # km/h: igual que el cuaderno (speed > 2)
ESCALA = 100                        # celda de 0,01°: celda = floor(lat * 100)
MIN_VALOR = 0.5                     # no se guardan celdas con menos de medio reporte esperado
CONSERVAR_VERSIONES = 3
FEATURES = ["dia_semana", "lag1", "lag7", "media7", "media_dia_semana", "media_historica"]
PARAMS = {"metric": "l1", "learning_rate": 0.05, "num_leaves": 15,
          "min_data_in_leaf": 30, "seed": 42, "verbose": -1, "num_threads": 1}  # 1 hilo: no acapara la CPU
# "regression_l1" minimiza directamente el error absoluto (el que se compara); "poisson"
# modela conteos. La validación elige cuál usar.
OBJETIVOS = ("regression_l1", "poisson")
SEMANAS_DIA = 4  # promedio del mismo día de la semana: últimas 4 semanas
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


def contar_dia(cursor, dia, lista_camiones):
    """Lee el día local con el índice (camión, fecha), como el cierre diario."""
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
    return contar_filas(filas)


def actualizar_actividad(connection, desde, hasta, lista_camiones, recontar=False):
    """Cuenta los días cerrados de la ventana que faltan o que el cierre diario recalculó."""
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT e.fecha, e.calculado_en, a.calculado_en AS contado_en
               FROM reporte_diario_estado AS e
               LEFT JOIN actividad_dia_estado AS a ON a.fecha = e.fecha
               WHERE e.estado = 'cerrado' AND e.fecha >= %s AND e.fecha <= %s ORDER BY e.fecha""",
            (desde, hasta),
        )
        filas = cursor.fetchall()
    connection.commit()
    pendientes = [f for f in filas if recontar or f["contado_en"] is None or f["contado_en"] < f["calculado_en"]]
    for fila in pendientes:
        with connection.cursor() as cursor:
            conteo = contar_dia(cursor, fila["fecha"], lista_camiones)
            cursor.execute("DELETE FROM actividad_celda_dia WHERE fecha=%s", (fila["fecha"],))
            if conteo:
                cursor.executemany(
                    "INSERT INTO actividad_celda_dia (fecha,celda_lat,celda_lon,conteo) VALUES (%s,%s,%s,%s)",
                    [(fila["fecha"], ci, cj, n) for (ci, cj), n in conteo.items()],
                )
            cursor.execute(
                """INSERT INTO actividad_dia_estado (fecha,calculado_en,reportes) VALUES (%s,%s,%s)
                   ON DUPLICATE KEY UPDATE calculado_en=VALUES(calculado_en), reportes=VALUES(reportes)""",
                (fila["fecha"], fila["calculado_en"], sum(conteo.values())),
            )
        connection.commit()
    return len(pendientes), [f["fecha"] for f in filas]


def cargar_matriz(cursor, lista_dias):
    """Matriz celdas x días (conteos) de los días indicados."""
    if not lista_dias:
        return [], np.zeros((0, 0))
    cursor.execute(
        "SELECT fecha, celda_lat, celda_lon, conteo FROM actividad_celda_dia WHERE fecha >= %s AND fecha <= %s",
        (lista_dias[0], lista_dias[-1]),
    )
    posicion = {d: k for k, d in enumerate(lista_dias)}
    celdas, indice, valores = [], {}, []
    for fila in cursor.fetchall():
        if fila["fecha"] not in posicion:
            continue
        clave = (fila["celda_lat"], fila["celda_lon"])
        if clave not in indice:
            indice[clave] = len(celdas)
            celdas.append(clave)
        valores.append((indice[clave], posicion[fila["fecha"]], fila["conteo"]))
    matriz = np.zeros((len(celdas), len(lista_dias)), dtype=np.float32)
    for i, k, n in valores:
        matriz[i, k] = n
    return celdas, matriz


# --- Modelo ------------------------------------------------------------------------------

def features(matriz, d, dia_semana):
    """Variables del día d usando solo días anteriores (sin fuga de datos)."""
    previos = matriz[:, :d]
    mismos = [d - 7 * k for k in range(1, SEMANAS_DIA + 1) if d - 7 * k >= 0]
    return np.column_stack([
        np.full(matriz.shape[0], dia_semana, dtype=np.float32),
        matriz[:, d - 1],
        matriz[:, d - 7],
        matriz[:, d - 7:d].mean(axis=1),
        matriz[:, mismos].mean(axis=1),
        previos.mean(axis=1),
    ])


def tabla(matriz, lista_dias, indices):
    X = [features(matriz, d, lista_dias[d].weekday()) for d in indices]
    y = [matriz[:, d] for d in indices]
    return np.vstack(X), np.concatenate(y)


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
        X_train, y_train = tabla(matriz, lista_dias, train)
        X_val, y_val = tabla(matriz, lista_dias, val)
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
    if metodo != "lightgbm":
        metricas["motivo"] = ("LightGBM no superó a los promedios en los días de prueba"
                              if "lightgbm" in errores else metricas.get("nota", ""))
        return {"metodo": metodo, "modelo": None}, metricas
    # Se reentrena con todos los días (incluida la prueba) y las rondas que eligió la validación.
    X_all, y_all = tabla(matriz, lista_dias, indices)
    final = lgb.train(dict(PARAMS, objective=metricas["objetivo"]),
                      lgb.Dataset(X_all, label=y_all, feature_name=FEATURES), num_boost_round=metricas["rondas"])
    return {"metodo": "lightgbm", "modelo": final}, metricas


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

def guardar_version(connection, base_hasta, celdas, pronostico, metricas, n_camiones):
    fechas = sorted(pronostico)
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO prediccion_version (creado_en,base_hasta,desde,hasta,camiones,activa,metricas_json)
               VALUES (UTC_TIMESTAMP(6),%s,%s,%s,%s,0,%s)""",
            (base_hasta, fechas[0], fechas[-1], n_camiones, json.dumps(metricas, ensure_ascii=False, default=str)),
        )
        version = cursor.lastrowid
        filas = [(version, fecha, ci, cj, round(float(v), 2))
                 for fecha in fechas for (ci, cj), v in zip(celdas, pronostico[fecha]) if v >= MIN_VALOR]
        for k in range(0, len(filas), 5000):
            cursor.executemany(
                "INSERT INTO prediccion_celda (version_id,fecha,celda_lat,celda_lon,valor) VALUES (%s,%s,%s,%s,%s)",
                filas[k:k + 5000],
            )
        cursor.execute("UPDATE prediccion_version SET activa = (id = %s)", (version,))
        cursor.execute("SELECT id FROM prediccion_version ORDER BY id DESC LIMIT 18446744073709551615 OFFSET %s",
                       (CONSERVAR_VERSIONES,))
        for vieja in [fila["id"] for fila in cursor.fetchall()]:
            cursor.execute("DELETE FROM prediccion_celda WHERE version_id=%s", (vieja,))
            cursor.execute("DELETE FROM prediccion_version WHERE id=%s", (vieja,))
    connection.commit()
    return version


def ultimo_cierre(cursor):
    cursor.execute("SELECT MAX(fecha) AS hasta FROM reporte_diario_estado WHERE estado='cerrado'")
    return (cursor.fetchone() or {}).get("hasta")


def version_activa(cursor):
    try:
        cursor.execute("SELECT * FROM prediccion_version WHERE activa=1 ORDER BY id DESC LIMIT 1")
    except Exception:
        return None
    version = cursor.fetchone()
    if version:
        version["metricas"] = json.loads(version.pop("metricas_json") or "{}")
    return version


def necesita_calculo(cursor, utc_now):
    """Hay un cierre nuevo, cambió el catálogo de camiones o el pronóstico ya no llega a
    hoy + DIAS_ADELANTE."""
    hasta = ultimo_cierre(cursor)
    if hasta is None:
        return False
    version = version_activa(cursor)
    if version is None:
        cursor.execute("SELECT COUNT(*) AS n FROM reporte_diario_estado WHERE estado='cerrado'")
        return (cursor.fetchone() or {}).get("n", 0) >= MIN_DIAS
    return (version["base_hasta"] < hasta
            or version["metricas"].get("huella_camiones") != huella(camiones(cursor))
            or version["hasta"] < dias.hoy(utc_now) + timedelta(days=DIAS_ADELANTE))


def ejecutar(connection, utc_now):
    comienzo = time.monotonic()
    with connection.cursor() as cursor:
        base_hasta = ultimo_cierre(cursor)
        lista_camiones = camiones(cursor)
        anterior = version_activa(cursor)
    connection.commit()
    if base_hasta is None:
        return None
    desde = base_hasta - timedelta(days=VENTANA_DIAS - 1)
    # Regla de camiones nuevos: si cambió el catálogo, se recuentan todos los días.
    recontar = bool(anterior) and anterior["metricas"].get("huella_camiones") != huella(lista_camiones)
    contados, lista_dias = actualizar_actividad(connection, desde, base_hasta, lista_camiones, recontar)
    if len(lista_dias) < MIN_DIAS:
        print(f"Predicción: faltan días cerrados ({len(lista_dias)} de {MIN_DIAS})", flush=True)
        return None
    with connection.cursor() as cursor:
        celdas, matriz = cargar_matriz(cursor, lista_dias)
    connection.commit()
    if not celdas:
        return None
    predictor, metricas = entrenar(matriz, lista_dias)
    hoy = dias.hoy(utc_now)
    futuras = []
    fecha = base_hasta + timedelta(days=1)
    while fecha <= hoy + timedelta(days=DIAS_ADELANTE):
        futuras.append(fecha)
        fecha += timedelta(days=1)
    pronostico = pronosticar(predictor, matriz, lista_dias, futuras)
    metricas.update(
        huella_camiones=huella(lista_camiones), camiones=len(lista_camiones), dias_historia=len(lista_dias),
        historia_desde=lista_dias[0], historia_hasta=lista_dias[-1], dias_recontados=contados,
        segundos=round(time.monotonic() - comienzo, 1),
    )
    version = guardar_version(connection, base_hasta, celdas, pronostico, metricas, len(lista_camiones))
    print(f"Predicción: versión {version} · {futuras[0]}..{futuras[-1]} · método {metricas['metodo']} · "
          f"errores en prueba {metricas['errores']} en {metricas['segundos']} s", flush=True)
    return metricas


if __name__ == "__main__":
    # Ejecución manual: python3 scripts/artisa.py prediccion
    from datetime import datetime, timezone
    from common.db import connect_db, initialize_db
    from machine_learning.diario_worker import _con_bloqueo
    initialize_db()
    with connect_db(autocommit=False, read_timeout=900) as conexion:
        print("Esperando a que termine el trabajo en curso del cierre diario (si hay)…", flush=True)
        resultado = _con_bloqueo(conexion, lambda: ejecutar(conexion, datetime.now(timezone.utc).replace(tzinfo=None)),
                                 espera=3600)
    raise SystemExit(0 if resultado else 1)
