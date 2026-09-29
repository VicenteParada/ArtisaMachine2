"""Prueba el acceso HTTP local y guarda evidencia sin contraseñas ni tokens."""

import argparse
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(base, method, path, payload=None, token=None):
    headers = {}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(base + path, data=payload, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def render_page(title, rows, timestamp):
    entries = "".join(
        "<tr><td>{name}</td><td><code>{method} {path}</code></td>"
        "<td>{expected}</td><td><strong>{actual}</strong></td>"
        "<td class='{css}'>{result}</td></tr>".format(
            name=escape(row["prueba"]), method=escape(row["metodo"]),
            path=escape(row["ruta"]), expected=row["esperado"],
            actual=escape(str(row["obtenido"])),
            css="pass" if row["correcto"] else "fail",
            result="CORRECTO" if row["correcto"] else "FALLÓ",
        ) for row in rows
    )
    return f"""<!doctype html><html lang="es"><meta charset="utf-8">
<title>{escape(title)} · Artisa</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#eaf1f5;color:#18323c;font:20px Arial,sans-serif}}
main{{width:1220px;margin:55px auto;padding:44px 52px;background:white;border-radius:20px;
box-shadow:0 15px 40px #203d4b1b}}header{{border-bottom:4px solid #66b7d1;padding-bottom:25px}}
h1{{font-size:38px;margin:10px 0}}p{{color:#49616a;line-height:1.5}}.eyebrow{{font-size:16px;
font-weight:700;color:#187696;letter-spacing:.13em;text-transform:uppercase}}table{{border-collapse:collapse;
width:100%;margin-top:36px}}th,td{{padding:20px 14px;border-bottom:1px solid #d7e3e8;text-align:left}}
th{{background:#e8f5f9;font-size:17px}}td{{font-size:18px}}code{{font-size:16px}}.pass{{color:#167545;
font-weight:700}}.fail{{color:#b42318;font-weight:700}}footer{{margin-top:34px;font-size:16px;
color:#607780}}</style><main><header><div class="eyebrow">Transportes Artisa · evidencia de pruebas</div>
<h1>{escape(title)}</h1><p>Ejecutado: {escape(timestamp)} · Entorno: http://127.0.0.1:8080</p>
</header><table><thead><tr><th>Prueba</th><th>Solicitud</th><th>Esperado</th><th>Obtenido</th>
<th>Resultado</th></tr></thead><tbody>{entries}</tbody></table><footer>
Pruebas HTTP directas al servicio local. No se guardan contraseñas, tokens ni datos de telemetría.
</footer></main></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    password = os.environ.get("ARTISA_TEST_PASSWORD")
    if not password:
        parser.error("Define ARTISA_TEST_PASSWORD solo para este proceso")
    base = args.base.rstrip("/")
    rows = []

    def check(name, method, path, expected, payload=None, token=None):
        status, body = request(base, method, path, payload, token)
        rows.append({"prueba": name, "metodo": method, "ruta": path,
                     "esperado": expected, "obtenido": status,
                     "correcto": status == expected})
        return status, body

    check("Página principal", "GET", "/", 200)
    status, body = check("Credenciales correctas", "POST", "/auth/login", 200,
                         json.dumps({"username": "admin", "password": password}).encode())
    token = json.loads(body).get("token") if status == 200 else None
    rows[-1]["correcto"] = rows[-1]["correcto"] and bool(token)
    check("Contraseña incorrecta", "POST", "/auth/login", 401,
          b'{"username":"admin","password":"clave-incorrecta"}')
    check("JSON mal formado", "POST", "/auth/login", 400, b'{"username":')
    check("API sin token", "GET", "/api/estado", 401)
    if token:
        check("API con token válido", "GET", "/api/estado", 200, token=token)
        parts = token.split(".")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        check("API con token alterado", "GET", "/api/estado", 401,
              token=".".join(parts))
    check("Salud de autenticación", "GET", "/auth/health", 200)

    args.output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    result = {"ejecutado_en": timestamp, "base": base, "pruebas": rows,
              "aprobadas": sum(row["correcto"] for row in rows), "total": len(rows)}
    (args.output / "resultados.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "01-login.html").write_text(
        render_page("Inicio de sesión", rows[:4], timestamp), encoding="utf-8")
    (args.output / "02-api.html").write_text(
        render_page("Acceso a la API", rows[4:], timestamp), encoding="utf-8")
    print(f"{result['aprobadas']}/{result['total']} pruebas correctas")
    for row in rows:
        print(f"{row['obtenido']} {row['metodo']} {row['ruta']} · {row['prueba']}")
    raise SystemExit(0 if result["aprobadas"] == result["total"] else 1)


if __name__ == "__main__":
    main()
