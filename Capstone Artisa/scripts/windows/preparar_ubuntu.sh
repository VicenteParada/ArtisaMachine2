#!/bin/bash
# Parte Linux de la instalación en Windows (la llama scripts/windows/instalar.ps1 dentro de
# Ubuntu WSL). Cada paso se puede repetir sin problemas: lo que ya está hecho se salta.
# Ningún paso borra datos ni volúmenes.
#
#   preparar_ubuntu.sh configurar USUARIO        (root) usuario Linux, systemd, usuario por defecto
#   preparar_ubuntu.sh docker USUARIO            (root) Git, Python y Docker; Docker arranca solo
#   preparar_ubuntu.sh proyecto CARPETA_WINDOWS  (usuario) clona o copia el proyecto y trae .env y respaldos
#   preparar_ubuntu.sh iniciar [RESPALDO]        (usuario) restaura el respaldo si la base está vacía y levanta todo
#   preparar_ubuntu.sh api URL [DESDE]           (usuario) conecta la API de la empresa
#   preparar_ubuntu.sh historico CARPETA         (usuario) carga el JSON histórico (solo desarrollo)
#   preparar_ubuntu.sh red                       (usuario) permite entrar desde otros equipos
#   preparar_ubuntu.sh estado                    (usuario) estado de los servicios
set -euo pipefail

REPO_URL="https://github.com/angelomanque12-jpg/Artisa.git"
RAMA="main"
DESTINO="${HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}/Artisa"
PROYECTO="$DESTINO/Capstone Artisa"

info() { printf '   %s\n' "$*"; }

configurar() {
    local usuario="$1"
    if ! id "$usuario" >/dev/null 2>&1; then
        useradd -m -s /bin/bash -G sudo "$usuario"
        passwd -d "$usuario" >/dev/null           # sin contraseña: se entra desde Windows con wsl
        echo "$usuario ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$usuario"
        chmod 440 "/etc/sudoers.d/$usuario"
        info "Usuario Linux '$usuario' creado."
    fi
    printf '[boot]\nsystemd=true\n\n[user]\ndefault=%s\n' "$usuario" > /etc/wsl.conf
    info "systemd activado y '$usuario' como usuario por defecto."
}

docker_y_herramientas() {
    local usuario="$1"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git python3 curl rsync ca-certificates >/dev/null
    if ! command -v docker >/dev/null 2>&1; then
        info "Instalando Docker (unos minutos)..."
        curl -fsSL https://get.docker.com | sh >/dev/null
    fi
    usermod -aG docker "$usuario"
    systemctl enable --now docker >/dev/null 2>&1
    docker info >/dev/null && info "Docker funcionando."
}

proyecto() {
    local origen="$1"   # carpeta "Capstone Artisa" del lado Windows, ya como ruta /mnt/c/...
    if [ -d "$DESTINO/.git" ]; then
        info "El proyecto ya está en $PROYECTO (se actualiza con git pull)."
        git -C "$DESTINO" pull --ff-only origin "$RAMA" || info "No se pudo actualizar con git; se sigue con lo que hay."
    elif [ -f "$PROYECTO/compose.yaml" ]; then
        info "Actualizando la copia de $PROYECTO desde la carpeta de Windows..."
        rsync -a --exclude .env --exclude respaldos --exclude __pycache__ --exclude .venv "$origen/" "$PROYECTO/"
    elif rm -rf "$DESTINO.tmp" && { GIT_TERMINAL_PROMPT=0 git clone -q --branch "$RAMA" "$REPO_URL" "$DESTINO.tmp" 2>/dev/null \
            || { info "GitHub pide acceso: usuario de GitHub y, como contraseña, un token de solo lectura."; \
                 git clone -q --branch "$RAMA" "$REPO_URL" "$DESTINO.tmp"; }; }; then
        if [ -e "$DESTINO" ]; then rmdir "$DESTINO"; fi   # solo si está vacía: nunca se borra nada
        mv "$DESTINO.tmp" "$DESTINO"
        info "Proyecto clonado desde GitHub (rama $RAMA)."
    else
        rm -rf "$DESTINO.tmp"
        info "No se pudo clonar desde GitHub: se copia la carpeta de Windows."
        mkdir -p "$PROYECTO"
        rsync -a --exclude .env --exclude respaldos --exclude __pycache__ --exclude .venv "$origen/" "$PROYECTO/"
    fi
    # .env y respaldos que vengan de otro equipo (se ponen en la carpeta de Windows antes de instalar)
    if [ -f "$origen/.env" ] && [ ! -f "$PROYECTO/.env" ]; then
        cp "$origen/.env" "$PROYECTO/.env" && chmod 600 "$PROYECTO/.env"
        info "Se usa el .env que estaba en la carpeta de Windows (mismas claves y contraseña)."
    fi
    mkdir -p "$PROYECTO/respaldos"
    if compgen -G "$origen/respaldos/artisa_*.sql.gz" >/dev/null; then
        cp -n "$origen"/respaldos/artisa_*.sql.gz "$PROYECTO/respaldos/" 2>/dev/null || true
    fi
}

base_vacia() {
    local tablas
    tablas=$(docker compose exec -T mariadb sh -c \
        'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\"$MARIADB_DATABASE\" AND table_name=\"reporte_diario\""' \
        2>/dev/null | tr -d '[:space:]')
    [ "${tablas:-0}" = "0" ]
}

iniciar() {
    local respaldo="${1:-}"
    cd "$PROYECTO"
    local nueva=0
    [ -f .env ] || nueva=1
    local log
    log=$(mktemp)   # solo para mostrar la contraseña nueva al final (queda en 600 y se borra)
    python3 scripts/prepare_env.py | tee "$log"
    if [ -n "$respaldo" ] && [ -f "respaldos/$respaldo" ]; then
        docker compose up --detach --wait mariadb
        if base_vacia; then
            info "Restaurando el respaldo $respaldo (puede tardar varios minutos)..."
            gunzip -c "respaldos/$respaldo" | docker compose exec -T mariadb \
                sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'
            info "Respaldo restaurado."
        else
            info "La base ya tiene datos: no se restaura el respaldo (no se pisa nada)."
        fi
    fi
    python3 scripts/artisa.py iniciar
    if [ "$nueva" = 1 ]; then
        grep -i "contras" "$log" | sed 's/^/>>> /' || true
    fi
    rm -f "$log"
}

api() {
    cd "$PROYECTO"
    if [ -n "${2:-}" ]; then
        python3 scripts/artisa.py conectar-api "$1" --desde "$2"
    else
        python3 scripts/artisa.py conectar-api "$1"
    fi
}

historico() {
    cd "$PROYECTO"
    if grep -q '^ETL_HISTORICO=' .env; then
        sed -i 's/^ETL_HISTORICO=.*/ETL_HISTORICO=1/' .env
    else
        echo "ETL_HISTORICO=1" >> .env
    fi
    python3 scripts/artisa.py iniciar
    python3 scripts/recovery.py "$1"
}

red() {
    cd "$PROYECTO"
    python3 scripts/artisa.py acceso-red si
}

estado() {
    cd "$PROYECTO"
    python3 scripts/artisa.py estado
}

paso="${1:?Falta el paso}"
shift
case "$paso" in
    configurar) configurar "$@" ;;
    docker) docker_y_herramientas "$@" ;;
    proyecto) proyecto "$@" ;;
    iniciar) iniciar "$@" ;;
    api) api "$@" ;;
    historico) historico "$@" ;;
    red) red ;;
    estado) estado ;;
    *) echo "Paso desconocido: $paso" >&2; exit 2 ;;
esac
