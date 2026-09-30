"""Frontend estático y proxy a endpoints: único puerto público."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


API = os.environ.get("API_URL", "http://endpoints:8000")
ROOT = Path(__file__).with_name("static")
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/map-data.js": ("map-data.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8"),
         "/artisa-logo.png": ("artisa-logo.png", "image/png"),
         "/artisa-fleet-cover.jpg": ("artisa-fleet-cover.jpg", "image/jpeg")}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self' https://unpkg.com 'unsafe-inline'; connect-src 'self'; img-src 'self' data: https://unpkg.com https://*.tile.openstreetmap.org; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def proxy(self):
        body = None
        try:
            if self.headers.get("Transfer-Encoding"):
                self.reply(400, b'{"error":"Transfer-Encoding no soportado"}')
                return
            if self.command == "POST":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if not 0 < length <= 4096:
                    self.reply(413 if length > 4096 else 400, b'{"error":"Tamano de cuerpo invalido"}')
                    return
                body = self.rfile.read(length)
            request = Request(API + self.path, data=body, method=self.command)
            if body is not None:
                request.add_header("Content-Type", self.headers.get("Content-Type", "application/octet-stream"))
            try:
                upstream = urlopen(request, timeout=20)
            except HTTPError as error:
                upstream = error
            with upstream:
                content = upstream.read()
                if urlsplit(self.path).path in ("/api/estado", "/health"):
                    payload = json.loads(content)
                    payload.setdefault("componentes", {})["front"] = "ok"
                    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.reply(upstream.code, content, upstream.headers.get("Content-Type", "application/json"))
        except (URLError, TimeoutError, ConnectionError):
            self.reply(503, b'{"status":"unavailable","error":"Los endpoints no estan disponibles"}')

    def route(self):
        path = urlsplit(self.path).path
        if self.command == "GET" and path == "/front-health":
            self.reply(200, b'{"status":"ok","servicio":"front"}')
        elif path.startswith("/api/") or path == "/health":
            self.proxy()
        elif self.command == "GET" and path in FILES:
            filename, content_type = FILES[path]
            self.reply(200, (ROOT / filename).read_bytes(), content_type)
        else:
            self.reply(404, b'{"error":"Ruta no encontrada"}')

    do_GET = route
    do_POST = route
    do_DELETE = route


if __name__ == "__main__":
    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    with ThreadingHTTPServer(("0.0.0.0", 8000), Handler) as server:
        print("Frontend escuchando en 8000", flush=True)
        server.serve_forever()
