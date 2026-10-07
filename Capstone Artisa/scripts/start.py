"""Inicia los servicios base del proyecto Artisa con Docker Compose."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
print("Preparando entorno...", flush=True)
subprocess.run([sys.executable, str(ROOT / "scripts" / "prepare_env.py")], check=True)
def env_value(name, default):
    """Lee una variable de .env sin mostrar ni cargar las demás."""
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and not line.lstrip().startswith("#"):
                return value.strip().strip('"').strip("'")
    return default


historico = env_value("ETL_HISTORICO", "1") != "0"
servicios = ["mariadb", "telemetria", "machine-diario", "endpoints", "front", "auth", "nginx", "respaldo"]
perfil = []
if historico:
    servicios = ["mongodb", "etl"] + servicios
    perfil = ["--profile", "historico"]
(ROOT / "respaldos").mkdir(exist_ok=True)
# El servicio "machine" antiguo (DBSCAN) ya no existe: se quita su contenedor si quedó
# de una versión anterior. No tiene datos propios ni volúmenes.
subprocess.run(["docker", "rm", "--force", "artisa-machine"], cwd=ROOT,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("Iniciando servicios de Artisa" + (" (con carga del JSON histórico)" if historico else "") + "...", flush=True)
subprocess.run(
    ["docker", "compose", *perfil, "up", "--detach", "--build", "--wait", "--wait-timeout", "180", *servicios],
    cwd=ROOT,
    check=True,
)
# Nginx guarda la dirección interna de cada servicio al arrancar. Si se reconstruyó
# front o endpoints, sin este reinicio podría seguir enviando a una dirección vieja.
subprocess.run(["docker", "compose", "restart", "nginx"], cwd=ROOT, check=True)
subprocess.run(["docker", "compose", "up", "--detach", "--wait", "--wait-timeout", "60", "nginx"],
               cwd=ROOT, check=True)

puerto = env_value("ARTISA_PORT", "8080")
if env_value("ARTISA_BIND", "127.0.0.1") == "0.0.0.0":
    ip = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    print(f"Artisa en funcionamiento: http://{ip[0] if ip else 'IP-DE-ESTE-EQUIPO'}:{puerto} (desde la red)")
else:
    print(f"Artisa en funcionamiento: http://localhost:{puerto}")
