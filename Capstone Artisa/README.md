# Artisa: telemetría histórica y mapa local

La aplicación se abre en http://localhost:8080. El código de esta rama usa el
mismo esquema MariaDB de seis tablas acordado por el equipo:

- raw_gps: reportes GPS aceptados.
- raw_gps_estado: cursor, avance y rechazos del ETL.
- modelo_telemetria_estado: métricas y zonas del modelo DBSCAN.
- tipos_vehiculo y vehiculos: catálogo de vehículos.
- faenas: geocercas.

El origen histórico local es MongoDB (gps_data.raw_gps). El ETL lee documentos
en bloques de 5000 y confirma los registros y el cursor juntos en MariaDB. El
volumen de MariaDB conserva lo ya cargado al parar y reanudar el contenedor.
Los datos de MongoDB y MariaDB son locales y no se suben a GitHub.

El mapa y el dashboard leen una muestra reciente de hasta 3000 reportes de
raw_gps cada cinco minutos. Infieren trayectos y paradas de esa ventana, mientras
el total histórico se consulta en raw_gps_estado. La clasificación verde,
naranja y roja se aplica a las rutas inferidas en la ventana. El trazado sigue
puntos GPS filtrados; aún no hay ajuste exacto a la red vial. Los camiones
muestran la última posición dentro de esa muestra, que puede ser antigua si no
se recibe telemetría nueva. La sección de datos simulados usa
simulacion/mapa_procesado.json y no mezcla esos datos con raw_gps. La predicción
de duración queda deshabilitada porque el esquema compartido no contiene ese
modelo ni sus solicitudes.

## Carpetas activas

- DB/schema.sql: definición compartida de las seis tablas.
- ETLs/telemetry.py y ETLs/worker.py: carga MongoDB → MariaDB.
- modelo_machine/telemetry.py: clustering geoespacial → modelo_telemetria_estado.
- common/map_provider.py y common/telemetry_map.py: contrato y análisis del mapa.
- endpoints/api.py: API de estado, telemetría, mapa, dashboard y faenas.
- front/static/: interfaz; front/server.py sirve archivos y transmite la API.
- auth/ y nginx/: inicio de sesión y entrada HTTP del puerto 8080.
- simulacion/: datos de demostración separados del histórico.

El archivo app.py de la raíz es una demostración anterior y no forma parte de
los servicios activos. Los módulos processing/ y ETLs/realtime.py son
experimentos históricos; no deben ejecutarse sobre este esquema de seis tablas.

## Arranque y seguimiento en WSL

Desde PowerShell de VS Code, situado en la carpeta Capstone Artisa, inicia la
aplicación sin arrancar el modelo durante la carga histórica:

    wsl.exe -e docker compose up -d --build mariadb endpoints front auth nginx

En esta instalación, la carga histórica se ejecuta en los contenedores
artisa-mongodb-historico y artisa-etl-historico-main. Para reanudarla:

    wsl.exe -e docker start artisa-mongodb-historico artisa-etl-historico-main

Para pausarla sin perder el cursor:

    wsl.exe -e docker stop artisa-etl-historico-main

Para ver los últimos bloques:

    wsl.exe -e docker logs --tail 20 artisa-etl-historico-main

Para ver el avance exacto del ETL desde PowerShell:

    "SELECT leidos, validos, rechazados, estado FROM raw_gps_estado WHERE id=1;" | wsl.exe -e docker exec -i artisa-mariadb sh -c 'mariadb --user="$MARIADB_USER" --password="$MARIADB_PASSWORD" "$MARIADB_DATABASE"'

No uses docker compose down -v: esa opción elimina los volúmenes con los datos.
La carga de los archivos históricos disponibles ya terminó. El modelo DBSCAN analiza hasta 5000 posiciones recientes por ciclo para limitar el uso de memoria. Se inicia con:

    wsl.exe -e docker compose up -d machine

## Agregar nuevas semanas de reportes

Los archivos disponibles cubren del 1 de abril al 26 de mayo de 2026. Cuando
lleguen semanas posteriores, copia los JSON originales a
C:\Users\angel\Desktop\base de datos completa con nombres como
reportes_semana_09_20260527.json. No muevas los ocho archivos anteriores.

En PowerShell de VS Code, primero revisa qué archivos nuevos detecta el
importador. Este paso no modifica ninguna base de datos:

    wsl.exe -e bash "/mnt/c/Users/angel/Documents/ChatGPT/Artisa/Capstone Artisa/scripts/import_mongodb.sh" "/mnt/c/Users/angel/Desktop/base de datos completa" 20260527 --dry-run

Si muestra solo las semanas nuevas y sus archivos tienen el mismo formato JSON
que los originales, ejecútalo sin --dry-run:

    wsl.exe -e bash "/mnt/c/Users/angel/Documents/ChatGPT/Artisa/Capstone Artisa/scripts/import_mongodb.sh" "/mnt/c/Users/angel/Desktop/base de datos completa" 20260527

El script detiene temporalmente el ETL y el modelo, carga solo las semanas
pendientes en MongoDB y vuelve a iniciar el ETL. No detengas el PC ni cierres
la terminal durante la importación de un archivo. Para seguir el paso a MariaDB:

    wsl.exe -e docker logs --tail 20 -f artisa-etl-historico-main

También puedes consultar raw_gps_estado con el comando de arriba. Cuando el
estado vuelva a esperando, inicia nuevamente el modelo:

    wsl.exe -e docker start artisa-machine

Si aparece un archivo .artisa-en-proceso, la importación se interrumpió;
revisa cuántos documentos entraron antes de reintentar para evitar duplicados.
No borres los volúmenes de Docker ni reinicies el cursor del ETL. Los archivos
JSON y los marcadores de importación quedan fuera de GitHub.
## Limitaciones actuales

Las rutas y paradas del mapa son inferencias exploratorias de una ventana, no
resultados persistidos de todo el histórico. El modelo DBSCAN identifica zonas
frecuentes, no trayectos completos ni tiempos de viaje. La sincronización con
una fuente en vivo y el map matching vial todavía no están implementados.
Las columnas heredadas adicionales que ya existían en raw_gps y raw_gps_estado
se conservaron en el volumen para no perder telemetría ni progreso; el código
nuevo solo necesita las columnas definidas en DB/schema.sql.
