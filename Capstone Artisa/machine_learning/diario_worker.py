"""Servicio de cierre diario (00:00 hora Chile). Sondea cada minuto.

1. Hoy: recalcula el resumen "en curso" cada DIARIO_HOY_CADA_MINUTOS.
2. Días pasados sin cerrar: se cierran cuando pasó la medianoche + DIARIO_GRACIA_MINUTOS
   y la API de la empresa respondió después de ese momento (así se alcanza a recibir el
   último minuto del día). Si la API está desconectada, el día queda "esperando datos"
   con cifras provisorias y se cierra cuando vuelva la conexión.
3. Datos atrasados: si después del cierre llega un reporte de un día ya cerrado
   (por un corte de red, un GPS que envía tarde, etc.), el día se recalcula solo.
   Se revisan los últimos DIARIO_REVISAR_DIAS días.

Si el servicio estuvo apagado varios días, al volver cierra todos los pendientes.
"""

from datetime import date, datetime, timedelta, timezone
import os
import threading
import time
from urllib.parse import urlsplit

from common import dias
from common.db import connect_db, initialize_db
from common.http import JsonHandler, serve
from machine_learning.diario import procesar_dia, ultimo_recibido


GRACIA = timedelta(minutes=int(os.environ.get("DIARIO_GRACIA_MINUTOS", "15")))
HOY_CADA = timedelta(minutes=int(os.environ.get("DIARIO_HOY_CADA_MINUTOS", "10")))
REVISAR_DIAS = int(os.environ.get("DIARIO_REVISAR_DIAS", "7"))
MAX_DIAS_POR_CICLO = int(os.environ.get("DIARIO_MAX_DIAS_POR_CICLO", "3"))
DESDE = os.environ.get("DIARIO_DESDE", "").strip()
INTERVALO = 60
STATE = {"estado": "iniciando", "error": None, "ultimo_ciclo": None, "dias_procesados": 0}
HEARTBEAT = {"value": time.monotonic()}


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def primer_dia(cursor):
    """Primer día a procesar: DIARIO_DESDE (para incluir el histórico) o el primer
    día con datos de la API de la empresa."""
    if DESDE:
        return date.fromisoformat(DESDE)
    try:
        cursor.execute("SELECT MIN(report_date) AS primero FROM telemetria_api")
    except Exception:
        return None
    fila = cursor.fetchone() or {}
    return dias.dia_local(fila["primero"]) if fila.get("primero") else None


def estado_conector(cursor):
    """('sin_api', None) si la API no está configurada; si no ('api', ultimo_exito)."""
    try:
        cursor.execute("SELECT estado, ultimo_exito FROM telemetria_api_estado WHERE id=1")
    except Exception:
        return "sin_api", None
    fila = cursor.fetchone()
    if not fila or fila["estado"] == "sin_configurar":
        return "sin_api", None
    return "api", fila["ultimo_exito"]


def dia_listo_para_cerrar(dia, utc_now, modo, ultimo_exito):
    _, fin = dias.limites_utc(dia)
    if utc_now < fin + GRACIA:
        return False
    if modo == "sin_api":
        return True
    return ultimo_exito is not None and ultimo_exito >= fin + GRACIA


