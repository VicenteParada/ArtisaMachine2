"""Genera credenciales locales una sola vez, sin mostrarlas en la terminal."""

import os
from pathlib import Path
import secrets
import subprocess
import sys


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
            "MONGO_URI=mongodb://mongodb:27017\n"
            "MONGO_DATABASE=gps_data\n"
            "MONGO_COLLECTION=raw_gps\n"
            "MONGO_TIMEOUT_SECONDS=10\n"
            "TELEMETRY_BATCH_SIZE=2000\n"
            "TELEMETRY_INTERVAL_SECONDS=30\n"
        )
    print("Archivo .env creado con credenciales locales aleatorias.")

subprocess.run([sys.executable, str(env_path.parent / "scripts" / "setup_auth.py")],
               cwd=env_path.parent, check=True)
