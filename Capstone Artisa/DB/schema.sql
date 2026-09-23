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
