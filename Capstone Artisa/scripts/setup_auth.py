"""Create or rotate the local administrator credentials in the ignored .env file."""

import argparse
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from auth.passwords import make_password_hash, valid_password_hash

ENV_PATH = ROOT / ".env"
AUTH_KEYS = {"AUTH_USERNAME", "AUTH_PASSWORD_HASH", "JWT_SECRET"}


def configure(rotate=False):
    if not ENV_PATH.is_file():
        raise SystemExit("Falta .env; ejecuta primero python3 scripts/prepare_env.py")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    values = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    configured = (values.get("AUTH_USERNAME") and
                  valid_password_hash(values.get("AUTH_PASSWORD_HASH")) and
                  len(values.get("JWT_SECRET", "")) >= 32)
    if configured and not rotate:
        print("La autenticacion local ya esta configurada.")
        return

    password = secrets.token_urlsafe(24)
    preserved = [line for line in lines
                 if line.split("=", 1)[0].strip() not in AUTH_KEYS]
    content = "\n".join(preserved).rstrip("\n") + "\n"
    content += ("AUTH_USERNAME=admin\n"
                f"AUTH_PASSWORD_HASH={make_password_hash(password)}\n"
                f"JWT_SECRET={secrets.token_hex(32)}\n")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                     dir=ROOT, prefix=".env-auth-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, ENV_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    print("Usuario: admin")
    print("Contrasena nueva (guardala ahora; no se conserva en texto): " + password)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate", action="store_true",
                        help="Genera una nueva contrasena e invalida los tokens anteriores")
    configure(parser.parse_args().rotate)
