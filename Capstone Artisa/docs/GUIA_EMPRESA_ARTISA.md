# Guía de instalación, acceso y operación — Transportes Artisa

Esta guía está dirigida al personal de **Transportes Artisa** y al equipo técnico responsable de instalar, iniciar, detener y mantener la plataforma.

El sistema actual utiliza **Docker Compose** para levantar los servicios principales y permite operar la plataforma base desde un navegador web. La solución incluye MongoDB, MariaDB, ETL, Endpoints, Frontend, autenticación y Nginx.

> **Estado actual del proyecto**
>
> - Sistema base: operativo.
> - Autenticación: operativa.
> - ETL: operativo.
> - MongoDB y MariaDB: operativos.
> - Frontend y Endpoints: operativos.
> - Telemetría en tiempo real: pendiente de conexión con la API de la empresa.
> - Machine Learning: pendiente de integración final.
>
> Mientras estas dos integraciones estén pendientes, la interfaz las mostrará como **pendientes** y no presentará datos simulados como resultados reales.

---

## 1. Ubicación del proyecto

Dentro del repositorio `Artisa`, la aplicación se encuentra en:

```text
Capstone Artisa/
```

Todos los comandos de esta guía deben ejecutarse desde esa carpeta.

---

## 2. Requisitos

El equipo debe tener instalado:

- Git
- Python 3
- Docker Engine
- Docker Compose v2

### Verificar Docker

```bash
docker --version
docker compose version
```

Ambos comandos deben responder con su versión instalada.

### Linux — Arch / Manjaro

```bash
sudo pacman -Syu docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Después de agregar el usuario al grupo `docker`, cerrar sesión y volver a entrar.

### Linux — Ubuntu / Debian

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Volver a abrir la sesión antes de continuar.

### Windows / macOS

Instalar **Docker Desktop** y asegurarse de que Docker esté iniciado antes de levantar Artisa.

En Windows se recomienda utilizar Docker Desktop con WSL2.

---

## 3. Obtener el proyecto

Para una instalación nueva:

```bash
git clone https://github.com/angelomanque12-jpg/Artisa.git
cd Artisa
git checkout main
cd "Capstone Artisa"
```

Si el proyecto ya está instalado:

```bash
cd Artisa
git checkout main
git pull origin main
cd "Capstone Artisa"
```

---

## 4. Configuración inicial

El repositorio **no almacena contraseñas reales ni secretos**.

Antes del primer arranque ejecutar:

```bash
python3 scripts/prepare_env.py
```

Este proceso:

- crea `.env` si no existe;
- conserva una configuración existente;
- prepara las variables necesarias para Docker;
- configura el usuario administrador;
- genera de forma segura la información de autenticación cuando aún no existe.

El archivo `.env` es local y está ignorado por Git.

### Usuario administrador

```text
Usuario: admin
```

La contraseña inicial debe entregarse de forma privada al responsable de la empresa.

La contraseña **no se guarda en texto plano**. El sistema conserva únicamente su hash.

---

## 5. Cambiar la contraseña del administrador

La empresa puede cambiar la contraseña cuando lo requiera sin modificar el código.

Desde `Capstone Artisa/` ejecutar:

```bash
python3 scripts/setup_auth.py --rotate --manual
```

El sistema solicitará:

```text
Nueva contraseña:
Repita la contraseña:
```

La entrada no se muestra en pantalla.

Después aplicar la nueva configuración:

```bash
docker compose up -d --force-recreate auth
```

La rotación invalida las sesiones anteriores.

---

## 6. Duración de la sesión

La sesión tiene una duración predeterminada de **12 horas**, equivalente a una jornada laboral.

Valor actual:

```dotenv
JWT_TTL_SECONDS=43200
```

Puede modificarse en `.env`.

Ejemplos:

```text
3600  = 1 hora
28800 = 8 horas
43200 = 12 horas
86400 = 24 horas
```

Después de cambiarlo:

```bash
docker compose up -d --force-recreate auth
```

Los nuevos inicios de sesión utilizarán la nueva duración.

---

## 7. Iniciar Artisa

El comando recomendado para iniciar el sistema es:

```bash
python3 scripts/start.py
```

Este comando prepara el entorno y levanta:

- MongoDB
- MariaDB
- ETL
- Endpoints
- Frontend
- Auth
- Nginx

La primera ejecución puede tardar más porque Docker debe descargar y construir imágenes.

Cuando finalice correctamente se mostrará:

```text
Artisa en funcionamiento: http://localhost:8080
```

Abrir en el navegador:

```text
http://localhost:8080
```

---

## 8. Verificar los servicios

Ejecutar:

```bash
docker compose ps
```

Los servicios base deben aparecer como `healthy`.

La instalación actual considera operativos:

```text
mongodb
mariadb
etl
endpoints
front
auth
nginx
```

Machine no forma parte del arranque base mientras su integración esté pendiente.

---

## 9. Estado del sistema

La plataforma dispone de:

```text
http://localhost:8080/health
```

Mientras el sistema base esté funcionando correctamente, debe mostrar:

```json
{
  "status": "ok",
  "servicio": "artisa-api",
  "componentes": {
    "etl": "ok",
    "mariadb": "ok",
    "machine": "pending",
    "endpoints": "ok",
    "front": "ok"
  }
}
```

`machine: pending` indica que la integración de Machine Learning todavía no está disponible. No significa que el sistema base esté caído.

---

## 10. Arquitectura actual

El flujo base es:

```text
MongoDB
   ↓
