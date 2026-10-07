# Instalación de Artisa en el servidor de la empresa

## Requisitos

| | Mínimo | Recomendado |
|---|---|---|
| Sistema | Linux con Docker y Docker Compose | Ubuntu Server LTS |
| CPU / RAM | 4 núcleos / 8 GB | 8 núcleos / 16 GB |
| Disco | 100 GB | 200 GB SSD (base + 3 respaldos diarios) |
| Red | Acceso a la API de la empresa (`http://IP:3003/sql`) | IP fija en la red interna |

El servidor debe quedar siempre encendido y en la red donde responde la API.

## Instalación

```bash
git clone <repositorio> && cd Artisa/"Capstone Artisa"
python3 scripts/prepare_env.py      # crea .env con contraseñas aleatorias (una sola vez)
```

Editar `.env`:

```dotenv
COMPANY_API_URL=http://IP_DE_LA_API:3003/sql
COMPANY_API_START=2026-04-01 00:00   # desde qué fecha traer el historial (hora de Chile)
ARTISA_BIND=0.0.0.0                  # permite entrar desde otros equipos de la red
```

Probar la conexión y levantar:

```bash
python3 scripts/check_company_api.py
python3 scripts/start.py
```

Al terminar muestra la dirección, por ejemplo `http://192.168.1.20:8080`.

## Qué pasa después (automático)

- **Datos:** el servicio `telemetria` trae todo el historial desde `COMPANY_API_START` (varias horas la primera vez) y luego consulta la API cada 30 s. Si se cae la red, reintenta y recupera lo pendiente.
- **Cierre diario:** a las `DIARIO_HORA` (00:30) cierra el día anterior (00:00–00:00 Chile) y actualiza la clasificación de rutas.
- **Respaldo:** a las `RESPALDO_HORA` (03:00) guarda la base en `respaldos/`; conserva `RESPALDO_DIAS` días.
- Si el servidor se reinicia, Docker vuelve a levantar todo (`sudo systemctl enable docker`).

## Usuario y contraseña

```bash
python3 scripts/setup_auth.py --rotate --manual   # define una contraseña nueva para la web
docker compose up -d --force-recreate auth
```

## Comandos útiles

| Para | Comando |
|---|---|
| Ver estado | `docker compose ps` |
| Ver avance de la API | `docker logs --tail 20 artisa-telemetria` |
| Ver cierres diarios | `docker logs --tail 20 artisa-machine-diario` |
| Cerrar días ahora | `docker compose exec machine-diario python -m machine_learning.diario_worker --ahora` |
| Revisar rutas | `docker compose exec machine-diario python -m machine_learning.validar_rutas` |
| Respaldar ahora | `docker compose exec respaldo bash /scripts/respaldo.sh --ahora` |
| Restaurar respaldo | `gunzip -c respaldos/artisa_AAAA-MM-DD.sql.gz \| docker compose exec -T mariadb sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'` |
| Actualizar versión | `git pull && python3 scripts/start.py` |

## Si algo falla

| Síntoma | Revisar |
|---|---|
| Web no abre | `docker compose ps`; luego `docker compose restart nginx` |
| "Reconectando con la API" | Red del servidor y `COMPANY_API_URL` en `.env` |
| Días "esperando datos" | Se cierran solos cuando la API vuelve a responder |
| Disco lleno | Bajar `RESPALDO_DIAS` o mover respaldos antiguos fuera del servidor |

Nunca usar `docker compose down -v`: borra la base de datos.
