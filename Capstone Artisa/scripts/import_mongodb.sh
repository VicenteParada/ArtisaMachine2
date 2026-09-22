#!/usr/bin/env bash
set -euo pipefail

source_dir="${1:?Uso: import_mongodb.sh /ruta/a/base_de_datos}"
network="${MONGO_DOCKER_NETWORK:-artisa_default}"
mongo_host="${MONGO_DOCKER_HOST:-mongodb:27017}"

shopt -s nullglob
files=("$source_dir"/reportes_semana_*.json)
if (( ${#files[@]} == 0 )); then
  echo "No se encontraron reportes_semana_*.json en $source_dir" >&2
  exit 1
fi

for source_file in "${files[@]}"; do
  echo "IMPORTANDO $(basename "$source_file")"
  docker run --rm     --network "$network"     --volume "$source_file:/import/reportes.json:ro"     mongo:8.0     mongoimport       --host "$mongo_host"       --db gps_data       --collection raw_gps       --file /import/reportes.json       --jsonArray       --numInsertionWorkers 4       --stopOnError
done

echo "TOTAL_MONGODB"
docker exec artisa-mongodb mongosh --quiet --eval   'db.getSiblingDB("gps_data").raw_gps.estimatedDocumentCount()'
