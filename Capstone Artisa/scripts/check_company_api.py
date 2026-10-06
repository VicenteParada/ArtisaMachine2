"""Prueba la conexión con la API de la empresa usando la configuración de .env.

Uso (conectado al Wi-Fi de la empresa), desde la carpeta Capstone Artisa:
    python3 scripts/check_company_api.py

Hace una sola consulta de lectura (los 5 reportes más recientes) e informa:
si hay conexión, cuánto tarda, la hora del reloj del origen frente a UTC y cómo
quedarían guardados los reportes. No escribe en MariaDB ni muestra el token.
"""

from datetime import datetime, timezone
from pathlib import Path
import os
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    load_env()
    from telemetria_api.source import (
        ApiConfig, ApiSourceError, NATIVE_FORMAT, detect_offset, fetch_rows, transform_rows,
    )
    config = ApiConfig.from_env()
    if config is None:
        print("COMPANY_API_URL está vacío en .env: la telemetría seguirá como 'pendiente'.")
        return 1
    date_expr = f"DATE_FORMAT(r.report_date, '{NATIVE_FORMAT}')" if config.native_dates else "r.report_date"
    now_expr = f"DATE_FORMAT(NOW(6), '{NATIVE_FORMAT}')" if config.native_dates else "NOW(6)"
    query = (f"SELECT r.report_id, r.asset_id, r.asset_name, r.latitude, r.longitude, r.speed, "
             f"r.heading, r.ignition, r.event_name, {date_expr} AS report_date_origen, "
             f"{now_expr} AS ahora_origen FROM {config.table} AS r "
             "ORDER BY r.report_date DESC LIMIT 5")
    print(f"Origen: {config.origin}  (tabla {config.table})")
    started = time.monotonic()
    try:
        rows = fetch_rows(config, query)
    except ApiSourceError as error:
        print(f"FALLÓ [{error.kind}]: {error}")
        if error.kind == "sin_conexion":
            print("¿Este equipo está conectado al Wi-Fi de la empresa? ¿La IP/puerto siguen vigentes?")
        return 2
    elapsed = time.monotonic() - started
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    print(f"Conexión OK en {elapsed:.2f} s — {len(rows)} filas")
    offset = detect_offset(rows, utc_now)
    if offset is not None:
        hours = offset.total_seconds() / 3600
        print(f"Reloj del origen: UTC{hours:+g} (COMPANY_API_TIMEZONE={config.timezone_name})")
    accepted, rejected = transform_rows(rows, config, offset)
    for row in accepted:
        print(f"  {row['report_date']:%Y-%m-%d %H:%M:%S} UTC  {row['asset_id']:<8} {row['asset_name'] or ''}")
    for item in rejected:
        print(f"  RECHAZADO {item['report_id']}: {item['motivo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
