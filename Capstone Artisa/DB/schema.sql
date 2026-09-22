CREATE TABLE IF NOT EXISTS mensajes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    texto VARCHAR(255) NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS viajes (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    origen VARCHAR(80) NOT NULL,
    destino VARCHAR(80) NOT NULL,
    distancia_km DOUBLE NOT NULL,
    carga_kg DOUBLE NOT NULL,
    paradas INT NOT NULL,
    duracion_horas DOUBLE NOT NULL,
    dataset_version CHAR(64) NOT NULL,
    actualizado_en DATETIME(6) NOT NULL,
    INDEX idx_viajes_version (dataset_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS etl_estado (
    id TINYINT NOT NULL PRIMARY KEY,
    dataset_version CHAR(64) NOT NULL,
    origen VARCHAR(120) NOT NULL,
    leidos INT NOT NULL,
    validos INT NOT NULL,
    rechazados INT NOT NULL,
    rechazos_json TEXT NOT NULL,
    ultima_carga DATETIME(6) NOT NULL,
    ultima_revision DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS modelo_estado (
    id TINYINT NOT NULL PRIMARY KEY,
    dataset_version CHAR(64) NOT NULL,
    algoritmo VARCHAR(80) NOT NULL,
    metricas_json TEXT NOT NULL,
    coeficientes_json TEXT NOT NULL,
    intercepto DOUBLE NOT NULL,
    entrenado_en DATETIME(6) NOT NULL,
    ultima_revision DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS predicciones_viajes (
    viaje_id VARCHAR(32) NOT NULL PRIMARY KEY,
    dataset_version CHAR(64) NOT NULL,
    duracion_estimada DOUBLE NOT NULL,
    calculada_en DATETIME(6) NOT NULL,
    FOREIGN KEY (viaje_id) REFERENCES viajes(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS solicitudes_prediccion (
    id CHAR(36) NOT NULL PRIMARY KEY,
    distancia_km DOUBLE NOT NULL,
    carga_kg DOUBLE NOT NULL,
    paradas INT NOT NULL,
    estado VARCHAR(16) NOT NULL DEFAULT 'pendiente',
    duracion_estimada DOUBLE NULL,
    dataset_version CHAR(64) NULL,
    creada_en DATETIME(6) NOT NULL,
    completada_en DATETIME(6) NULL,
    INDEX idx_solicitudes_estado (estado, creada_en)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS telemetria_raw (
    report_id VARCHAR(128) NOT NULL,
    sequential_id BIGINT NULL,
    asset_id VARCHAR(64) NOT NULL,
    asset_name VARCHAR(255) NULL,
    report_date DATETIME(6) NOT NULL,
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    speed DOUBLE NULL,
    gps_speed DOUBLE NULL,
    heading DOUBLE NULL,
    odometer DOUBLE NULL,
    ignition BOOLEAN NULL,
    event_id BIGINT NULL,
    event_name VARCHAR(255) NULL,
    gps_validity INT NULL,
    gps_satellites INT NULL,
    gps_dop DOUBLE NULL,
    raw_json JSON NOT NULL,
    ingestado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (report_id),
    INDEX idx_telemetria_asset_fecha (asset_id, report_date),
    INDEX idx_telemetria_fecha (report_date),
    INDEX idx_telemetria_evento (event_id, report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS telemetria_etl_estado (
    id TINYINT NOT NULL,
    origen VARCHAR(255) NOT NULL,
    ventana_desde DATETIME(6) NOT NULL,
    ventana_hasta DATETIME(6) NOT NULL,
    ultimo_report_date DATETIME(6) NULL,
    ultimo_report_id VARCHAR(128) NULL,
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

ALTER TABLE raw_gps ADD COLUMN IF NOT EXISTS gps_speed DOUBLE NULL AFTER speed;
ALTER TABLE raw_gps ADD COLUMN IF NOT EXISTS heading DOUBLE NULL AFTER gps_speed;
ALTER TABLE raw_gps ADD COLUMN IF NOT EXISTS odometer DOUBLE NULL AFTER heading;
ALTER TABLE raw_gps ADD COLUMN IF NOT EXISTS ignition BOOLEAN NULL AFTER odometer;
ALTER TABLE raw_gps ADD COLUMN IF NOT EXISTS event_id BIGINT NULL AFTER ignition;
ALTER TABLE raw_gps ADD COLUMN IF NOT EXISTS sequential_id BIGINT NULL AFTER event_id;
ALTER TABLE raw_gps ADD INDEX IF NOT EXISTS idx_raw_gps_conductor_fecha (driver_document_number, report_date);
ALTER TABLE raw_gps ADD INDEX IF NOT EXISTS idx_raw_gps_coordenadas (latitude, longitude);
ALTER TABLE raw_gps_estado ADD COLUMN IF NOT EXISTS total_origen BIGINT UNSIGNED NOT NULL DEFAULT 0 AFTER rechazados;
ALTER TABLE raw_gps_estado ADD COLUMN IF NOT EXISTS bloques_completados BIGINT UNSIGNED NOT NULL DEFAULT 0 AFTER total_origen;
ALTER TABLE raw_gps_estado ADD COLUMN IF NOT EXISTS bloques_totales BIGINT UNSIGNED NOT NULL DEFAULT 0 AFTER bloques_completados;
ALTER TABLE raw_gps_estado ADD COLUMN IF NOT EXISTS tamano_bloque INT UNSIGNED NOT NULL DEFAULT 0 AFTER bloques_totales;

CREATE TABLE IF NOT EXISTS etl_bloques (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    numero_bloque BIGINT UNSIGNED NOT NULL,
    cursor_desde VARCHAR(24) NULL,
    cursor_hasta VARCHAR(24) NULL,
    leidos INT UNSIGNED NOT NULL,
    validos INT UNSIGNED NOT NULL,
    rechazados INT UNSIGNED NOT NULL,
    estado VARCHAR(24) NOT NULL,
    iniciado_en DATETIME(6) NOT NULL,
    completado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY idx_etl_bloque_numero (numero_bloque),
    INDEX idx_etl_bloque_estado (estado, completado_en)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS vehiculos_ultima_posicion (
    asset_id VARCHAR(16) NOT NULL,
    asset_name VARCHAR(64) NULL,
    report_id VARCHAR(36) NOT NULL,
    report_date DATETIME(6) NOT NULL,
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    speed DOUBLE NULL,
    gps_speed DOUBLE NULL,
    heading DOUBLE NULL,
    odometer DOUBLE NULL,
    ignition BOOLEAN NULL,
    event_name VARCHAR(128) NULL,
    driver_name VARCHAR(64) NULL,
    driver_last_name VARCHAR(128) NULL,
    driver_document_number VARCHAR(32) NULL,
    actualizado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (asset_id),
    UNIQUE KEY idx_vehiculo_ultimo_report (report_id),
    INDEX idx_vehiculo_ultima_fecha (report_date),
    INDEX idx_vehiculo_ultima_estado (ignition, report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS encadenamientos_viajes (
    encadenamiento_id CHAR(36) NOT NULL,
    asset_id VARCHAR(16) NOT NULL,
    driver_document_number VARCHAR(32) NULL,
    inicio DATETIME(6) NOT NULL,
    termino DATETIME(6) NOT NULL,
    cantidad_viajes INT UNSIGNED NOT NULL DEFAULT 0,
    distancia_km DOUBLE NOT NULL DEFAULT 0,
    duracion_minutos DOUBLE NOT NULL DEFAULT 0,
    tiempo_detenido_minutos DOUBLE NOT NULL DEFAULT 0,
    estado VARCHAR(24) NOT NULL,
    actualizado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (encadenamiento_id),
    INDEX idx_encadenamiento_asset_fecha (asset_id, inicio),
    INDEX idx_encadenamiento_conductor_fecha (driver_document_number, inicio)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rutas_inferidas (
    route_id VARCHAR(64) NOT NULL,
    nombre VARCHAR(160) NOT NULL,
    origen VARCHAR(160) NULL,
    destino VARCHAR(160) NULL,
    geometria_json MEDIUMTEXT NOT NULL,
    geometry_source VARCHAR(40) NOT NULL DEFAULT 'GPS_TRACE_FILTERED',
    map_matching_estado VARCHAR(24) NOT NULL DEFAULT 'pendiente',
    cantidad_viajes INT UNSIGNED NOT NULL DEFAULT 0,
    cantidad_vehiculos INT UNSIGNED NOT NULL DEFAULT 0,
    cantidad_conductores INT UNSIGNED NOT NULL DEFAULT 0,
    distancia_promedio_km DOUBLE NOT NULL DEFAULT 0,
    duracion_promedio_minutos DOUBLE NOT NULL DEFAULT 0,
    paradas_promedio DOUBLE NOT NULL DEFAULT 0,
    usage_level ENUM('HIGH','MEDIUM','LOW') NOT NULL DEFAULT 'LOW',
    color CHAR(7) NOT NULL DEFAULT '#dc2626',
    version_datos CHAR(64) NOT NULL,
    actualizado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (route_id),
    INDEX idx_rutas_nivel (usage_level, cantidad_viajes),
    INDEX idx_rutas_version (version_datos)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS viajes_telemetria (
    viaje_id CHAR(36) NOT NULL,
    encadenamiento_id CHAR(36) NULL,
    route_id VARCHAR(64) NULL,
    asset_id VARCHAR(16) NOT NULL,
    asset_name VARCHAR(64) NULL,
    driver_document_number VARCHAR(32) NULL,
    driver_name VARCHAR(64) NULL,
    driver_last_name VARCHAR(128) NULL,
    inicio DATETIME(6) NOT NULL,
    termino DATETIME(6) NOT NULL,
    origen VARCHAR(160) NULL,
    destino VARCHAR(160) NULL,
    distancia_km DOUBLE NOT NULL DEFAULT 0,
    duracion_minutos DOUBLE NOT NULL DEFAULT 0,
    velocidad_promedio DOUBLE NOT NULL DEFAULT 0,
    velocidad_maxima DOUBLE NOT NULL DEFAULT 0,
    puntos_gps INT UNSIGNED NOT NULL DEFAULT 0,
    cantidad_paradas INT UNSIGNED NOT NULL DEFAULT 0,
    tiempo_detenido_minutos DOUBLE NOT NULL DEFAULT 0,
    geometria_json MEDIUMTEXT NOT NULL,
    estado VARCHAR(24) NOT NULL,
    version_datos CHAR(64) NOT NULL,
    actualizado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (viaje_id),
    INDEX idx_viaje_asset_fecha (asset_id, inicio),
    INDEX idx_viaje_conductor_fecha (driver_document_number, inicio),
    INDEX idx_viaje_ruta_fecha (route_id, inicio),
    INDEX idx_viaje_encadenamiento (encadenamiento_id, inicio),
    CONSTRAINT fk_viaje_ruta FOREIGN KEY (route_id) REFERENCES rutas_inferidas(route_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS paradas_telemetria (
    parada_id CHAR(36) NOT NULL,
    viaje_id CHAR(36) NULL,
    asset_id VARCHAR(16) NOT NULL,
    driver_document_number VARCHAR(32) NULL,
    inicio DATETIME(6) NOT NULL,
    termino DATETIME(6) NOT NULL,
    duracion_minutos DOUBLE NOT NULL,
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    zona VARCHAR(160) NULL,
    tipo VARCHAR(32) NOT NULL,
    anomala BOOLEAN NOT NULL DEFAULT 0,
    actualizado_en DATETIME(6) NOT NULL,
    PRIMARY KEY (parada_id),
    INDEX idx_parada_asset_fecha (asset_id, inicio),
    INDEX idx_parada_viaje (viaje_id, inicio),
    INDEX idx_parada_coordenadas (latitude, longitude),
    CONSTRAINT fk_parada_viaje FOREIGN KEY (viaje_id) REFERENCES viajes_telemetria(viaje_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS modelo_rutas_estado (
    id TINYINT NOT NULL,
    version_datos CHAR(64) NOT NULL,
    version_modelo VARCHAR(64) NOT NULL,
    algoritmo VARCHAR(120) NOT NULL,
    parametros_json TEXT NOT NULL,
    metricas_json TEXT NOT NULL,
    rango_desde DATETIME(6) NULL,
    rango_hasta DATETIME(6) NULL,
    registros_entrenamiento BIGINT UNSIGNED NOT NULL DEFAULT 0,
    artefacto VARCHAR(255) NULL,
    estado VARCHAR(24) NOT NULL,
    ultimo_error VARCHAR(255) NULL,
    entrenado_en DATETIME(6) NULL,
    ultima_revision DATETIME(6) NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS realtime_estado (
    id TINYINT NOT NULL,
    origen VARCHAR(255) NOT NULL,
    ultimo_report_date DATETIME(6) NULL,
    ultimo_report_id VARCHAR(128) NULL,
    leidos BIGINT UNSIGNED NOT NULL DEFAULT 0,
    validos BIGINT UNSIGNED NOT NULL DEFAULT 0,
    rechazados BIGINT UNSIGNED NOT NULL DEFAULT 0,
    duplicados BIGINT UNSIGNED NOT NULL DEFAULT 0,
    estado VARCHAR(24) NOT NULL,
    ultimo_error VARCHAR(255) NULL,
    ultima_carga DATETIME(6) NULL,
    ultima_revision DATETIME(6) NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
