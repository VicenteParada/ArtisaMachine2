# Configurar el entorno completo (Docker + MongoDB + MariaDB)

Guía para que un compañero nuevo levante exactamente la misma base que tenemos
nosotros: Docker, un MongoDB local con la colección `gps_data.raw_gps`, y el
resto de los servicios base (MariaDB, ETL, Endpoints, Front, Auth y Nginx)
vía Docker Compose. Machine queda fuera del arranque actual.

Nota importante antes de empezar: **este proyecto vive en la subcarpeta
`Capstone Artisa/` del repositorio `Artisa`**, no en la raíz. Todos los
comandos de esta guía se ejecutan estando dentro de esa carpeta.

---

## 1. Instalar Docker

Necesitas Docker Engine 24+ con el plugin `docker compose` (v2). Elige tu
sistema operativo:

### Linux — Arch / Manjaro

```bash
sudo pacman -Syu docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Cierra sesión y vuelve a entrar (o reinicia) para que el grupo `docker` tome
efecto sin necesitar `sudo` en cada comando.

### Linux — Ubuntu / Debian / WSL

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Vuelve a abrir la terminal después del último comando.

### Windows / macOS

Instala **Docker Desktop**: https://www.docker.com/products/docker-desktop/
Incluye Docker Engine y `docker compose` juntos, no hace falta nada más.
En Windows, se recomienda usar Docker Desktop con el backend WSL2.

### Verificar la instalación

```bash
docker --version
docker compose version
```

Ambos comandos deben responder con un número de versión, sin errores.

---

## 2. Obtener el proyecto

```bash
git clone https://github.com/angelomanque12-jpg/Artisa.git
cd Artisa
git checkout alejandro-dev
cd "Capstone Artisa"
```

---

## 3. Generar las variables de entorno

El proyecto no trae contraseñas en el repositorio. Este script crea la
configuración local `.env` si no existe:

```bash
python3 scripts/prepare_env.py
```

Si `.env` ya existe, conserva su configuración y añade
`JWT_TTL_SECONDS=43200` solo si esa variable no existe, sin sobrescribir un
valor previamente configurado. Luego ejecuta
`scripts/setup_auth.py`: si la autenticación aún no está configurada, genera
el usuario `admin`, una contraseña aleatoria y un secreto JWT seguro. La
contraseña aleatoria se muestra una sola vez; entrégala por un medio privado.
Solo su hash se guarda en `.env`, junto con `AUTH_USERNAME` y `JWT_SECRET`.
Una autenticación válida existente no se cambia automáticamente.

`.env` está ignorado por Git. `.env.example` contiene únicamente referencias
y campos vacíos para el hash y el secreto JWT.

## Acceso al sistema

**Usuario administrador:** `admin`

**Contraseña:** La contraseña inicial es entregada de forma privada junto
con el sistema y no se almacena en el repositorio.

**Acceso:** [http://localhost:8080](http://localhost:8080)

Nginx es la entrada web: dirige `/auth/*` a Auth y el resto al frontend.
La autenticación usa `AUTH_USERNAME`, `AUTH_PASSWORD_HASH` y `JWT_SECRET`
de la configuración local `.env`.

### Duración de la sesión

La sesión tiene una duración predeterminada de **12 horas (43200 segundos)**,
pensada para cubrir una jornada laboral. El usuario puede cerrar sesión
manualmente antes mediante **Cerrar sesión**.

La duración se configura en `.env`, sin modificar código:

```dotenv
JWT_TTL_SECONDS=43200
```

Por ejemplo: `3600` = 1 hora, `28800` = 8 horas, `43200` = 12 horas y
`86400` = 24 horas. Después de cambiar el valor, aplica la configuración:

```bash
docker compose up -d --force-recreate auth
```

La nueva duración se aplica a los próximos inicios de sesión; los tokens
ya emitidos conservan su fecha de expiración. Ejecuta `prepare_env.py` o el
arranque habitual con `scripts/start.py` para añadir el valor predeterminado
si falta en una instalación existente. Compose conserva su respaldo de
1 hora si se inicia directamente sin esa variable en la configuración.

## Cambiar contraseña del administrador

1. Desde la carpeta que contiene el proyecto, entra en él (si ya estás
   dentro, omite este paso):

   ```bash
   cd "Capstone Artisa"
   ```

2. Ejecuta:

   ```bash
   python3 scripts/setup_auth.py --rotate --manual
   ```

   El sistema solicitará `Nueva contraseña:` y `Repita la contraseña:`.
   La entrada permanece oculta. Se aceptan entre 4 y 128 caracteres y ambas
   entradas deben coincidir; si la validación falla, `.env` no se modifica.
   `--manual` requiere `--rotate` y no imprime la contraseña elegida.

3. Aplica la nueva configuración al servicio de autenticación:

   ```bash
   docker compose up -d --force-recreate auth
   ```

No se guarda la contraseña en texto plano: solo se almacena su hash en
`.env`. La rotación también regenera `JWT_SECRET`; al recrear Auth, las
sesiones anteriores quedan invalidadas. No es necesario modificar código.
Sin `--manual`, `--rotate` genera una contraseña aleatoria segura y la
muestra una sola vez.

---

## 4. Levantar MongoDB (con la misma colección de GPS)

MongoDB está definido en `compose.yaml` como servicio `mongodb`, con el
contenedor `artisa-mongodb`. Si necesitas preparar los datos antes del
arranque completo, puedes levantar solo MongoDB:

```bash
docker compose up -d --wait mongodb
```

MongoDB usa el volumen persistente `mongodb_data` y publica el puerto
`127.0.0.1:27017`. Los servicios Docker se conectan mediante
`mongodb://mongodb:27017`. La base `gps_data` y la colección `raw_gps` se
crean con la primera escritura. El arranque no importa los históricos.

**Importante — esto NO trae los datos reales de GPS.** Este comando te deja
un Mongo vacío, estructuralmente igual al nuestro. Los datos los importa
cada uno desde sus propios archivos JSON (ver siguiente paso).

---

## 5. Importar los archivos JSON propios a Mongo

Cada compañero ya tiene sus archivos JSON de telemetría en su equipo. Hay que
cargarlos a la colección `gps_data.raw_gps` del Mongo que acabas de levantar,
con `mongoimport` (viene incluido en la imagen `mongo:8.0`).

Primero identifica el formato del archivo, abriéndolo con un editor de texto:

- **Si el archivo es un array grande** (empieza con `[` y termina con `]`,
  con todos los documentos separados por comas adentro):

  ```bash
  docker cp /ruta/en/tu/pc/datos.json artisa-mongodb:/tmp/datos.json
  docker exec artisa-mongodb mongoimport \
    --db gps_data --collection raw_gps \
    --file /tmp/datos.json --jsonArray
  ```

- **Si el archivo tiene un documento JSON por línea** (JSON Lines / NDJSON,
  sin corchetes envolviendo todo):

  ```bash
  docker cp /ruta/en/tu/pc/datos.json artisa-mongodb:/tmp/datos.json
  docker exec artisa-mongodb mongoimport \
    --db gps_data --collection raw_gps \
    --file /tmp/datos.json
  ```

Si son varios archivos (por ejemplo uno por día o por vehículo), se repite el
mismo `mongoimport` para cada uno — `mongoimport` solo agrega documentos, no
borra los que ya están.

Cada documento debe tener, como mínimo, estos campos para que el ETL lo
acepte (son los que valida `ETLs/telemetry.py`): `report_id`, `asset_id`,
`report_date`, `latitude`, `longitude`. Los demás (`asset_name`, `location`,
`speed`, `cartography_limit_speed`, `event_name`, `driver_name`,
`driver_last_name`, `driver_document_number`) son opcionales. Si a un
compañero el ETL le rechaza filas, lo primero es comparar sus nombres de
campo contra esta lista.

Para confirmar que la carga funcionó:

```bash
docker exec artisa-mongodb mongosh --quiet --eval "db.getSiblingDB('gps_data').raw_gps.countDocuments()"
```

---

## 6. Levantar el resto de los servicios

Desde `Capstone Artisa/`, con Docker corriendo y el `.env` ya generado:

```bash
python3 scripts/start.py
```

El comando prepara la configuración local e inicia `mongodb`, `mariadb`,
`etl`, `endpoints`, `front`, `auth` y `nginx`, con construcción de imágenes
y espera de salud de hasta 180 segundos. No levanta Machine ni cambia una
autenticación ya configurada. La primera ejecución puede tardar más por
las descargas y la construcción de imágenes.

Al terminar, abre [http://localhost:8080](http://localhost:8080). Nginx publica
ese puerto; Front y Auth permanecen dentro de la red Docker.

Al terminar, revisa que todo esté arriba:

```bash
docker compose ps
```

---

## 7. Cargar los datos de catálogo en MariaDB

El schema (`DB/schema.sql`) se aplica solo al iniciar `etl`. Pero los datos
de referencia (tipos de vehículo, vehículos clasificados, faenas) están en
archivos aparte y hay que cargarlos una vez:

```bash
docker exec -i artisa-mariadb mariadb -u artisa_app -p"$(grep MARIADB_PASSWORD .env | cut -d= -f2)" artisa < DB/datos_vehiculos.sql
docker exec -i artisa-mariadb mariadb -u artisa_app -p"$(grep MARIADB_PASSWORD .env | cut -d= -f2)" artisa < DB/faenas_data.sql
```

Ambos son seguros de repetir si algo falla a mitad de camino (aunque
`faenas_data.sql` está pensado para correr una sola vez sobre una tabla
vacía — si ves un error de clave duplicada al repetirlo, es esperable).

---

## 8. Verificar que todo funciona

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check.py
docker compose logs etl --tail 30
```

`check.py` solo realiza GET y no requiere Machine. Para comprobar `/api/estado`,
proporciona un JWT vigente mediante `ARTISA_TEST_TOKEN`; sin él, esa comprobación
se informa como omitida. No guarda ni imprime el token.
`check_pipeline.py` permanece como referencia provisional del contrato antiguo,
no como prueba del sistema actual.

El ETL debería mostrar en los logs que está leyendo de Mongo y guardando en
MariaDB. Si en el paso 5 confirmaste que `raw_gps` de Mongo tiene documentos,
acá deberías ver `validos` subiendo en los logs.

---

## 9. Comandos útiles

```bash
docker compose stop            # detener sin borrar datos
python3 scripts/start.py       # volver a levantar los servicios base, sin Machine
docker compose logs --tail 50 <servicio>   # ver logs de un servicio puntual
docker compose ps              # ver estado de los servicios
```

**Nunca uses `docker compose down --volumes`** salvo que quieras borrar la
base de datos completa — elimina el volumen persistente de MariaDB.

Para administrar únicamente MongoDB con Compose:

```bash
docker compose stop mongodb   # detener
docker compose start mongodb  # volver a iniciar (conserva los datos del volumen)
```

---

## Nota sobre el README del proyecto

El `README.md` de la raíz describe una versión anterior del proyecto
(orígen por CSV/API SQL, tablas `telemetria_raw`, modelo de regresión de
viajes) que ya no coincide con el código actual — hoy el origen real es
Mongo → `raw_gps`, y el modelo es DBSCAN sobre posiciones geográficas. Esta
guía documenta el estado actual; conviene actualizar el README más adelante
para que no confunda a quien se sume al equipo.
