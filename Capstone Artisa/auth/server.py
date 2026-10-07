"""Administrator login and JWT verifier for the NGINX auth_request gate."""

import base64
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from auth.passwords import valid_password_hash, verify_password

SECRET = os.environ["JWT_SECRET"].encode("utf-8")
USERNAME = os.environ["AUTH_USERNAME"].strip()
PASSWORD_HASH = os.environ["AUTH_PASSWORD_HASH"]
TOKEN_TTL = int(os.environ.get("JWT_TTL_SECONDS", "43200"))
if len(SECRET) < 32 or not USERNAME or not valid_password_hash(PASSWORD_HASH):
    raise RuntimeError("Configuracion de autenticacion insegura o incompleta")


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign(header, payload):
    unsigned = f"{encode(json.dumps(header, separators=(',', ':')).encode())}.{encode(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(SECRET, unsigned.encode(), hashlib.sha256).digest()
    return f"{unsigned}.{encode(signature)}"


def verify(token):
    if len(token) > 4096:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        expected = hmac.new(SECRET, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, decode(parts[2])):
            return False
        header = json.loads(decode(parts[0]))
        payload = json.loads(decode(parts[1]))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    now = int(time.time())
    return (isinstance(header, dict) and header.get("alg") == "HS256"
            and header.get("typ") == "JWT" and isinstance(payload, dict)
            and payload.get("sub") == USERNAME
            and type(payload.get("exp")) is int and payload["exp"] > now
            and type(payload.get("iat")) is int and payload["iat"] <= now + 30)


class Handler(BaseHTTPRequestHandler):
    # Los chequeos de salud de Docker (cada 5 s) no se registran si responden bien;
    # así los registros muestran solo lo útil. Los errores siempre se registran.
    QUIET_PATHS = {"/health"}

    def log_request(self, code="-", size="-"):
        path = getattr(self, "path", "").split("?", 1)[0]
        if path in self.QUIET_PATHS and isinstance(code, int) and 200 <= code < 300:
            return
        super().log_request(code, size)

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok"})
            return
        if self.path == "/verify":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            self.reply(200 if token and verify(token) else 401, {})
            return
        self.reply(404, {"error": "Ruta no encontrada"})

    def do_POST(self):
        if self.path != "/login":
            self.reply(404, {"error": "Ruta no encontrada"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("body")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("body")
            username = data.get("username", "")
            password = data.get("password", "")
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError("body")
            username = username.strip()
        except (ValueError, json.JSONDecodeError):
            self.reply(400, {"error": "Solicitud de login invalida"})
            return
        if (not username or not password or len(username) > 64 or len(password) > 128
                or not verify_password(password, PASSWORD_HASH) or username != USERNAME):
            self.reply(401, {"error": "Usuario o contrasena incorrectos"})
            return
        now = int(time.time())
        jwt = sign({"alg": "HS256", "typ": "JWT"}, {"sub": username, "iat": now, "exp": now + TOKEN_TTL})
        self.reply(200, {"token": jwt, "expires_in": TOKEN_TTL})


if __name__ == "__main__":
    with ThreadingHTTPServer(("0.0.0.0", 8001), Handler) as server:
        server.serve_forever()
