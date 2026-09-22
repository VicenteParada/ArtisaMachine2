"""CLI segura para consultar, pausar o procesar una cantidad explícita de bloques."""

import argparse
import json

from common.db import initialize_db
from ETLs.telemetry import TelemetryConfig, mark_status, progress_snapshot, run_batches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--blocks", type=int, metavar="N", help="procesa exactamente hasta N bloques")
    action.add_argument("--until-complete", action="store_true", help="procesa hasta agotar MongoDB")
    action.add_argument("--pause", action="store_true", help="marca la importación como pausada")
    action.add_argument("--status", action="store_true", help="muestra el progreso sin procesar datos")
    args = parser.parse_args()

    initialize_db()
    if args.pause:
        mark_status("pausado")
        result = progress_snapshot()
    elif args.status:
        result = progress_snapshot()
    else:
        blocks = args.blocks if args.blocks is not None else 1
        if blocks < 1:
            parser.error("--blocks debe ser mayor o igual a 1")
        config = TelemetryConfig.from_env()
        executions = run_batches(config, None if args.until_complete else blocks)
        result = {"ejecuciones": executions, "progreso": progress_snapshot()}
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
