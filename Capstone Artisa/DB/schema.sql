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
