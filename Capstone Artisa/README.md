# Artisa: API con MariaDB en Docker

Proyecto local de prueba con dos contenedores:

| Servicio | Contenedor | Función |
| --- | --- | --- |
| API Python | `artisa-docker-demo` | Responde en <http://localhost:8080> |
| MariaDB 11.8 | `artisa-mariadb` | Guarda los mensajes en la base `artisa` |

La API usa PyMySQL para consultar MariaDB. Docker Compose espera a que MariaDB esté
saludable antes de iniciar la API. Los datos se guardan en el volumen
`artisa_mariadb_data`, separado del contenedor.

Este proyecto es una demostración local sin autenticación, no la aplicación final.

## Validación realizada

El 2 de septiembre de 2026 se verificó lo siguiente en este equipo:

- Ambos servicios quedaron en ejecución con estado `healthy`.
- La API guardó, consultó y eliminó un registro real en MariaDB.
- El registro sobrevivió a la recreación del contenedor MariaDB usando el volumen.
- `/health` respondió correctamente desde Windows; al detener MariaDB devolvió
  HTTP 503 y volvió a responder correctamente después de iniciarla.
- Las entradas inválidas se rechazaron y se conservaron tildes, emoji y comillas.

El contenedor de la prueba anterior, sin base de datos, se conserva detenido con el
nombre `artisa-docker-demo-sin-db`. El proyecto actual se controla con Compose.

## Ejecutar desde Visual Studio Code

Abre esta carpeta en VS Code. Las tareas funcionan tanto en Windows como en una
ventana conectada a WSL; desde Windows llaman explícitamente a Ubuntu.

1. Presiona **Ctrl + Shift + P**.
2. Busca **Tareas: Ejecutar tarea**.
3. Selecciona **Docker: levantar API y MariaDB**. Construye la API, inicia ambos
   servicios y espera hasta que estén saludables. También aplica cambios al código.
4. Ejecuta **Docker: comprobar servicio**.

La comprobación debe mostrar:

```text
OK: API y MariaDB responden; escritura, lectura y eliminacion verificadas.
OK: entradas invalidas rechazadas; Unicode y comillas conservados.
```

La prueba crea un registro temporal, lo consulta y elimina únicamente ese registro.
Las tareas anteriores de crear o iniciar el servicio siguen disponibles y ahora
levantan tanto la API como MariaDB.

Para detener ambos servicios usa **Docker: detener servicio**. Para consultar sus
mensajes usa **Docker: ver ultimos logs**.

## Terminal correcta

Si el prompt empieza con `PS C:\...>`, estás en PowerShell. Puedes ejecutar estos
comandos desde la carpeta del proyecto:

```powershell
wsl -d Ubuntu --exec python3 scripts/prepare_env.py
wsl -d Ubuntu --exec docker compose up --detach --build --wait --wait-timeout 120
wsl -d Ubuntu --exec python3 scripts/check.py
wsl -d Ubuntu --exec docker compose ps
```

La configuración de este proyecto abre Ubuntu al seleccionar **Terminal > Nueva
terminal**. Una terminal PowerShell que ya estaba abierta conserva su shell.

Si el prompt se parece a `angelo@equipo:...$`, estás en Ubuntu y puedes ejecutar:

```bash
cd "/mnt/c/Users/angel/Documents/ChatGPT/Capstone Artisa"
python3 scripts/prepare_env.py
docker compose up --detach --build --wait --wait-timeout 120
python3 scripts/check.py
docker compose ps
```

Docker Engine, Compose y Buildx están instalados dentro de Ubuntu. Para abrir VS Code
conectado a WSL puedes ejecutar `code .` desde la terminal de Ubuntu.

## Comprobar MariaDB

Abre <http://localhost:8080/health>. Además de `status: ok`, debe mostrar:

```json
"database": {
  "engine": "MariaDB",
  "status": "ok"
}
```

