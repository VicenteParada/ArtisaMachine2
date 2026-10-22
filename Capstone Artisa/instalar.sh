#!/bin/bash
# Instala Artisa con un solo comando en un equipo Linux nuevo (Ubuntu o Debian).
#
#   bash instalar.sh                       (desde la carpeta "Capstone Artisa")
#
# Sin preguntas, de cero: instala Git, Python y Docker; levanta Artisa; conecta la API de la
# empresa y, si la base está vacía, trae sus reportes desde el 1 de abril de 2026; abre el acceso
# desde la red y deja todo arrancando solo al encender el equipo.
#
# Opcional (variables de entorno, por ejemplo: ARTISA_DESDE=2026-05-01 bash instalar.sh):
#   ARTISA_API_URL   otra URL de la API (por defecto la de la empresa, abajo)
#   ARTISA_DESDE     primer día a traer de la API en una base vacía (por defecto 2026-04-01)
#   ARTISA_RED=no    web solo desde este equipo (por defecto se permite desde la red)
#   ARTISA_JSON      carpeta con el JSON histórico (solo desarrollo)
# Si hay un respaldo artisa_AAAA-MM-DD.sql.gz en respaldos/ y la base está vacía, se restaura
# en vez de descargar todo desde la API (más rápido; trae también su .env para la misma clave).
#
# Se puede volver a ejecutar cuando se quiera: lo que ya está hecho se salta y NUNCA se borran
# datos ni volúmenes.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
REGISTRO="$PWD/instalacion.log"
PUERTO=8080
# API de la empresa (recibe SQL sin token): por eso el repositorio debe seguir siendo PRIVADO.
API_EMPRESA="http://172.93.54.128:3003/sql"

paso() { printf '\n\033[1;36m[%s/6] %s\033[0m\n' "$1" "$2"; }
info() { printf '   %s\n' "$*"; }
env_valor() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -n1 || true; }
sudo_() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

if [ ! -f compose.yaml ] || [ ! -f scripts/artisa.py ]; then
    echo "Ejecuta este archivo desde la carpeta 'Capstone Artisa' del proyecto." >&2
    exit 1
fi
if ! command -v apt-get >/dev/null 2>&1; then
    echo "Este instalador es para Ubuntu o Debian (usa apt)." >&2
    exit 1
fi

exec > >(tee -a "$REGISTRO") 2>&1   # todo queda también en instalacion.log
trap 'printf "\n\033[1;31mLa instalación se detuvo (ver arriba y %s).\033[0m\nCorrige el problema y vuelve a ejecutar: bash instalar.sh (no se borra nada).\n" "$REGISTRO"' ERR

# Una vez instalado Docker, el resto corre con el grupo docker activo (sin cerrar sesión).
if [ "${ARTISA_FASE:-}" != "2" ]; then
    printf '\n=== Instalación de Artisa · %s ===\n' "$(date '+%F %T')"

    # --- 1. Sistema ------------------------------------------------------------------------
    paso 1 "Git, Python y Docker"
    sudo_ env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    sudo_ env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 curl ca-certificates >/dev/null
    if ! command -v docker >/dev/null 2>&1; then
        info "Instalando Docker (unos minutos)..."
        curl -fsSL https://get.docker.com | sudo_ sh >/dev/null
    fi
    # Docker (y con él Artisa) arranca solo al encender el equipo.
    sudo_ systemctl enable --now docker >/dev/null 2>&1 || info "Aviso: no se pudo activar el arranque automático de Docker (systemctl)."
    if [ "$(id -u)" != 0 ] && ! id -nG | grep -qw docker; then
        sudo_ usermod -aG docker "$USER"
        info "Usuario $USER agregado al grupo docker."
    fi
    info "Listo: $(docker --version 2>/dev/null || sudo_ docker --version)"
    if [ "$(id -u)" != 0 ] && ! docker info >/dev/null 2>&1; then
        exec sg docker -c "ARTISA_FASE=2 bash '$PWD/instalar.sh'"
    fi
fi

