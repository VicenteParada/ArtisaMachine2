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
        )
    print("Archivo .env creado con credenciales locales aleatorias.")
