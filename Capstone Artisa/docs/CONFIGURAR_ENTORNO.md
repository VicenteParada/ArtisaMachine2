# Configurar el entorno completo (Docker + MongoDB + MariaDB)

Guía para que un compañero nuevo levante exactamente la misma base que tenemos
nosotros: Docker, un MongoDB local con la colección `gps_data.raw_gps`, y el
resto de los servicios del proyecto (MariaDB, ETL, Machine, Endpoints, Front)
vía `docker compose`.

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
git checkout andres-valero   # o la rama/main que corresponda cuando se junten los cambios
cd "Capstone Artisa"
```

---

## 3. Generar las variables de entorno

El proyecto no trae contraseñas en el repositorio. Este script genera un
archivo `.env` local con credenciales aleatorias seguras (no se muestran en
la terminal, y `.env` nunca se sube a Git):

```bash
python3 scripts/prepare_env.py
```

Si `.env` ya existe, el script no lo toca — conserva las credenciales que
tengas.

---

## 4. Levantar MongoDB (con la misma colección de GPS)

MongoDB **no** está definido en `compose.yaml` — corre aparte, como un
contenedor independiente, y el resto de los servicios se conectan a él vía
`host.docker.internal`. Así es como está armado en nuestra máquina hoy:

```bash
docker run -d \
  --name mongo-gps \
  -p 27017:27017 \
  -v mongo_gps_data:/data/db \
  mongo:latest
```

Esto crea:
- Un contenedor Mongo escuchando en el puerto `27017` de tu equipo.
- Un volumen con nombre `mongo_gps_data` para que los datos sobrevivan si
  reinicias o eliminas el contenedor.
- La base `gps_data` y la colección `raw_gps` **se crean solas, vacías**, la
  primera vez que algo escriba en ellas (Mongo no necesita un `CREATE TABLE`
  como MariaDB). No hace falta ningún paso manual adicional para la
  estructura — hoy en nuestra base, `raw_gps` solo tiene el índice por
  defecto de `_id`, nada especial que replicar a mano.

**Importante — esto NO trae los datos reales de GPS.** Este comando te deja
un Mongo vacío, estructuralmente igual al nuestro. Los datos los importa
cada uno desde sus propios archivos JSON (ver siguiente paso).

---

## 5. Importar los archivos JSON propios a Mongo

Cada compañero ya tiene sus archivos JSON de telemetría en su equipo. Hay que
cargarlos a la colección `gps_data.raw_gps` del Mongo que acabas de levantar,
con `mongoimport` (viene incluido en la imagen `mongo:latest`).

Primero identifica el formato del archivo, abriéndolo con un editor de texto:

- **Si el archivo es un array grande** (empieza con `[` y termina con `]`,
  con todos los documentos separados por comas adentro):

  ```bash
  docker cp /ruta/en/tu/pc/datos.json mongo-gps:/tmp/datos.json
  docker exec mongo-gps mongoimport \
    --db gps_data --collection raw_gps \
    --file /tmp/datos.json --jsonArray
  ```

- **Si el archivo tiene un documento JSON por línea** (JSON Lines / NDJSON,
  sin corchetes envolviendo todo):

  ```bash
  docker cp /ruta/en/tu/pc/datos.json mongo-gps:/tmp/datos.json
  docker exec mongo-gps mongoimport \
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
docker exec mongo-gps mongosh --quiet --eval "db.getSiblingDB('gps_data').raw_gps.countDocuments()"
```

---

## 6. Levantar el resto de los servicios

Desde `Capstone Artisa/`, con Docker corriendo y el `.env` ya generado:

```bash
docker compose up --detach --build --wait --wait-timeout 180
```

Esto construye las imágenes de `etl`, `mariadb`, `machine`, `endpoints` y
`front`, y espera a que cada uno pase su chequeo de salud antes de continuar.
La primera vez tarda más porque descarga las dependencias de Machine
Learning.

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
python3 scripts/check_pipeline.py
docker compose logs etl --tail 30
```

El ETL debería mostrar en los logs que está leyendo de Mongo y guardando en
MariaDB. Si en el paso 5 confirmaste que `raw_gps` de Mongo tiene documentos,
acá deberías ver `validos` subiendo en los logs.

---

## 9. Comandos útiles

```bash
docker compose stop            # detener sin borrar datos
docker compose up --detach     # volver a levantar
docker compose logs --tail 50 <servicio>   # ver logs de un servicio puntual
docker compose ps              # ver estado de los 5 contenedores
```

**Nunca uses `docker compose down --volumes`** salvo que quieras borrar la
base de datos completa — elimina el volumen persistente de MariaDB.

Para el contenedor de Mongo (que es aparte, no lo maneja `docker compose`):

```bash
docker stop mongo-gps          # detener
docker start mongo-gps         # volver a iniciar (conserva los datos del volumen)
docker rm -f mongo-gps         # eliminar el contenedor (el volumen mongo_gps_data sobrevive)
```

---

## Nota sobre el README del proyecto

El `README.md` de la raíz describe una versión anterior del proyecto
(orígen por CSV/API SQL, tablas `telemetria_raw`, modelo de regresión de
viajes) que ya no coincide con el código actual — hoy el origen real es
Mongo → `raw_gps`, y el modelo es DBSCAN sobre posiciones geográficas. Esta
guía documenta el estado actual; conviene actualizar el README más adelante
para que no confunda a quien se sume al equipo.
