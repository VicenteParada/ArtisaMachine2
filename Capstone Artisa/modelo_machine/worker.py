"""MariaDB -> entrenamiento/predicción -> MariaDB; no forma parte de la API."""

from hashlib import sha256
import json
import threading
import time
from urllib.parse import urlsplit

from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split

from common.db import connect_db
from common.http import JsonHandler, serve
from common.validation import FEATURES
from modelo_machine.telemetry import train_hotspots


STATE = {"last_success": 0.0, "error": None, "version": None}
TELEMETRY_MODEL_INTERVAL_SECONDS = 300
TELEMETRY_MODEL_MAX_POINTS = 20000


def train(rows):
    if len(rows) < 10:
        raise ValueError("Se necesitan al menos 10 viajes validos para entrenar")
    x = [[row[key] for key in FEATURES] for row in rows]
    y = [row["duracion_horas"] for row in rows]
    x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=0.2, random_state=42)
    evaluation = LinearRegression().fit(x_train, y_train)
    metrics = {
        "mae_horas": round(float(mean_absolute_error(y_test, evaluation.predict(x_test))), 4),
        "mae_base_horas": round(float(mean_absolute_error(y_test, [sum(y_train)/len(y_train)]*len(y_test))), 4),
        "filas_entrenamiento": len(x_train), "filas_evaluacion": len(x_test),
        "filas_modelo_final": len(rows), "variables": list(FEATURES),
        "evaluacion": "80% entrenamiento / 20% evaluacion; semilla 42",
        "alcance": "Demostracion con datos sinteticos; no validado para viajes reales",
    }
    model = LinearRegression().fit(x, y)
    predictions = [max(0.01, float(value)) for value in model.predict(x)]
    return model, metrics, predictions


def cycle(model, version):
    # El snapshot consistente evita entrenar sobre dos cargas del ETL mezcladas.
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT dataset_version FROM etl_estado WHERE id=1")
        state = cursor.fetchone()
        if not state:
            raise ValueError("Esperando la primera carga ETL")
        current = state["dataset_version"]
        if model is None or version != current:
            cursor.execute("SELECT * FROM viajes WHERE dataset_version=%s ORDER BY id", (current,))
            rows = cursor.fetchall()
        else:
            rows = None
        connection.commit()
    if rows is not None:
        candidate, metrics, predictions = train(rows)
        with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT dataset_version FROM etl_estado WHERE id=1 FOR UPDATE")
            if cursor.fetchone()["dataset_version"] != current:
                connection.rollback()
                return model, version
            cursor.executemany(
                """INSERT INTO predicciones_viajes (viaje_id,dataset_version,duracion_estimada,calculada_en)
                VALUES (%s,%s,%s,UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE
                dataset_version=VALUES(dataset_version),duracion_estimada=VALUES(duracion_estimada),
                calculada_en=VALUES(calculada_en)""",
                [(row["id"], current, predicted) for row, predicted in zip(rows, predictions)],
            )
            cursor.execute(
                """INSERT INTO modelo_estado
                (id,dataset_version,algoritmo,metricas_json,coeficientes_json,intercepto,entrenado_en,ultima_revision)
                VALUES (1,%s,'Regresion lineal',%s,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))
                ON DUPLICATE KEY UPDATE dataset_version=VALUES(dataset_version),algoritmo=VALUES(algoritmo),
                metricas_json=VALUES(metricas_json),coeficientes_json=VALUES(coeficientes_json),
                intercepto=VALUES(intercepto),entrenado_en=VALUES(entrenado_en),ultima_revision=VALUES(ultima_revision)""",
                (current, json.dumps(metrics, ensure_ascii=False), json.dumps(candidate.coef_.tolist()), float(candidate.intercept_)),
            )
            connection.commit()
        model, version = candidate, current
        print(f"Machine: modelo entrenado con {len(rows)} viajes; MAE evaluacion {metrics['mae_horas']} h", flush=True)
    if model is None:
        raise ValueError("Modelo todavia no disponible")
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM solicitudes_prediccion WHERE estado='pendiente' ORDER BY creada_en LIMIT 25 FOR UPDATE")
        for request in cursor.fetchall():
            value = max(0.01, float(model.predict([[request[key] for key in FEATURES]])[0]))
            cursor.execute(
                """UPDATE solicitudes_prediccion SET estado='completada',duracion_estimada=%s,
                dataset_version=%s,completada_en=UTC_TIMESTAMP(6) WHERE id=%s""",
                (round(value, 4), version, request["id"]),
            )
        cursor.execute("UPDATE modelo_estado SET ultima_revision=UTC_TIMESTAMP(6) WHERE id=1")
        connection.commit()
    return model, version