def ciclo(connection, utc_now, procesar=procesar_dia):
    """Un ciclo de trabajo. Devuelve la lista de (día, estado) procesados."""
    hecho = []
    with connection.cursor() as cursor:
        inicio = primer_dia(cursor)
        modo, ultimo_exito = estado_conector(cursor)
        if inicio is None:
            return hecho
        cursor.execute(
            "SELECT fecha, estado, calculado_en, ultimo_recibido FROM reporte_diario_estado WHERE fecha >= %s",
            (inicio,),
        )
        estados = {fila["fecha"]: fila for fila in cursor.fetchall()}
    connection.commit()
    hoy = dias.hoy(utc_now)

    # 1) Días pasados sin cerrar, del más antiguo al más reciente.
    dia = inicio
    while dia < hoy and len(hecho) < MAX_DIAS_POR_CICLO:
        fila = estados.get(dia)
        if not fila or fila["estado"] != "cerrado":
            if dia_listo_para_cerrar(dia, utc_now, modo, ultimo_exito):
                procesar(connection, dia, "cerrado")
                hecho.append((dia, "cerrado"))
            elif not fila or fila["calculado_en"] is None or fila["calculado_en"] <= utc_now - HOY_CADA:
                procesar(connection, dia, "esperando_datos")
                hecho.append((dia, "esperando_datos"))
        dia += timedelta(days=1)

    # 2) Hoy, en curso.
    fila = estados.get(hoy)
    if hoy >= inicio and (not fila or fila["calculado_en"] is None or fila["calculado_en"] <= utc_now - HOY_CADA):
        procesar(connection, hoy, "en_curso")
        hecho.append((hoy, "en_curso"))

    # 3) Reportes que llegaron tarde a días ya cerrados.
    for atras in range(1, REVISAR_DIAS + 1):
        dia = hoy - timedelta(days=atras)
        fila = estados.get(dia)
        if not fila or fila["estado"] != "cerrado":
            continue
        with connection.cursor() as cursor:
            recibido = ultimo_recibido(cursor, dia)
        connection.commit()
        if recibido and (fila["ultimo_recibido"] is None or recibido > fila["ultimo_recibido"]):
            procesar(connection, dia, "cerrado", recalculo=True)
            hecho.append((dia, "recalculado"))
    return hecho


def loop():
    print(f"Cierre diario: zona {dias.ZONA}, desde "
          f"{DESDE or 'el primer dato de la API de la empresa'}, gracia {GRACIA}", flush=True)
    avisado_sin_datos = False
    initialized = False
    while True:
        HEARTBEAT["value"] = time.monotonic()
        try:
            if not initialized:
                initialize_db()  # idempotente: crea las tablas del cierre si faltan
                initialized = True
            # Leer un día completo del histórico puede tomar más que el límite normal de 15 s.
            with connect_db(autocommit=False, read_timeout=600) as connection:
                hecho = ciclo(connection, _utc_now())
            STATE.update(estado="ok", error=None, ultimo_ciclo=_utc_now().isoformat() + "Z")
            STATE["dias_procesados"] += len(hecho)
            for dia, estado in hecho:
                print(f"Cierre diario: {dia} -> {estado}", flush=True)
            if STATE["dias_procesados"] == 0 and not hecho and not avisado_sin_datos:
                avisado_sin_datos = True
                print("Cierre diario: sin días para procesar (sin datos de la API y sin DIARIO_DESDE)", flush=True)
            if len(hecho) >= MAX_DIAS_POR_CICLO:
                continue  # hay días pendientes: seguir sin esperar
        except Exception as error:  # MariaDB caída u otro fallo: se reintenta en el próximo ciclo
            STATE.update(estado="error", error=f"{type(error).__name__}: {error}"[:255])
            print(f"Cierre diario: ciclo fallido ({STATE['error']})", flush=True)
        time.sleep(INTERVALO)


class Handler(JsonHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self.respond(404, {"error": "Ruta no encontrada"})
            return
        alive = time.monotonic() - HEARTBEAT["value"] < 900
        self.respond(200 if alive else 503, {"servicio": "machine-diario",
                                             "status": "ok" if alive else "unavailable",
                                             "zona": dias.ZONA, "cierre": dict(STATE)})


def watchdog(worker):
    while True:
        time.sleep(30)
        if not worker.is_alive() or time.monotonic() - HEARTBEAT["value"] > 3600:
            print("Cierre diario: el ciclo se detuvo; reiniciando el contenedor", flush=True)
            os._exit(1)


if __name__ == "__main__":
    worker = threading.Thread(target=loop, daemon=True, name="cierre-diario")
    worker.start()
    threading.Thread(target=watchdog, args=(worker,), daemon=True).start()
    serve(Handler, "Cierre diario")
