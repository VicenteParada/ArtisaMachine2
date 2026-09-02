"""API local de prueba: Python y MariaDB en contenedores separados."""

import json
import os
from pathlib import Path
import re
import signal
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pymysql


SERVICE = "artisa-docker-demo"
STARTED_AT = time.monotonic()
MESSAGE_PATH = re.compile(r"/mensajes/([1-9][0-9]{0,18})")


def connect_db():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "mariadb"),
        user=os.environ.get("DB_USER", "artisa_app"),
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "artisa"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=3,
        read_timeout=3,
        write_timeout=3,
    )


def initialize_db():
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    for attempt in range(15):
        try:
            with connect_db() as connection, connection.cursor() as cursor:
                cursor.execute(schema)
            return
        except pymysql.MySQLError:
            if attempt == 14:
                raise
            time.sleep(2)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.respond(200, {
                "servicio": SERVICE,
                "mensaje": "El proyecto esta funcionando con MariaDB dentro de Docker.",
                "contenedor": socket.gethostname(),
                "health": "/health",
                "mensajes": "/mensajes",
            })
            return
        message_match = MESSAGE_PATH.fullmatch(path)
        if path not in ("/health", "/mensajes") and not message_match:
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        try:
            with connect_db() as connection, connection.cursor() as cursor:
                if path == "/health":
                    cursor.execute("SELECT id FROM mensajes LIMIT 1")
                    self.respond(200, {
                        "status": "ok",
                        "servicio": SERVICE,
                        "database": {"engine": "MariaDB", "status": "ok"},
                        "uptime_seconds": round(time.monotonic() - STARTED_AT, 2),
                    })
                elif path == "/mensajes":
                    cursor.execute("SELECT id, texto FROM mensajes ORDER BY id DESC LIMIT 100")
                    self.respond(200, {"mensajes": cursor.fetchall(), "limite": 100})
                else:
                    cursor.execute("SELECT id, texto FROM mensajes WHERE id = %s", (int(message_match[1]),))
                    message = cursor.fetchone()
                    self.respond(200 if message else 404, message or {"error": "Mensaje no encontrado"})
        except pymysql.MySQLError as error:
            self.database_error(error)

    def do_POST(self):
        if urlsplit(self.path).path != "/mensajes":
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.respond(400, {"error": "Transfer-Encoding no soportado"})
            return
        if self.headers.get_content_type() != "application/json":
            self.respond(415, {"error": "Usa Content-Type: application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError
            if length > 4096:
                self.respond(413, {"error": "El cuerpo supera 4096 bytes"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = payload.get("texto") if isinstance(payload, dict) else None
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 255:
                raise ValueError
            text = text.strip()
        except (ValueError, UnicodeError):
            self.respond(400, {"error": "Envia un objeto JSON con texto de 1 a 255 caracteres"})
            return
        except TimeoutError:
            self.respond(408, {"error": "Tiempo de espera agotado al recibir el cuerpo"})
            return
        try:
            with connect_db() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO mensajes (texto) VALUES (%s)", (text,))
                self.respond(201, {"id": cursor.lastrowid, "texto": text})
        except pymysql.MySQLError as error:
            self.database_error(error)

    def do_DELETE(self):
        match = MESSAGE_PATH.fullmatch(urlsplit(self.path).path)
        if not match:
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        try:
            with connect_db() as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM mensajes WHERE id = %s", (int(match[1]),))
                if cursor.rowcount:
                    self.respond(200, {"eliminado": int(match[1])})
                else:
                    self.respond(404, {"error": "Mensaje no encontrado"})
        except pymysql.MySQLError as error:
            self.database_error(error)

    def database_error(self, error):
        self.log_error("Error de MariaDB: %s", type(error).__name__)
        self.respond(503, {
            "status": "error",
            "database": {"engine": "MariaDB", "status": "unavailable"},
        })

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def stop_server(signum, frame):
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop_server)
    initialize_db()
    with ThreadingHTTPServer(("0.0.0.0", 8000), Handler) as server:
        print(f"{SERVICE} escuchando en el puerto 8000", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            print("Servicio detenido.", flush=True)