Este resultado requiere una consulta SQL real a la tabla `mensajes`. Si la conexión
falla, el endpoint responde HTTP 503 y `database.status` cambia a `unavailable`.

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/` | Información de la API |
| GET | `/health` | Estado de la API y conexión con MariaDB |
| GET | `/mensajes` | Hasta 100 mensajes, comenzando por el más reciente |
| POST | `/mensajes` | Guarda un mensaje de 1 a 255 caracteres |
| GET | `/mensajes/1` | Consulta el mensaje con ID 1 |
| DELETE | `/mensajes/1` | Elimina únicamente el mensaje con ID 1 |

Para guardar un mensaje desde **PowerShell**:

```powershell
Invoke-RestMethod -Uri 'http://localhost:8080/mensajes' -Method Post -ContentType 'application/json' -Body '{"texto":"Hola desde Artisa"}'
```

Abre <http://localhost:8080/mensajes> para verlo guardado. Conserva el `id` devuelto
si después quieres consultar o eliminar ese mensaje.

## Persistencia

Detener, iniciar o reconstruir los contenedores conserva los datos del volumen.
`docker compose stop` detiene ambos servicios. `docker compose up --detach --wait`
los vuelve a iniciar. `docker compose down` retira los contenedores y la red,
conservando el volumen. La opción `down --volumes` elimina los datos: no forma parte
del flujo normal de este proyecto.

Para comprobar la persistencia desde Ubuntu:

```bash
python3 scripts/check.py --persistence
```

Esta prueba guarda un registro temporal, recrea únicamente el contenedor MariaDB,
comprueba que el registro sigue disponible y lo elimina al finalizar. La API puede
estar unos segundos sin acceso a la base mientras se recrea MariaDB.

## Configuración y credenciales

`scripts/prepare_env.py` crea `.env` una sola vez con contraseñas aleatorias distintas
para el usuario de la API y para root. Conserva un `.env` existente. El archivo está
excluido de Git y del contexto de construcción de la imagen.

- Base: `artisa`.
- Usuario de la API: `artisa_app`, con permisos sobre esa base.
- Host desde la API: `mariadb`; puerto interno: `3306`.
- MariaDB se comunica con la API por la red de Compose; su puerto no se publica en Windows.
- La API se publica únicamente en `127.0.0.1:8080`.

Para abrir la consola SQL desde Ubuntu:

```bash
docker compose exec mariadb mariadb --user=artisa_app --password artisa
```

Cuando pida la contraseña, usa el valor de `MARIADB_PASSWORD` de tu `.env` local.
Dentro de la consola puedes ejecutar:

```sql
SHOW TABLES;
SELECT id, texto FROM mensajes;
```

Los valores de inicialización de MariaDB se aplican al crear un volumen vacío.
Cambiar las contraseñas de `.env` después no modifica las cuentas ya creadas:
conserva el archivo para seguir accediendo a la base existente.

## Archivos principales

- `compose.yaml`: servicios, red, volumen y arranque coordinado.
- `Dockerfile` y `requirements.txt`: imagen Python y conector MariaDB.
- `app.py`: API y consultas SQL con parámetros.
- `schema.sql`: tabla de prueba creada si todavía no existe.
- `scripts/check.py`: pruebas reales por HTTP, incluida persistencia opcional.
- `.vscode/tasks.json`: tareas para operar el proyecto desde VS Code.

Si cambias el código, ejecuta nuevamente **Docker: levantar API y MariaDB**. La
creación de tabla inicial no es un sistema de migraciones para cambios de esquema.

## Referencias

- [Imagen de MariaDB y variables de inicialización](https://mariadb.com/docs/server/server-management/automated-mariadb-deployment-and-administration/docker-and-mariadb/mariadb-server-docker-official-image-environment-variables)
- [MariaDB 11.8 LTS](https://mariadb.org/11-8-is-lts/)
- [PyMySQL](https://pymysql.readthedocs.io/en/latest/user/examples.html)
- [Orden de arranque de Docker Compose](https://docs.docker.com/compose/how-tos/startup-order/)
