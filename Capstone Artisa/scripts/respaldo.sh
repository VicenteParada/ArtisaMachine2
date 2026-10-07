#!/bin/bash
# Respaldo diario de MariaDB (servicio "respaldo" de compose.yaml).
#
# Cada día a la hora RESPALDO_HORA (hora de Chile) guarda la base completa comprimida en
# /respaldos/artisa_AAAA-MM-DD.sql.gz y borra los respaldos más antiguos que RESPALDO_DIAS.
# Escribe en un archivo temporal y lo valida antes de dejarlo: un respaldo cortado nunca
# reemplaza a uno bueno. Con "--ahora" hace un respaldo inmediato y termina.
#
# Restaurar (desde la carpeta Capstone Artisa):
#   gunzip -c respaldos/artisa_AAAA-MM-DD.sql.gz | docker compose exec -T mariadb \
#     sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'
set -uo pipefail  # un fallo de mariadb-dump no puede pasar por un respaldo bueno

HORA="${RESPALDO_HORA:-03:00}"
DIAS="${RESPALDO_DIAS:-3}"
DESTINO="${RESPALDO_DIR:-/respaldos}"

respaldar() {
    fecha=$(date +%F)
    final="$DESTINO/artisa_$fecha.sql.gz"
    temporal="$DESTINO/.artisa_$fecha.sql.gz.parcial"
    echo "Respaldo: comenzando $final"
    inicio=$(date +%s)
    if MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb-dump -h mariadb -uroot \
            --single-transaction --quick --routines --events --no-tablespaces \
            --databases "$MARIADB_DATABASE" | gzip -c > "$temporal" \
        && gzip -t "$temporal"; then
        mv -f "$temporal" "$final"
        tamano=$(du -h "$final" | cut -f1)
        echo "Respaldo: listo $final ($tamano en $(( $(date +%s) - inicio )) s)"
        find "$DESTINO" -maxdepth 1 -name 'artisa_*.sql.gz' -mtime +"$((DIAS - 1))" -print -delete \
            | sed 's/^/Respaldo: eliminado /'
        return 0
    fi
    rm -f "$temporal"
    echo "Respaldo: FALLÓ; se conservan los respaldos anteriores"
    return 1
}

if [ "${1:-}" = "--ahora" ]; then
    respaldar
    exit $?
fi

minutos() { echo "$1" | awk -F: '{ print $1 * 60 + $2 }'; }
objetivo=$(minutos "$HORA")
echo "Respaldo: diario a las $HORA (hora de Chile), se conservan $DIAS días en $DESTINO"
while true; do
    hoy="$DESTINO/artisa_$(date +%F).sql.gz"
    ahora=$(minutos "$(date +%H:%M)")
    # Ventana de 3 horas desde la hora fijada: si el equipo se enciende más tarde, ese día
    # no se respalda en medio de la jornada (es una carga pesada) y queda para el siguiente.
    if [ "$ahora" -ge "$objetivo" ] && [ "$ahora" -lt $((objetivo + 180)) ] && [ ! -f "$hoy" ]; then
        respaldar || sleep 1800  # si falla, reintentar en 30 min dentro de la ventana
    fi
    sleep 60
done
