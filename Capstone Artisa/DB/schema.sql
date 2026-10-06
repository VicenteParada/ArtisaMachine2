CREATE TABLE IF NOT EXISTS raw_gps (
    mongo_id VARCHAR(24) NOT NULL,
    report_id VARCHAR(36) NOT NULL,
    report_date DATETIME(6) NOT NULL,
    asset_id VARCHAR(16) NOT NULL,
    asset_name VARCHAR(64) NULL,
    event_name VARCHAR(128) NULL,
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    location VARCHAR(255) NULL,
    speed DOUBLE NULL,
    cartography_limit_speed DOUBLE NULL,
    driver_name VARCHAR(64) NULL,
    driver_last_name VARCHAR(128) NULL,
    driver_document_number VARCHAR(32) NULL,
    ingestado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (report_id),
    UNIQUE KEY idx_raw_gps_mongo_id (mongo_id),
    INDEX idx_raw_gps_asset_fecha (asset_id, report_date),
    INDEX idx_raw_gps_fecha (report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS raw_gps_estado (
    id TINYINT NOT NULL,
    origen VARCHAR(255) NOT NULL,
    ultimo_mongo_id VARCHAR(24) NULL,
    leidos BIGINT UNSIGNED NOT NULL DEFAULT 0,
    validos BIGINT UNSIGNED NOT NULL DEFAULT 0,
    rechazados BIGINT UNSIGNED NOT NULL DEFAULT 0,
    estado VARCHAR(32) NOT NULL,
    ultimo_error VARCHAR(255) NULL,
    rechazos_json TEXT NOT NULL,
    ultima_carga DATETIME(6) NULL,
    ultima_revision DATETIME(6) NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS modelo_telemetria_estado (
    id TINYINT NOT NULL,
    dataset_version CHAR(64) NOT NULL,
    algoritmo VARCHAR(80) NOT NULL,
    metricas_json TEXT NOT NULL,
    zonas_json MEDIUMTEXT NOT NULL,
    entrenado_en DATETIME(6) NOT NULL,
    ultima_revision DATETIME(6) NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS faenas (
    id INT NOT NULL AUTO_INCREMENT,
    external_id VARCHAR(32) NULL,
    nombre VARCHAR(128) NOT NULL,
    grupo VARCHAR(64) NULL,
    area_m2 DOUBLE NULL,
    geocerca POLYGON REF_SYSTEM_ID=4326 NOT NULL,
    creado_en DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY idx_faenas_external_id (external_id),
    SPATIAL INDEX idx_faenas_geocerca (geocerca)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Catálogo de vehículos. raw_gps contiene todos los vehículos que entregó el GPS.
-- las lecturas de camiones filtran con este catálogo (common/vehiculos.py) en vez de
-- borrar filas. DB/vehiculos_data.sql lo puebla y common.vehiculos registra los nuevos.
CREATE TABLE IF NOT EXISTS tipos_vehiculo (
    id_tipo TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    nombre VARCHAR(50) NOT NULL,
    PRIMARY KEY (id_tipo),
    UNIQUE KEY idx_tipos_vehiculo_nombre (nombre)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS vehiculos (
    asset_id VARCHAR(16) NOT NULL,
    id_tipo TINYINT UNSIGNED NOT NULL,
    actualizado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (asset_id),
    INDEX idx_vehiculos_id_tipo (id_tipo),
    FOREIGN KEY (id_tipo) REFERENCES tipos_vehiculo(id_tipo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Telemetría actual recibida desde la API de la empresa (servicio telemetria).
-- Separada de raw_gps para no mezclar posiciones recientes con el histórico.
-- report_date se guarda en UTC y report_id evita duplicados al reintentar.
CREATE TABLE IF NOT EXISTS telemetria_api (
    report_id VARCHAR(128) NOT NULL,
    asset_id VARCHAR(16) NOT NULL,
    asset_name VARCHAR(128) NULL,
    report_date DATETIME(6) NOT NULL,
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    speed DOUBLE NULL,
    heading DOUBLE NULL,
    ignition TINYINT(1) NULL,
    event_name VARCHAR(128) NULL,
    recibido_en DATETIME(6) NOT NULL,
    PRIMARY KEY (report_id),
    INDEX idx_telemetria_api_fecha_recibido (report_date, recibido_en),
    INDEX idx_telemetria_api_asset_fecha (asset_id, report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Estado y cursor del conector. El cursor (fecha, id) se guarda en la misma
-- transacción que cada página: tras un corte de red o un reinicio se retoma
-- exactamente desde ahí, sin perder ni duplicar reportes. cursor_fecha está en la
-- hora propia del origen (tal como la guarda su base), las demás fechas en UTC.
CREATE TABLE IF NOT EXISTS telemetria_api_estado (
    id TINYINT NOT NULL,
    origen VARCHAR(255) NOT NULL,
    cursor_fecha DATETIME(6) NULL,
    cursor_id VARCHAR(128) NULL,
    estado VARCHAR(32) NOT NULL,
    ultimo_error VARCHAR(255) NULL,
    fallos_consecutivos INT UNSIGNED NOT NULL DEFAULT 0,
    leidos BIGINT UNSIGNED NOT NULL DEFAULT 0,
    validos BIGINT UNSIGNED NOT NULL DEFAULT 0,
    rechazados BIGINT UNSIGNED NOT NULL DEFAULT 0,
    rechazos_json TEXT NOT NULL,
    ultimo_exito DATETIME(6) NULL,
    ultimo_dato DATETIME(6) NULL,
    desconectado_desde DATETIME(6) NULL,
    advertencia VARCHAR(255) NULL,
    ultima_revision DATETIME(6) NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Cierre diario (machine_learning/diario.py): resumen por camión de cada día de
-- 00:00 a 00:00 hora Chile, con las mismas reglas del cuaderno de telemetría.
-- Son datos derivados: se recalculan desde telemetria_api y raw_gps cuando hace falta.
CREATE TABLE IF NOT EXISTS reporte_diario (
    fecha DATE NOT NULL,
    asset_id VARCHAR(16) NOT NULL,
    asset_name VARCHAR(128) NULL,
    reportes INT UNSIGNED NOT NULL,
    primer_reporte DATETIME(6) NOT NULL,
    ultimo_reporte DATETIME(6) NOT NULL,
    km_observados DOUBLE NOT NULL,
    viajes INT UNSIGNED NOT NULL,
    tramos_unidos INT UNSIGNED NOT NULL,
    PRIMARY KEY (fecha, asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- estado: en_curso (hoy), esperando_datos (pasó la medianoche pero la API aún no
-- confirma el final del día) o cerrado. ultimo_recibido permite detectar reportes
-- que llegan tarde y recalcular el día.
CREATE TABLE IF NOT EXISTS reporte_diario_estado (
    fecha DATE NOT NULL,
    zona VARCHAR(64) NOT NULL,
    inicio_utc DATETIME(6) NOT NULL,
    fin_utc DATETIME(6) NOT NULL,
    estado VARCHAR(32) NOT NULL,
    reportes INT UNSIGNED NOT NULL,
    camiones INT UNSIGNED NOT NULL,
    km_observados DOUBLE NOT NULL,
    descartados INT UNSIGNED NOT NULL,
    ultimo_recibido DATETIME(6) NULL,
    calculado_en DATETIME(6) NOT NULL,
    cerrado_en DATETIME(6) NULL,
    recalculos INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
