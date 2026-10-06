"""Inicia los servicios base del proyecto Artisa con Docker Compose."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
print("Preparando entorno...", flush=True)
subprocess.run([sys.executable, str(ROOT / "scripts" / "prepare_env.py")], check=True)
print("Iniciando servicios base de Artisa...", flush=True)
subprocess.run(
    [
        "docker", "compose", "up", "--detach", "--build", "--wait",
        "--wait-timeout", "180",
        "mongodb", "mariadb", "etl", "telemetria", "machine-diario", "endpoints", "front", "auth", "nginx",
    ],
    cwd=ROOT,
    check=True,
)
print("Artisa en funcionamiento: http://localhost:8080")