# --- 2. Configuración --------------------------------------------------------------------------
paso 2 "Configuración"
URL_ACTUAL=$(env_valor COMPANY_API_URL)
URL="${ARTISA_API_URL:-${URL_ACTUAL:-$API_EMPRESA}}"
DESDE="${ARTISA_DESDE:-2026-04-01}"
RED="${ARTISA_RED:-si}"
HISTORICO="${ARTISA_JSON:-}"
RESPALDO=$(ls -1 respaldos/artisa_*.sql.gz 2>/dev/null | sort | tail -n1 || true)
info "API de la empresa: $URL"
[ -n "$RESPALDO" ] && info "Respaldo disponible: $(basename "$RESPALDO")"

# --- 3. Base de datos ------------------------------------------------------------------------
paso 3 "Base de datos"
NUEVA=0
[ -f .env ] || NUEVA=1
CLAVE=$(python3 scripts/prepare_env.py | grep -i "contras" || true)
docker compose up --detach --wait mariadb
sql() {
    docker compose exec -T mariadb sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -N "$MARIADB_DATABASE"' \
        <<<"$1" 2>/dev/null | tr -d '[:space:]'
}
# ¿Ya hay reportes? (las tablas existen desde el primer arranque; importa si tienen filas)
DATOS=$(sql "SELECT EXISTS(SELECT 1 FROM telemetria_api) OR EXISTS(SELECT 1 FROM raw_gps);")
if [ "${DATOS:-0}" = "1" ]; then
    info "La base ya tiene reportes: se conserva tal cual (no se pisa nada)."
elif [ -n "$RESPALDO" ]; then
    info "Base vacía: restaurando $(basename "$RESPALDO") (puede tardar varios minutos)..."
    gunzip -c "$RESPALDO" | docker compose exec -T mariadb sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'
    info "Respaldo restaurado."
    DATOS=1
else
    info "Base vacía: se traerán los reportes de la API desde el $DESDE."
fi

# --- 4. Levantar y cargar ----------------------------------------------------------------------
paso 4 "Levantando Artisa (la primera vez tarda varios minutos)"
python3 scripts/artisa.py iniciar
if [ "${DATOS:-0}" != "1" ]; then
    python3 scripts/artisa.py conectar-api "$URL" --desde "$DESDE"
elif [ "$URL" != "$URL_ACTUAL" ]; then
    python3 scripts/artisa.py conectar-api "$URL"
fi
if [ -n "$HISTORICO" ]; then
    if grep -q '^ETL_HISTORICO=' .env; then
        sed -i 's/^ETL_HISTORICO=.*/ETL_HISTORICO=1/' .env
    else
        echo "ETL_HISTORICO=1" >> .env
    fi
    python3 scripts/artisa.py iniciar
    python3 scripts/recovery.py "$HISTORICO"
fi

# --- 5. Acceso desde la red -----------------------------------------------------------------
paso 5 "Acceso desde la red"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ "$RED" != "no" ]; then
    python3 scripts/artisa.py acceso-red si
    if command -v ufw >/dev/null 2>&1 && sudo_ ufw status | grep -q "Status: active"; then
        sudo_ ufw allow "$PUERTO/tcp" >/dev/null && info "Firewall: puerto $PUERTO abierto."
    fi
else
    info "Solo desde este equipo (ARTISA_RED=no; se cambia con: python3 scripts/artisa.py acceso-red si)."
fi

# --- 6. Listo --------------------------------------------------------------------------------
paso 6 "Listo"
python3 scripts/artisa.py estado || true
printf '\n\033[1;32m=== Artisa instalado ===\033[0m\n'
echo "Web en este equipo: http://localhost:$PUERTO   (usuario: admin)"
[ "$RED" != "no" ] && [ -n "$IP" ] && echo "Web desde la red:   http://$IP:$PUERTO"
if [ "$NUEVA" = 1 ] && [ -n "$CLAVE" ]; then
    printf '\033[1;33m>>> %s\033[0m\n' "$CLAVE"
    echo ">>> Guárdala ahora: no se vuelve a mostrar (se cambia con: python3 scripts/artisa.py contrasena)."
elif [ "$NUEVA" = 0 ]; then
    echo "Se usó el .env existente: la contraseña de admin es la misma de antes."
fi
echo "Los reportes desde abril se descargan y procesan solos por detrás: la primera carga tarda horas."
echo "Avance: python3 scripts/artisa.py logs telemetria   (si la API no responde, reintenta sola)."
echo "Al reiniciar el equipo, Artisa vuelve a arrancar solo. Registro: $REGISTRO"
