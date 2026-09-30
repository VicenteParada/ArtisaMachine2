"""Comprueba Artisa mediante GET; ARTISA_TEST_TOKEN habilita el diagnóstico protegido."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Evita enviar el JWT a un destino distinto mediante una redirección.
        return None


def request(base_url, path, token=None, html=False):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = Request(base_url + path, headers=headers, method="GET")
    try:
        with build_opener(NoRedirect).open(req, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError(f"GET {path}: HTTP {response.status}")
            expected = "text/html" if html else "application/json"
            if response.headers.get_content_type() != expected:
                raise RuntimeError(f"GET {path}: se esperaba {expected}")
            if html:
                return None
            payload = json.load(response)
            if not isinstance(payload, dict):
                raise RuntimeError(f"GET {path}: respuesta JSON no válida")
            return payload
    except HTTPError as error:
        raise RuntimeError(f"GET {path}: HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError(f"GET {path}: no se pudo obtener una respuesta válida") from None


def check_state(state, path):
    required = ("mariadb", "etl", "endpoints", "front")
    components = state.get("componentes")
    if (state.get("status") != "ok" or not isinstance(components, dict)
            or any(components.get(name) != "ok" for name in required)):
        raise RuntimeError(f"GET {path}: los servicios base no están operativos")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base_url = args.url.rstrip("/")
    parsed = urlsplit(base_url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        parser.error("Indica una URL HTTP(S) sin credenciales, consulta ni fragmento")
    try:
        request(base_url, "/", html=True)
        print("OK: frontend disponible.")
        check_state(request(base_url, "/health"), "/health")
        print("OK: servicios base operativos; Machine es opcional.")
        token = os.environ.get("ARTISA_TEST_TOKEN")
        if token:
            check_state(request(base_url, "/api/estado", token=token), "/api/estado")
            print("OK: diagnóstico autenticado disponible.")
        else:
            print("OMITIDA: /api/estado requiere ARTISA_TEST_TOKEN con un JWT vigente.")
        return 0
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
