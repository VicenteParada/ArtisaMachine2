"""CLI para revisar el cursor o ejecutar bloques del ETL compartido."""

import argparse
import json

from common.db import connect_db, initialize_db
from ETLs.telemetry import TelemetryConfig, run_once


def status():
    with connect_db() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT origen,ultimo_mongo_id,leidos,validos,rechazados,estado,"
            "ultimo_error,ultima_carga FROM raw_gps_estado WHERE id=1"
        )
        return cursor.fetchone() or {"estado": "pendiente", "leidos": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--blocks", type=int, metavar="N")
    actions.add_argument("--until-complete", action="store_true")
    actions.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if not args.status:
        initialize_db()
        config = TelemetryConfig.from_env()
        blocks = args.blocks if args.blocks is not None else 1
        if blocks < 1:
            parser.error("--blocks debe ser mayor o igual a 1")
        executions = 0
        while args.until_complete or executions < blocks:
            result = run_once(config)
            executions += 1
            if result["recibidos"] < config.batch_size:
                break
    print(json.dumps(status(), default=str, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
