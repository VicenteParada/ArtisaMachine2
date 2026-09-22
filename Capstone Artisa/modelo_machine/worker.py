"""raw_gps -> DBSCAN -> modelo_telemetria_estado. Reentrena cada TELEMETRY_MODEL_INTERVAL_SECONDS."""

import os
import threading
import time
from urllib.parse import urlsplit

from common.http import JsonHandler, serve
from modelo_machine.telemetry import ModelConfig, cycle, last_trained_version


INTERVAL = int(os.environ.get("TELEMETRY_MODEL_INTERVAL_SECONDS", "30"))
STATE = {"last_success": 0.0, "error": None, "dataset_version": None}


def loop():
    config = ModelConfig.from_env()
    version = None
    initialized = False
    while True:
        try:
            if not initialized:
                version = last_trained_version()
                STATE["dataset_version"] = version
                initialized = True
            version = cycle(version, config.radius_km, config.min_samples, config.max_rows)
            STATE.update(last_success=time.monotonic(), error=None, dataset_version=version)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            STATE["error"] = message
            print(f"Machine: ciclo fallido ({message})", flush=True)
        time.sleep(INTERVAL)


class Handler(JsonHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        ready = STATE["error"] is None and STATE["dataset_version"] is not None
        self.respond(200 if ready else 503, {"servicio": "machine", "status": "ok" if ready else "unavailable",
                                                "modelo": {k: v for k, v in STATE.items() if k != "last_success"}})


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    serve(Handler, "Machine")
