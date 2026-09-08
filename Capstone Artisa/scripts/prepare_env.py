"""Genera credenciales locales una sola vez, sin mostrarlas en la terminal."""

import os
from pathlib import Path
import secrets


env_path = Path(__file__).resolve().parent.parent / ".env"
try:
    descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Se conserva el archivo .env existente.")
else:
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as env_file:
        env_file.write(
            "MARIADB_DATABASE=artisa\n"
            "MARIADB_USER=artisa_app\n"
            f"MARIADB_PASSWORD={secrets.token_hex(24)}\n"
            f"MARIADB_ROOT_PASSWORD={secrets.token_hex(24)}\n"
            "TELEMETRY_ENABLED=0\n"
            "SOURCE_API_URL=\n"
            "SOURCE_API_TIMEOUT_SECONDS=90\n"
            "TELEMETRY_START=2026-03-31T00:00:00\n"
            "TELEMETRY_END=2026-04-01T00:00:00\n"
            "TELEMETRY_BATCH_SIZE=500\n"
            "TELEMETRY_MAX_TOTAL_ROWS=5000\n"
            "TELEMETRY_INTERVAL_SECONDS=900\n"
            "TELEMETRY_CATCHUP_INTERVAL_SECONDS=5\n"
            "TELEMETRY_RETRY_INTERVAL_SECONDS=60\n"
            "TELEMETRY_CONTINUOUS=0\n"
            "TELEMETRY_PAGES_PER_CYCLE=1\n"
        )
    print("Archivo .env creado con credenciales locales aleatorias.")
