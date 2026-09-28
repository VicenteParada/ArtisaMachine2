"""Development JWT issuer and verifier for the NGINX auth_request gate."""

import base64
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ["JWT_SECRET"].encode("utf-8")
TOKEN_TTL = int(os.environ.get("JWT_TTL_SECONDS", "3600"))


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign(header, payload):
    unsigned = f"{encode(json.dumps(header, separators=(',', ':')).encode())}.{encode(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(SECRET, unsigned.encode(), hashlib.sha256).digest()
    return f"{unsigned}.{encode(signature)}"


def verify(token):
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        expected = hmac.new(SECRET, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, decode(parts[2])):
            return False
        payload = json.loads(decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return False
    return payload.get("exp", 0) > int(time.time()) and bool(payload.get("sub"))


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
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
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))
        except (ValueError, json.JSONDecodeError):
            self.reply(400, {"error": "Solicitud de login invalida"})
            return
        if not username or not password:
            self.reply(401, {"error": "Ingresa usuario y contraseña"})
            return
        now = int(time.time())
        jwt = sign({"alg": "HS256", "typ": "JWT"}, {"sub": username, "iat": now, "exp": now + TOKEN_TTL})
        self.reply(200, {"token": jwt, "expires_in": TOKEN_TTL})


if __name__ == "__main__":
    with ThreadingHTTPServer(("0.0.0.0", 8001), Handler) as server:
        server.serve_forever()
