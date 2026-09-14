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
