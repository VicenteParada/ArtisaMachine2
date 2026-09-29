"""Recupera históricos por semanas y guarda el progreso en MongoDB."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path


EXPECTED_FILES = 23
CONTAINER = "artisa-mongodb"
STATE_ID = "historico_abril_septiembre_2026"


def mongo_query(docker, query):
    return run_command(
        [docker, "exec", CONTAINER, "mongosh", "--quiet", "--norc", "--eval", query],
        "No se pudo consultar o actualizar el estado de recuperación",
        timeout=300,
    )


def read_recovery_state(docker):
    output = mongo_query(
        docker,
        "print(JSON.stringify(db.getSiblingDB('gps_data')"
        f".recovery_history_state.findOne({{_id: {json.dumps(STATE_ID)}}})));",
    )
    try:
        state = json.loads(output)
        if state is not None and not isinstance(state, dict):
            raise ValueError("Se esperaba un documento de control")
        return state
    except ValueError as exc:
        raise RuntimeError("Estado de recuperación no válido.") from exc


def build_manifest(data_dir, files):
    """Identifica el contenido sin cargar un JSON completo en memoria."""
    print("Verificando los archivos históricos (SHA-256)...", flush=True)
    manifest = []
    for name in files:
        digest = hashlib.sha256()
        size = 0
        try:
            with (data_dir / name).open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
        except OSError as exc:
            raise RuntimeError(f"No se pudo verificar {name}: {exc}") from exc
        manifest.append({"nombre": name, "bytes": size, "sha256": digest.hexdigest()})
    return manifest


def validate_recovery_state(state, manifest):
    names = [entry["nombre"] for entry in manifest]
    completed = state.get("archivos_completados")
    current = state.get("archivo_actual")
    if (
        state.get("_id") != STATE_ID
        or state.get("estado") not in ("en_progreso", "completada")
        or type(state.get("total_archivos")) is not int
        or not isinstance(completed, list)
        or any(not isinstance(name, str) for name in completed)
        or (current is not None and not isinstance(current, str))
        or "archivo_actual" not in state
    ):
        raise RuntimeError("El estado de recuperación está incompleto o no es válido.")
    referenced = set(completed)
    if current is not None:
        referenced.add(current)
    missing = referenced - set(names)
    if missing:
        raise RuntimeError(
            "Faltan archivos registrados en la carpeta seleccionada: "
            + ", ".join(sorted(missing))
        )
    if state.get("archivos") != manifest or state["total_archivos"] != len(names):
        raise RuntimeError(
            "Los archivos históricos no coinciden con la recuperación registrada "
            "(nombres, tamaños o SHA-256), o falta su registro original. No se continuará."
        )
    if len(completed) != len(set(completed)):
        raise RuntimeError("El estado contiene semanas completadas repetidas.")
    if state["estado"] == "completada" and (
        set(completed) != set(names) or current is not None
    ):
        raise RuntimeError("El estado completado es inconsistente. No se continuará.")


def start_recovery(docker, manifest):
    state = {
        "_id": STATE_ID,
        "estado": "en_progreso",
        "total_archivos": len(manifest),
        "archivos": manifest,
        "archivos_completados": [],
        "archivo_actual": None,
    }
    # insertOne evita sobrescribir una recuperación ya registrada.
    mongo_query(
        docker,
        "db.getSiblingDB('gps_data').recovery_history_state.insertOne("
        f"{json.dumps(state)}, {{writeConcern: {{w: 'majority', j: true}}}});",
    )
    return state


def update_recovery_state(docker, update, conditions=None):
    selector = {"_id": STATE_ID, "estado": "en_progreso"}
    if conditions:
        selector.update(conditions)
    mongo_query(
        docker,
        "const result = db.getSiblingDB('gps_data').recovery_history_state.updateOne("
        f"{json.dumps(selector)}, {json.dumps(update)}, "
        "{writeConcern: {w: 'majority', j: true}});"
        "if (result.matchedCount !== 1) {"
        "throw new Error('El estado cambió o no existe. Recuperación detenida.');}",
    )


def mark_week_in_progress(docker, name):
    update_recovery_state(docker, {"$set": {"archivo_actual": name}})


def mark_week_completed(docker, name):
    update_recovery_state(
        docker,
        {"$addToSet": {"archivos_completados": name}, "$set": {"archivo_actual": None}},
        {"archivo_actual": name},
    )


def mark_recovery_completed(docker, files):
    update_recovery_state(
        docker,
        {"$set": {"estado": "completada", "archivo_actual": None}},
        {"archivos_completados": {"$all": files, "$size": len(files)}},
    )


def run_command(command, description, timeout=30, capture_output=True):
    """Ejecuta un comando y convierte errores en mensajes claros."""
    try:
        result = subprocess.run(
            command,
            capture_output=capture_output,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{description}: tiempo de espera agotado ({timeout} segundos)."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"{description}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        detail = detail or f"Código {result.returncode}; consulte la salida anterior."
        raise RuntimeError(f"{description}: {detail}")
    return (result.stdout or "").strip()


def check_container(docker):
    """Comprueba existencia y ejecución sin iniciar el contenedor."""
    names = run_command(
        [docker, "container", "ls", "--all", "--format", "{{.Names}}"],
        "No se pudieron consultar los contenedores",
    )
    if CONTAINER not in names.splitlines():
        raise RuntimeError(f"No existe el contenedor {CONTAINER}.")
    running = run_command(
        [docker, "container", "inspect", "--format", "{{.State.Running}}", CONTAINER],
        f"No se pudo consultar el estado de {CONTAINER}",
    )
    if running == "false":
        raise RuntimeError(
            f"MongoDB no está en ejecución: {CONTAINER}. No se iniciará automáticamente."
        )
    if running != "true":
        raise RuntimeError(f"Estado inesperado del contenedor {CONTAINER}: {running}")


def check_mongodb(docker):
    """Comprueba si hay datos y consulta los índices en modo solo lectura."""
    query = """
        const collection = db.getSiblingDB('gps_data').raw_gps;
        const firstDocument = collection.findOne({}, {_id: 1});
        print(JSON.stringify({
            hasData: firstDocument !== null,
            uniqueReportId: collection.getIndexes().some(index =>
                index.unique === true &&
                !index.partialFilterExpression && !index.sparse &&
                Object.keys(index.key).length === 1 &&
                Object.prototype.hasOwnProperty.call(index.key, 'report_id')
            )
        }));
    """
    output = run_command(
        [docker, "exec", CONTAINER, "mongosh", "--quiet", "--norc", "--eval", query],
        "No se pudo consultar MongoDB mediante mongosh",
        timeout=300,
    )
    try:
        data = json.loads(output)
        has_data = data["hasData"]
        unique_report_id = data["uniqueReportId"]
        if type(has_data) is not bool or type(unique_report_id) is not bool:
            raise ValueError("Tipos de datos inesperados")
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("mongosh devolvió una respuesta no válida.") from exc

    print("MongoDB: disponible")
    print("Base: gps_data")
    print("Colección: raw_gps")
    data_status = "PRESENTES" if has_data else "VACÍO"
    print(f"Datos históricos: {data_status}")
    index_status = "OK" if unique_report_id else "NO ENCONTRADO"
    print(f"Índice único report_id: {index_status}")
    if not has_data:
        print("Estado de carga: MongoDB vacío, listo para recuperación.")
    else:
        print("Estado de carga: MongoDB ya contiene datos.")
        print("No se recomienda volver a cargar históricos automáticamente.")
    return has_data, unique_report_id


def check_mongoimport(docker):
    """Verifica las opciones del binario instalado sin importar datos."""
    help_text = run_command(
        [docker, "exec", CONTAINER, "mongoimport", "--help"],
        "No se pudo comprobar mongoimport",
    )
    for option in ("--jsonArray", "--mode", "--upsertFields", "--maintainInsertionOrder"):
        if option not in help_text:
            raise RuntimeError(f"mongoimport instalado no anuncia la opción {option}.")
    if "upsert" not in help_text:
        raise RuntimeError("mongoimport instalado no anuncia el modo upsert.")


def ensure_unique_index(docker, unique_report_id):
    if unique_report_id:
        return
    query = """
        const collection = db.getSiblingDB('gps_data').raw_gps;
        collection.createIndex(
            {report_id: 1}, {unique: true, name: 'ux_report_id'}
        );
    """
    run_command(
        [docker, "exec", CONTAINER, "mongosh", "--quiet", "--norc", "--eval", query],
        "No se pudo crear el índice único report_id",
        timeout=300,
    )


def import_week(docker, source, resume=False):
    """Copia una semana, importa por report_id y elimina solo su temporal."""
    temporary = f"/tmp/{source.name}"
    interrupted = False
    reserved = False

    def request_stop(signum, frame):
        nonlocal interrupted
        interrupted = True
        print(
            "\nCancelación solicitada. Se esperará a que termine el comando actual "
            "y se limpiará el temporal; no se cargarán más semanas.",
            file=sys.stderr,
            flush=True,
        )

    previous_handler = signal.signal(signal.SIGINT, request_stop)
    try:
        if resume:
            # Solo el temporal de la semana interrumpida registrada en MongoDB.
            run_command(
                [docker, "exec", CONTAINER, "rm", "-f", "--", temporary],
                f"No se pudo limpiar el temporal interrumpido {temporary}",
            )
        if interrupted:
            raise KeyboardInterrupt
        # noclobber impide sobrescribir o borrar un temporal ajeno preexistente.
        run_command(
            [docker, "exec", CONTAINER, "sh", "-c", 'set -C; : > "$1"', "sh", temporary],
            f"No se pudo reservar {temporary}; compruebe si ya existe",
        )
        reserved = True
        if interrupted:
            raise KeyboardInterrupt
        # Sin límite artificial: no dejar mongoimport activo al matar docker exec.
        run_command(
            [docker, "cp", str(source.resolve()), f"{CONTAINER}:{temporary}"],
            f"No se pudo copiar {source.name}",
            timeout=None,
        )
        if interrupted:
            raise KeyboardInterrupt
        run_command(
            [
                docker, "exec", CONTAINER, "mongoimport",
                "--db", "gps_data", "--collection", "raw_gps",
                "--file", temporary, "--jsonArray",
                "--mode=upsert", "--upsertFields=report_id",
                "--maintainInsertionOrder",
            ],
            f"Falló la importación de {source.name}",
            timeout=None,
            capture_output=False,
        )
        if interrupted:
            raise KeyboardInterrupt
    finally:
        pending_error = sys.exc_info()[0] is not None
        try:
            if reserved:
                try:
                    run_command(
                        [docker, "exec", CONTAINER, "rm", "-f", "--", temporary],
                        f"No se pudo eliminar el temporal {temporary}",
                    )
                except RuntimeError as exc:
                    if not pending_error:
                        raise
                    print(f"Error de limpieza: {exc}", file=sys.stderr)
        finally:
            signal.signal(signal.SIGINT, previous_handler)
    if interrupted:
        raise KeyboardInterrupt


def main():
    if len(sys.argv) > 2:
        print(f'Uso: {sys.argv[0]} [carpeta_de_datos]', file=sys.stderr)
        return 1

    if len(sys.argv) == 2:
        folder = sys.argv[1]
    else:
        try:
            folder = input("Ingrese la carpeta donde están los JSON históricos: ")
        except EOFError:
            folder = ""
    if not folder.strip():
        print("Error: debe indicar una carpeta de históricos.", file=sys.stderr)
        return 1
    try:
        data_dir = Path(folder).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: no se pudo resolver la carpeta de históricos: {exc}", file=sys.stderr)
        return 1

    docker = shutil.which("docker")
    if docker is None:
        print("Error: no se encontró el comando docker.", file=sys.stderr)
        return 1

    try:
        run_command([docker, "info"], "Docker no responde correctamente")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Carpeta utilizada: {data_dir}")

    try:
        # Solo archivos directamente dentro de la carpeta, ordenados por nombre.
        with os.scandir(data_dir) as entries:
            files = sorted(
                entry.name
                for entry in entries
                if entry.name.startswith("reportes_semana_")
                and entry.name.endswith(".json")
                and entry.is_file()
            )
    except OSError as exc:
        print(f"Error: no se pudo leer la carpeta de datos: {exc}", file=sys.stderr)
        return 1

    print(f"Cantidad de archivos encontrados: {len(files)}")
    if len(files) == EXPECTED_FILES:
        print(f"Validación del histórico: OK ({EXPECTED_FILES} semanas)")
    else:
        print(
            f"Advertencia: se esperaban {EXPECTED_FILES} archivos históricos "
            f"y se encontraron {len(files)}."
        )
    if not files:
        print(
            "Error: no se encontraron archivos reportes_semana_*.json.",
            file=sys.stderr,
        )
        return 1

    print(f"Primer archivo: {files[0]}")
    print(f"Último archivo: {files[-1]}")

    try:
        check_container(docker)
        has_data, unique_report_id = check_mongodb(docker)
        state = read_recovery_state(docker)
        manifest = None
        if state is not None:
            manifest = build_manifest(data_dir, files)
            validate_recovery_state(state, manifest)
            if not has_data and state["archivos_completados"]:
                raise RuntimeError(
                    "El estado registra semanas completadas, pero raw_gps está vacío. "
                    "No se continuará con un estado inconsistente."
                )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if state is not None and state["estado"] == "completada":
        print("La recuperación histórica ya está completada. No se volverá a cargar.")
        return 0

    resuming = state is not None
    if resuming:
        print("Recuperación histórica incompleta detectada.")
        print(f"Semanas completadas: {len(state['archivos_completados'])}/{len(files)}")
        print(f"Última semana en proceso: {state['archivo_actual'] or 'ninguna'}")
    try:
        prompt = (
            "¿Desea reanudar la recuperación? [s/N]: "
            if resuming else "¿Desea cargar los datos históricos? [s/N]: "
        )
        answer = input(prompt)
    except EOFError:
        answer = ""

    if answer.strip().lower() != "s":
        print("Carga histórica omitida.")
        return 0
    if has_data and state is None:
        print("Carga bloqueada: MongoDB ya contiene datos.")
        return 1

    try:
        check_mongoimport(docker)
        if manifest is None:
            manifest = build_manifest(data_dir, files)
        # Revalidar después de la confirmación, antes de modificar MongoDB.
        has_data, unique_report_id = check_mongodb(docker)
        if read_recovery_state(docker) != state:
            raise RuntimeError("El estado de recuperación cambió. Vuelva a ejecutar el script.")
        if has_data and state is None:
            print("Carga bloqueada: MongoDB ya contiene datos.")
            return 1
        ensure_unique_index(docker, unique_report_id)
        if state is None:
            state = start_recovery(docker, manifest)
        completed = set(state["archivos_completados"])
        interrupted_week = state["archivo_actual"]
        for position, name in enumerate(files, start=1):
            progress = f"[{position:02d}/{len(files):02d}]"
            if name in completed:
                print(f"{progress} OMITIDA (ya completada)", flush=True)
                continue
            resume_week = name == interrupted_week
            action = "REANUDANDO" if resume_week else "Cargando"
            print(f"{progress} {action} {name}...", flush=True)
            try:
                mark_week_in_progress(docker, name)
                import_week(docker, data_dir / name, resume=resume_week)
                mark_week_completed(docker, name)
                completed.add(name)
            except (RuntimeError, OSError) as exc:
                raise RuntimeError(f"Semana fallida: {name}. {exc}") from exc
            except KeyboardInterrupt:
                print(f"\nCarga detenida en {name}.", file=sys.stderr)
                raise
            print(f"{progress} OK", flush=True)
        mark_recovery_completed(docker, files)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Carga detenida. Los datos ya importados se conservan.", file=sys.stderr)
        return 1
    print(f"Carga histórica completada: {len(files)}/{len(files)} semanas.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nOperación cancelada.", file=sys.stderr)
        sys.exit(130)