ETL
   ↓
MariaDB
   ↓
Endpoints
   ↓
Frontend
   ↓
Nginx
   ↓
Navegador
```

La autenticación se gestiona mediante el servicio `auth`.

Machine Learning se integrará posteriormente con los resultados necesarios para mapa, dashboard, rutas y análisis.

---

## 11. Telemetría en tiempo real

La sección de telemetría está preparada para recibir información desde la **API de la empresa**.

Mientras esa conexión no esté implementada:

```text
/api/telemetria
/api/telemetria/resumen
```

responden con estado `pending`.

La plataforma no muestra registros históricos almacenados como si fueran telemetría en tiempo real.

Cuando la API empresarial sea integrada, estas mismas rutas podrán entregar la información actual sin cambiar la estructura principal del frontend.

---

## 12. Machine Learning

Machine Learning está pendiente de integración final.

Actualmente la plataforma deja preparadas las conexiones para:

- mapa;
- dashboard;
- resultados de análisis;
- clasificación de rutas;
- recorridos;
- viajes.

Mientras no existan resultados reales:

```text
/api/mapa
/api/dashboard
```

responden en estado pendiente.

La interfaz no inventa rutas, recorridos, rankings ni métricas.

### Catálogos pendientes de integración

La base persistente de desarrollo contiene:

```text
vehiculos
tipos_vehiculo
```

Estos catálogos todavía no forman parte del esquema versionado de una instalación nueva.

Su provisión reproducible debe quedar resuelta junto con la integración final de Machine.

---

## 13. MongoDB y datos históricos

MongoDB está definido en Docker Compose y utiliza almacenamiento persistente.

Puede iniciarse de forma independiente:

```bash
docker compose up -d --wait mongodb
```

Los servicios internos se conectan mediante:

```text
mongodb://mongodb:27017
```

La base utilizada es:

```text
gps_data
```

y la colección histórica:

```text
raw_gps
```

El arranque normal **no importa automáticamente datos históricos**.

---

## 14. Recuperación histórica

La recuperación histórica es una operación de contingencia y no debe ejecutarse durante un arranque normal.

Cuando exista una recuperación autorizada:

```bash
python3 scripts/recovery.py "/ruta/a/la/carpeta/de/historicos"
```

El proceso:

- valida los archivos;
- utiliza `report_id` como identificador;
- realiza cargas mediante upsert;
- evita duplicados;
- conserva el avance para poder reanudar una recuperación interrumpida.

No ejecutar este procedimiento si la base ya contiene información operativa, salvo que exista un procedimiento de recuperación previamente acordado.

---

## 15. Detener el sistema

Para detener los servicios sin borrar información:

```bash
docker compose stop
```

Para volver a iniciarlos:

```bash
python3 scripts/start.py
```

También es posible iniciar servicios existentes con:

```bash
docker compose start
```

---

## 16. Comandos de administración

### Ver servicios

```bash
docker compose ps
```

### Ver logs de un servicio

```bash
docker compose logs --tail 50 <servicio>
```

Ejemplo:

```bash
docker compose logs --tail 50 etl
```

### Seguir logs en tiempo real

```bash
docker compose logs -f endpoints
```

Para salir:

```text
Ctrl + C
```

### Reiniciar un servicio

```bash
docker compose restart endpoints
```

### Recrear Auth después de cambiar configuración

```bash
docker compose up -d --force-recreate auth
```

---

## 17. No borrar los volúmenes

Los datos de MongoDB y MariaDB se almacenan en volúmenes persistentes.

**No ejecutar:**

```bash
docker compose down --volumes
```

salvo que se quiera eliminar intencionalmente la información almacenada.

Para una detención normal utilizar:

```bash
docker compose stop
```

---

## 18. Comprobaciones técnicas

Para verificar el proyecto:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check.py
```

