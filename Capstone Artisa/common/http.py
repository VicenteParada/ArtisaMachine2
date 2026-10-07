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

