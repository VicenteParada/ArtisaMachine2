"""Comprueba HTTP y lectura/escritura real en MariaDB; solo necesita Python 3."""

import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
import check_pipeline


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("url", nargs="?", default="http://127.0.0.1:8080")
parser.add_argument("--persistence", action="store_true", help="Recrea MariaDB y verifica que el registro persista")
args = parser.parse_args()
base_url = args.url.rstrip("/")


def request(path, method="GET", data=None, expected=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = Request(f"{base_url}{path}", data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        response = urlopen(req, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        assert response.code == expected, f"{method} {path}: esperado {expected}, recibido {response.code}"
        assert response.headers.get_content_type() == "application/json", "La respuesta no es JSON"
        return json.load(response)


def wait_healthy():
    deadline = time.monotonic() + 60
    while True:
        try:
            with urlopen(f"{base_url}/health", timeout=8) as response:
                health = json.load(response)
            assert health["status"] == "ok"
            assert health["servicio"] == "artisa-docker-demo"
            assert health["database"] == {"engine": "MariaDB", "status": "ok"}
            return
        except (URLError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


wait_healthy()
check_pipeline.BASE = base_url
check_pipeline.main()
assert request("/ruta-inexistente", expected=404)["error"] == "Ruta no encontrada"
for invalid in ({}, {"texto": " "}, {"texto": 123}, {"texto": "x" * 256}, ["texto"]):
    request("/mensajes", "POST", invalid, expected=400)

text = f"Prueba MariaDB: áéíóú 👋 ' OR 1=1 -- {uuid.uuid4()}"
created = request("/mensajes", "POST", {"texto": text}, expected=201)
message_path = f"/mensajes/{created['id']}"
try:
    assert request(message_path) == {"id": created["id"], "texto": text}
    assert created in request("/mensajes")["mensajes"]
    if args.persistence:
        subprocess.run(
            ["docker", "compose", "up", "--detach", "--force-recreate", "--wait",
             "--wait-timeout", "120", "mariadb"],
            cwd=Path(__file__).resolve().parent.parent,
            check=True,
        )
        wait_healthy()
        assert request(message_path)["texto"] == text, "El registro no sobrevivio a la recreacion"
        print("OK: el registro persistio tras recrear el contenedor MariaDB.")
finally:
    request(message_path, "DELETE")
request(message_path, expected=404)

print("OK: API y MariaDB responden; escritura, lectura y eliminacion verificadas.")
print("OK: entradas invalidas rechazadas; Unicode y comillas conservados.")
print(f"Frontend: {base_url}")
