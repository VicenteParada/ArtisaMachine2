"""Utilidades HTTP compartidas por los servicios Artisa."""

from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import signal


def serialize(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat() + ("Z" if isinstance(value, datetime) else "")
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value).__name__)


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class JsonHandler(BaseHTTPRequestHandler):
    # Los chequeos de salud de Docker (cada 5 s) no se registran si responden bien;
    # así los registros muestran solo lo útil. Los errores siempre se registran.
    QUIET_PATHS = {"/health", "/"}

    def log_request(self, code="-", size="-"):
        path = getattr(self, "path", "").split("?", 1)[0]
        if path in self.QUIET_PATHS and isinstance(code, int) and 200 <= code < 300:
            return
        super().log_request(code, size)

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=serialize, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(400, "Transfer-Encoding no soportado")
        if self.headers.get_content_type() != "application/json":
            raise RequestError(415, "Usa Content-Type: application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError
            if length > 4096:
                raise RequestError(413, "El cuerpo supera 4096 bytes")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (ValueError, UnicodeError):
            raise RequestError(400, "Envia un objeto JSON valido") from None
        except TimeoutError:
            raise RequestError(408, "Tiempo de espera agotado") from None


def serve(handler, name, port=8000):
    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    with ThreadingHTTPServer(("0.0.0.0", port), handler) as server:
        print(f"{name} escuchando en {port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