def telemetry_cycle(previous_version):
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT ultimo_report_date,ultimo_report_id,validos FROM telemetria_etl_estado WHERE id=1")
        source = cursor.fetchone()
        if not source or int(source["validos"]) < 10:
            connection.commit()
            return previous_version
        fingerprint = f"{source['ultimo_report_date']}|{source['ultimo_report_id']}|{source['validos']}"
        current = sha256(fingerprint.encode("utf-8")).hexdigest()
        cursor.execute("SELECT dataset_version FROM modelo_telemetria_estado WHERE id=1")
        stored = cursor.fetchone()
        if previous_version == current or (stored and stored["dataset_version"] == current):
            connection.commit()
            return current
        cursor.execute(
            """SELECT asset_id,latitude,longitude,speed FROM telemetria_raw
            ORDER BY report_date DESC,report_id DESC LIMIT %s""",
            (TELEMETRY_MODEL_MAX_POINTS,),
        )
        rows = cursor.fetchall()
        connection.commit()

    metrics, zones = train_hotspots(rows)
    with connect_db(autocommit=False) as connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO modelo_telemetria_estado
            (id,dataset_version,algoritmo,metricas_json,zonas_json,entrenado_en,ultima_revision)
            VALUES (1,%s,'DBSCAN geoespacial',%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE dataset_version=VALUES(dataset_version),algoritmo=VALUES(algoritmo),
            metricas_json=VALUES(metricas_json),zonas_json=VALUES(zonas_json),
            entrenado_en=VALUES(entrenado_en),ultima_revision=VALUES(ultima_revision)""",
            (current, json.dumps(metrics, ensure_ascii=False), json.dumps(zones, ensure_ascii=False)),
        )
        connection.commit()
    print(
        f"Machine: telemetria entrenada con {metrics['reportes_entrenamiento']} posiciones; "
        f"{metrics['zonas_detectadas']} zonas frecuentes",
        flush=True,
    )
    return current

def loop():
    model, version = None, None
    telemetry_version = None
    next_telemetry_at = 0.0
    while True:
        try:
            model, version = cycle(model, version)
            STATE.update(last_success=time.monotonic(), error=None, version=version)
        except Exception as error:
            STATE["error"] = type(error).__name__
            print(f"Machine: ciclo fallido ({type(error).__name__}); se reintentara", flush=True)
        if time.monotonic() >= next_telemetry_at:
            try:
                telemetry_version = telemetry_cycle(telemetry_version)
                STATE["telemetry_version"] = telemetry_version
            except Exception as error:
                print(
                    f"Machine: entrenamiento de telemetria fallido ({type(error).__name__}); se reintentara",
                    flush=True,
                )
            finally:
                next_telemetry_at = time.monotonic() + TELEMETRY_MODEL_INTERVAL_SECONDS
        time.sleep(2)


class Handler(JsonHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        ready = (
            STATE["error"] is None
            and STATE["version"]
            and time.monotonic() - STATE["last_success"] < 120
        )
        self.respond(200 if ready else 503, {"servicio": "machine", "status": "ok" if ready else "unavailable"})


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    serve(Handler, "Machine Learning")