`check.py` realiza comprobaciones seguras mediante solicitudes GET.

Algunas pruebas relacionadas con Machine pueden quedar omitidas si las dependencias del modelo todavía no están instaladas o la integración permanece pendiente.

El archivo:

```text
scripts/check_pipeline.py
```

se conserva como referencia de un contrato anterior y no representa la validación vigente del sistema final.

---

## 19. Puertos principales

| Servicio | Acceso |
|---|---|
| Plataforma web | `http://localhost:8080` |
| MongoDB interno | `mongodb:27017` dentro de la red Docker |
| MariaDB local | `127.0.0.1:3306` |

MongoDB no publica un puerto en el equipo para evitar conflictos con otras instalaciones locales. Los demás servicios trabajan dentro de la red Docker y no necesitan exponerse directamente al usuario.

---

## 20. Solución de problemas

### Docker no está iniciado

```bash
docker ps
```

En Linux:

```bash
sudo systemctl status docker
```

Si está detenido:

```bash
sudo systemctl start docker
```

### Un servicio no aparece healthy

```bash
docker compose ps
docker compose logs --tail 100 <servicio>
```

### La aplicación no abre

```bash
docker compose ps nginx
curl http://localhost:8080/health
```

### No se puede iniciar sesión

```bash
docker compose ps auth
docker compose logs --tail 50 auth
```

Si es necesario:

```bash
python3 scripts/setup_auth.py --rotate --manual
docker compose up -d --force-recreate auth
```

### Revisar el estado general

```bash
curl http://localhost:8080/health
```

---

## 21. Actualizar el proyecto

Antes de actualizar:

```bash
git checkout main
git pull origin main
```

Luego:

```bash
cd "Capstone Artisa"
python3 scripts/start.py
```

Docker reconstruirá los servicios que hayan cambiado.

El archivo `.env` local y los volúmenes persistentes no deben borrarse durante una actualización normal.

---

## 22. Respaldo recomendado antes de cambios importantes

Antes de realizar cambios importantes en producción, se recomienda respaldar:

- archivo `.env`;
- volumen de MongoDB;
- volumen de MariaDB;
- cualquier fuente externa de datos históricos utilizada por la empresa.

La estrategia final de respaldo debe definirse de acuerdo con la infraestructura donde Transportes Artisa despliegue el sistema.

---

## 23. Resumen operativo

### Primera instalación

```bash
git clone https://github.com/angelomanque12-jpg/Artisa.git
cd Artisa
git checkout main
cd "Capstone Artisa"
python3 scripts/prepare_env.py
python3 scripts/start.py
```

Abrir:

```text
http://localhost:8080
```

### Uso diario

Iniciar:

```bash
python3 scripts/start.py
```

Detener:

```bash
docker compose stop
```

Ver estado:

```bash
docker compose ps
```

Ver salud:

```bash
curl http://localhost:8080/health
```

### Cambiar contraseña

```bash
python3 scripts/setup_auth.py --rotate --manual
docker compose up -d --force-recreate auth
```

---

## 24. Estado de entrega

La versión actual entrega una base funcional y preparada para continuar la integración.

### Disponible

- Docker Compose
- MongoDB
- MariaDB
- ETL
- Endpoints
- Frontend
- autenticación
- Nginx
- inicio de sesión persistente
- cierre de sesión
- estado de servicios
- documentación de arranque y recuperación

### Pendiente de integración

- API de telemetría en tiempo real de Transportes Artisa;
- Machine Learning;
- provisión versionada de los catálogos de vehículos;
- resultados reales de rutas, viajes, recorridos y clasificaciones.

Estos estados se muestran explícitamente en la interfaz y no impiden utilizar ni verificar el sistema base.
