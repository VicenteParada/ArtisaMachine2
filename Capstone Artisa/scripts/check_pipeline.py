"""Prueba el recorrido Front -> Endpoints -> DB -> Machine -> DB -> Endpoints -> Front."""

import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE = "http://127.0.0.1:8080"


def request(path, data=None, status=200):
    body = None if data is None else json.dumps(data).encode()
    req = Request(BASE+path, data=body, headers={"Content-Type": "application/json"})
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        assert response.code == status, f"{path}: esperado {status}, recibido {response.code}"
        return json.load(response)


def predict(distance):
    pending = request("/api/predicciones", {"distancia_km": distance, "carga_kg": 8000, "paradas": 2}, 202)
    assert pending["estado"] == "pendiente"
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        result = request(pending["consulta"])
        if result["estado"] == "completada":
            assert result["duracion_estimada"] > 0
            assert result["dataset_version"]
            assert result["completada_en"]
            assert result == request(pending["consulta"]), "El resultado persistido cambio inesperadamente"
            return result
        time.sleep(0.5)
    raise AssertionError("Machine no proceso la solicitud en 30 segundos")


def main():
    with urlopen(BASE, timeout=10) as response:
        assert response.headers.get_content_type() == "text/html"
        assert 'Simula un viaje' in response.read().decode("utf-8")
    for asset in ("/app.js", "/style.css"):
        with urlopen(BASE+asset, timeout=10) as response:
            assert response.code == 200
    state = request("/health")
    assert state["componentes"] == {key: "ok" for key in ("etl", "mariadb", "machine", "endpoints", "front")}
    summary = request("/api/resumen")
    rows = request("/api/viajes?limite=500")["viajes"]
    assert summary["viajes"] > 0
    assert summary["viajes"] == state["etl"]["validos"] == summary["predicciones"]
    assert len(rows) == len({row["id"] for row in rows})
    assert all(row["duracion_estimada"] > 0 for row in rows)
    assert all(row["dataset_version"] == state["etl"]["dataset_version"] for row in rows)
    model = request("/api/modelo")
    assert model["dataset_version"] == state["etl"]["dataset_version"]
    assert model["metricas"]["filas_evaluacion"] > 0
    assert model["metricas"]["mae_horas"] >= 0
    request("/api/viajes?limite=incorrecto", status=400)
    request("/api/ruta-inexistente", status=404)
    valid = {"distancia_km": 100, "carga_kg": 8000, "paradas": 2}
    for invalid in ({}, {**valid, "distancia_km": -2}, {**valid, "carga_kg": True},
                    {**valid, "distancia_km": "NaN"}, {**valid, "paradas": 0.5}):
        request("/api/predicciones", invalid, 400)
    short, long = predict(100), predict(600)
    assert long["duracion_estimada"] > short["duracion_estimada"], "La distancia no influye en la prediccion de demostracion"
    print(f"OK: cinco servicios disponibles; {summary['viajes']} viajes y {summary['predicciones']} predicciones en MariaDB.")
    print(f"OK: ETL descarto {state['etl']['rechazados']} filas; modelo entrenado y evaluado con datos separados.")
    print("OK: solicitud creada por endpoints, procesada por Machine y resultado persistido consultado desde el frontend.")
    print("OK: validacion de entradas, archivos del frontend y respuestas HTTP.")


if __name__ == "__main__":
    main()
