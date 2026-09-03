"""Inicia todo el proyecto con un solo comando desde Ubuntu/WSL."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, str(ROOT / "scripts" / "prepare_env.py")], check=True)
subprocess.run(["docker", "compose", "up", "--detach", "--build", "--wait", "--wait-timeout", "180"], cwd=ROOT, check=True)
print("Artisa en funcionamiento: http://localhost:8080")
