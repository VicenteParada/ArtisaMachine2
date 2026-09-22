"""Trabajador ETL controlado; queda pausado salvo activación explícita."""

import os
import threading
import time
from urllib.parse import urlsplit

from common.db import initialize_db
from common.http import JsonHandler, serve
from ETLs.telemetry import (
    TelemetryConfig,
    mark_status,
    progress_snapshot,
    record_error,
    run_batches,
)


def _boolean_env(name, default=False):
    value = os.environ.get(name, "1" if default else "0").strip().casefold()
    return value in {"1", "true", "yes", "si", "sí", "on"}


INTERVAL = int(os.environ.get("TELEMETRY_INTERVAL_SECONDS", "300"))
MAX_BLOCKS_PER_CYCLE = int(os.environ.get("ETL_MAX_BLOCKS_PER_CYCLE", "1"))
AUTOSTART = _boolean_env("ETL_AUTOSTART", False)
STATE = {"last_success": 0.0, "error": None, "estado": "iniciando"}


def loop():
    try:
        initialize_db()
        config = TelemetryConfig.from_env()
        if not AUTOSTART:
            mark_status("pausado")
            STATE.update(last_success=time.monotonic(), error=None, estado="pausado")
    except Exception as error:
        STATE.update(error=f"{type(error).__name__}: {error}", estado="error")
        return

    while True:
        if not AUTOSTART:
            STATE.update(last_success=time.monotonic(), progreso=progress_snapshot())
            time.sleep(min(30, INTERVAL))
            continue
        try:
            results = run_batches(config, max_blocks=MAX_BLOCKS_PER_CYCLE)
            result = results[-1]
            STATE.update(last_success=time.monotonic(), error=None, **result)
            print(
                f"ETL: ciclo controlado; {result['validos']} guardados, "
                f"{result['rechazados']} rechazados; progreso {result['progreso_pct']}%",
                flush=True,
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            STATE.update(error=message, estado="error")
            try:
                record_error(message)
            except Exception:
                pass
            print(f"ETL: ciclo fallido ({message})", flush=True)
        time.sleep(INTERVAL)


class Handler(JsonHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        age = time.monotonic() - STATE["last_success"]
        stable = STATE.get("estado") in {"pausado", "completado"}
        ready = STATE["error"] is None and (stable or age < max(60, INTERVAL * 3))
        state = {key: value for key, value in STATE.items() if key != "last_success"}
        self.respond(
            200 if ready else 503,
            {"servicio": "etl", "status": "ok" if ready else "unavailable", "telemetria": state},
        )


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    serve(Handler, "ETL")
