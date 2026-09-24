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

El mapa toma la última posición disponible de cada vehículo en raw_gps. Cada
cinco minutos vuelve a consultar esas posiciones; esto no convierte el histórico
en telemetría en vivo. Si un vehículo no transmite, aparece como «sin señal»
con la hora de su última lectura. Al seleccionarlo se consulta, bajo demanda,
su recorrido GPS del último día registrado: trazado, distancia y paradas
observadas. El día no equivale necesariamente a una jornada laboral.

Las rutas coloreadas proceden de un catálogo generado fuera de la petición web.
El procesamiento por camión lee MariaDB en secuencia, agrupa recorridos completos
por origen, destino y forma GPS, y clasifica según pasadas por ruta. El catálogo
piloto usa solo cinco camiones y se identifica claramente como parcial. El
proceso completo se escribe en exports/route_catalog.json cuando termina; la
API cambia automáticamente a ese archivo en el siguiente refresco. La geometría
es una traza GPS representativa, sin map matching vial confirmado. El dashboard
de métricas todavía resume una ventana reciente; sus cifras no deben tomarse
como indicadores de las 23 millones de lecturas.

## Carpetas activas

- DB/schema.sql: definición compartida de las seis tablas.
- ETLs/telemetry.py y ETLs/worker.py: carga MongoDB → MariaDB.
- modelo_machine/telemetry.py: clustering geoespacial → modelo_telemetria_estado.
- modelo_machine/route_catalog.py: catálogo histórico versionado y reanudable.
- common/vehicle_journey.py: ficha y recorrido GPS de un camión en un día observado.
- common/map_provider.py y common/telemetry_map.py: contrato y análisis del mapa.
- endpoints/api.py: API de estado, telemetría, mapa, dashboard y faenas.
- front/static/: interfaz; front/server.py sirve archivos y transmite la API.
- auth/ y nginx/: inicio de sesión y entrada HTTP del puerto 8080.
- simulacion/: datos de demostración separados del histórico.

Los módulos processing/ y ETLs/realtime.py son experimentos históricos; no deben
ejecutarse sobre este esquema de seis tablas.

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

La última lectura disponible en esta instalación es del 8 de septiembre de
2026. Cuando lleguen archivos posteriores, copia los JSON originales a
C:\Users\angel\Desktop\base de datos completa con nombres como
reportes_semana_17_20260909.json. Conserva los nombres originales y los archivos anteriores.

En PowerShell de VS Code, primero revisa qué archivos nuevos detecta el
importador. Este paso no modifica ninguna base de datos:

    wsl.exe -e bash "/mnt/c/Users/angel/Documents/ChatGPT/Artisa/Capstone Artisa/scripts/import_mongodb.sh" "/mnt/c/Users/angel/Desktop/base de datos completa" 20260909 --dry-run

El valor 20260909 es un ejemplo: sustitúyelo por la primera fecha realmente
nueva si los archivos recibidos comienzan otro día. Si el ensayo muestra solo
las semanas nuevas y tienen el mismo formato JSON que los originales, ejecútalo
sin --dry-run con esa misma fecha:

    wsl.exe -e bash "/mnt/c/Users/angel/Documents/ChatGPT/Artisa/Capstone Artisa/scripts/import_mongodb.sh" "/mnt/c/Users/angel/Desktop/base de datos completa" 20260909

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

## Catálogo de rutas históricas

Con MariaDB funcionando, desde la carpeta Capstone Artisa:

    wsl.exe -e docker compose build route-catalog
    wsl.exe -e docker compose run --rm route-catalog

El proceso genera un archivo SQLite de trabajo en exports/ después de cada
camión. Si se interrumpe, el mismo comando lo reanuda desde el siguiente camión.
El JSON definitivo solo reemplaza al anterior al finalizar el conjunto completo.
No borres exports/ si necesitas continuar. Para verificar la cobertura, revisa
metadata.processed_report_count, metadata.source_report_count y
metadata.catalog_complete en exports/route_catalog.json. Si la fuente cambia,
se crea automáticamente un nuevo checkpoint versionado.

La ruta del camión se asigna solo si varias posiciones, dirección y proximidad
aportan evidencia suficiente. En otro caso la ficha dice «Sin clasificar».
Patente y fin de jornada no están confirmados en la fuente; se muestran como
no informados. Las paradas y distancias son inferencias GPS, no un registro
certificado de operaciones.

## Limitaciones actuales

El ajuste a la red vial aún no está implementado. Algunos saltos con GPS
insuficiente pueden seguirse viendo como tramos rectos y requieren revisión
manual antes de tomar decisiones operativas. Tampoco existe una fuente en vivo
conectada: la pantalla enseña las últimas lecturas importadas, aunque sean
antiguas. El modelo DBSCAN identifica zonas frecuentes, no trayectos completos
ni tiempos de viaje. Las columnas heredadas adicionales que ya existían en
raw_gps y raw_gps_estado se conservaron en el volumen para no perder
telemetría ni progreso; el esquema compartido no se amplió.
