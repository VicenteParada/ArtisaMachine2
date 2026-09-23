#!/usr/bin/env bash
# Importa solo semanas posteriores a una fecha, sin volver a cargar archivos terminados.
set -euo pipefail

if (( $# < 2 || $# > 3 )); then
  echo "Uso: import_mongodb.sh DIRECTORIO FECHA_INICIO_YYYYMMDD [--dry-run]" >&2
  exit 2
fi
source_dir="$1"
after_date="$2"
mode="${3:-run}"
mongo_container="artisa-mongodb-historico"
etl_container="artisa-etl-historico-main"
machine_container="artisa-machine"
network="artisa_default"
mongo_host="mongodb:27017"

[[ -d "$source_dir" ]] || { echo "No existe el directorio: $source_dir" >&2; exit 2; }
[[ "$after_date" =~ ^[0-9]{8}$ ]] || { echo "La fecha debe ser YYYYMMDD" >&2; exit 2; }
[[ "$mode" == "run" || "$mode" == "--dry-run" ]] || { echo "Opcion desconocida: $mode" >&2; exit 2; }

shopt -s nullglob
files=("$source_dir"/reportes_semana_*.json)
selected=()
for source_file in "${files[@]}"; do
  filename=$(basename "$source_file")
  if [[ ! "$filename" =~ ^reportes_semana_[0-9]+_([0-9]{8})[.]json$ ]]; then
    echo "Nombre de archivo inesperado: $filename" >&2
    exit 2
  fi
  file_date="${BASH_REMATCH[1]}"
  [[ "$file_date" < "$after_date" ]] && continue
  python3 - "$source_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    prefix = source.read(1048576).lstrip()
if not prefix.startswith("["):
    raise SystemExit("Se esperaba un arreglo JSON de reportes")
try:
    first, _ = json.JSONDecoder().raw_decode(prefix[1:].lstrip())
except json.JSONDecodeError as error:
    raise SystemExit("No se pudo leer el primer reporte JSON") from error
if not isinstance(first, dict) or not {"report_id", "report_date", "asset_id"} <= first.keys():
    raise SystemExit("Formato distinto al de los archivos semanales originales")
if "_id" in first:
    raise SystemExit("Archivo con _id de Mongo; requiere convertirlo antes de continuar")
PY
  size=$(stat -c '%s' "$source_file")
  done_marker="$source_file.artisa-importado"
  working_marker="$source_file.artisa-en-proceso"
  if [[ -f "$working_marker" ]]; then
    echo "Importacion anterior interrumpida en $filename. No se reintenta para evitar duplicados." >&2
    exit 1
  fi
  if [[ -f "$done_marker" ]]; then
    [[ "$(cat "$done_marker")" == "$size" ]] || {
      echo "$filename cambio despues de importarse; revisalo antes de continuar." >&2
      exit 1
    }
    echo "YA IMPORTADO $filename"
    continue
  fi
  selected+=("$source_file")
done

if (( ${#selected[@]} == 0 )); then
  echo "No hay semanas nuevas pendientes desde $after_date."
  exit 0
fi
printf 'PENDIENTE %s\n' "${selected[@]}"
if [[ "$mode" == "--dry-run" ]]; then
  exit 0
fi

[[ "$(docker inspect --format '{{.State.Running}}' "$mongo_container")" == "true" ]] || {
  echo "MongoDB historico no esta activo." >&2
  exit 1
}
docker inspect "$etl_container" >/dev/null
if [[ "$(docker inspect --format '{{.State.Running}}' "$etl_container")" == "true" ]]; then
  docker stop "$etl_container"
fi
if docker inspect "$machine_container" >/dev/null 2>&1 &&
   [[ "$(docker inspect --format '{{.State.Running}}' "$machine_container")" == "true" ]]; then
  docker stop "$machine_container"
fi

for source_file in "${selected[@]}"; do
  filename=$(basename "$source_file")
  size=$(stat -c '%s' "$source_file")
  working_marker="$source_file.artisa-en-proceso"
  done_marker="$source_file.artisa-importado"
  printf '%s\n' "$size" > "$working_marker"
  echo "IMPORTANDO $filename"
  docker run --rm --network "$network" \
    --volume "$source_file:/import/reportes.json:ro" \
    mongo:8.0 mongoimport \
    --host "$mongo_host" --db gps_data --collection raw_gps \
    --file /import/reportes.json --jsonArray --numInsertionWorkers 4 --stopOnError
  mv "$working_marker" "$done_marker"
  echo "COMPLETADO $filename"
done

docker start "$etl_container"
echo "ETL reanudado. Machine queda detenido hasta que el ETL vuelva a 'esperando'."
docker exec "$mongo_container" mongosh --quiet --eval \
  'db.getSiblingDB("gps_data").raw_gps.estimatedDocumentCount()'
