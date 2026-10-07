# Instalación y operación de Artisa

Todos los comandos se ejecutan en una terminal Linux (Ubuntu, o Ubuntu/WSL en Windows),
dentro de la carpeta `Capstone Artisa`. Ningún comando de esta guía borra datos.

## A. Computador desde cero

**1. Instalar Git, Python y Docker** (una sola vez)

```bash
sudo apt update && sudo apt install -y git python3 curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
sudo systemctl enable --now docker
```

Cierra la sesión y vuelve a entrar para usar Docker sin `sudo`.
En Windows, instala Docker Desktop con WSL2 y abre la terminal de Ubuntu.

**2. Descargar el proyecto**

```bash
git clone https://github.com/angelomanque12-jpg/Artisa.git
cd "Artisa/Capstone Artisa"
```

**3. Levantar Artisa**

```bash
python3 scripts/artisa.py iniciar
```

La primera vez crea `.env` con claves aleatorias y muestra el usuario (`admin`) y la
contraseña de la web. **Guárdala**: no se vuelve a mostrar. Tarda unos minutos
mientras descarga y construye. Al terminar, la web queda en http://localhost:8080.

**4. Conectar la telemetría en vivo** (estando en la red de la empresa)

```bash
python3 scripts/artisa.py conectar-api http://IP_DE_LA_API:3003 --desde 2026-04-01
```

`--desde` indica desde qué día traer el historial la primera vez (hora de Chile).
La IP de la API la entrega el equipo; no se guarda en Git.

**5. (Servidor) Permitir entrar desde otros equipos de la red**

```bash
python3 scripts/artisa.py acceso-red si
```

Desde otro equipo se entra con `http://IP_DEL_SERVIDOR:8080`.

## B. Compañeros que ya tienen el proyecto y el histórico

Para empezar a recibir datos en tiempo real (en la red de la empresa):

```bash
git pull
python3 scripts/artisa.py iniciar
python3 scripts/artisa.py conectar-api http://IP_DE_LA_API:3003 --desde "2026-09-08 20:00"
```

`2026-09-08 20:00` empalma con el final del JSON histórico. La primera vez la API
trae todo lo que falta desde esa fecha (puede tardar horas). Para ver el avance:

```bash
python3 scripts/artisa.py logs telemetria
```

Fuera de la red de la empresa la API no responde. El servicio sigue intentando solo
y, al volver a la red, recupera lo que faltaba sin duplicar nada.

## C. Qué corre solo (sin hacer nada)

| Tarea | Cuándo | Qué hace |
|---|---|---|
| Telemetría | cada 30 s | Trae los reportes nuevos de la API. Si se corta la red, reintenta y recupera lo pendiente. |
| Resumen de hoy | cada 10 min | Actualiza el día en curso en **Reportes**. |
| Cierre diario | 00:30 (`DIARIO_HORA`) | Cierra el día anterior (00:00 a 00:00 hora de Chile), recalcula días con reportes atrasados y reclasifica las rutas. |
| Respaldo | 03:00 (`RESPALDO_HORA`) | Guarda la base en `respaldos/` y conserva los últimos 3 días (`RESPALDO_DIAS`). |

Si el equipo se reinicia, Docker vuelve a levantar todo.
Las horas se cambian en `.env` y se aplican con `python3 scripts/artisa.py iniciar`.

## D. Comandos

| Para | Comando |
|---|---|
| Levantar o actualizar (después de `git pull`) | `python3 scripts/artisa.py iniciar` |
| Ver estado y conexión con la API | `python3 scripts/artisa.py estado` |
| Cerrar días ahora | `python3 scripts/artisa.py cierre` |
| Reclasificar rutas ahora | `python3 scripts/artisa.py rutas` |
| Respaldar ahora | `python3 scripts/artisa.py respaldo` |
| Ver registros en vivo | `python3 scripts/artisa.py logs [servicio]` |
| Cambiar la contraseña de la web | `python3 scripts/artisa.py contrasena` |
| Detener (sin borrar datos) | `python3 scripts/artisa.py detener` |

Restaurar un respaldo:

```bash
gunzip -c respaldos/artisa_AAAA-MM-DD.sql.gz | docker compose exec -T mariadb \
  sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'
```

## E. Si algo falla

| Síntoma | Qué hacer |
|---|---|
| La web no abre | `python3 scripts/artisa.py estado`; luego `python3 scripts/artisa.py iniciar` |
| "Reconectando con la API" | Revisar la red y la URL (`conectar-api` otra vez) |
| Días "esperando datos" | Se cierran solos cuando la API vuelve a responder |
| Olvidé la contraseña | `python3 scripts/artisa.py contrasena` |
| Disco lleno | Bajar `RESPALDO_DIAS` en `.env` o sacar respaldos antiguos |

**Nunca** usar `docker compose down -v` ni borrar volúmenes: elimina la base de datos.

## F. Cargar el JSON histórico (opcional, solo desarrollo)

El histórico de abril a septiembre en JSON se carga con MongoDB y el ETL:

```bash
# en .env: ETL_HISTORICO=1
python3 scripts/artisa.py iniciar
python3 scripts/recovery.py "/ruta/a/la/carpeta/de/historicos"
```

La instalación de la empresa no lo necesita: trae el historial desde la API con `--desde`.
