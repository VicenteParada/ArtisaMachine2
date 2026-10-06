-- Catálogo inicial de vehículos (origen: patentes.csv, recuperado del historial del repo).
-- Lo aplica common.db.initialize_db() después de DB/schema.sql. Idempotente y seguro
-- sobre la base persistente: solo agrega lo que falta y nunca sobrescribe una
-- clasificación existente (para corregir una, usa UPDATE vehiculos).
-- Los tipos se resuelven por nombre, no por id, por si la tabla heredada numera distinto.

INSERT INTO tipos_vehiculo (nombre) SELECT 'camion' FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM tipos_vehiculo WHERE nombre = 'camion');
INSERT INTO tipos_vehiculo (nombre) SELECT 'camion_pequeño' FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM tipos_vehiculo WHERE nombre = 'camion_pequeño');
INSERT INTO tipos_vehiculo (nombre) SELECT 'camioneta' FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM tipos_vehiculo WHERE nombre = 'camioneta');
INSERT INTO tipos_vehiculo (nombre) SELECT 'minibus' FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM tipos_vehiculo WHERE nombre = 'minibus');

-- camion: 113 vehículos
SET @id_tipo := (SELECT id_tipo FROM tipos_vehiculo WHERE nombre = 'camion' ORDER BY id_tipo LIMIT 1);
INSERT IGNORE INTO vehiculos (asset_id, id_tipo, actualizado_en) VALUES
    ('RJXS30', @id_tipo, UTC_TIMESTAMP(6)),
    ('RJXS31', @id_tipo, UTC_TIMESTAMP(6)),
    ('RJXS32', @id_tipo, UTC_TIMESTAMP(6)),
    ('RJXS36', @id_tipo, UTC_TIMESTAMP(6)),
    ('RJXS37', @id_tipo, UTC_TIMESTAMP(6)),
    ('RJXS38', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRSP40', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRSP41', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRSP42', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRSP43', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRSP47', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRSP48', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRSS86', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRSS87', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRZJ23', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRZJ24', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRZJ25', @id_tipo, UTC_TIMESTAMP(6)),
    ('RRZJ26', @id_tipo, UTC_TIMESTAMP(6)),
    ('RVZB91', @id_tipo, UTC_TIMESTAMP(6)),
    ('RVZB93', @id_tipo, UTC_TIMESTAMP(6)),
    ('RVZB94', @id_tipo, UTC_TIMESTAMP(6)),
    ('RVZB95', @id_tipo, UTC_TIMESTAMP(6)),
    ('RVZB96', @id_tipo, UTC_TIMESTAMP(6)),
    ('RVZB97', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC78', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC84', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC85', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC86', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC87', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC88', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC89', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC90', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC91', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC92', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC93', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC94', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC95', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC96', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSC97', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSD28', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSD29', @id_tipo, UTC_TIMESTAMP(6)),
    ('RWSD30', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV10', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV11', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV12', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV13', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV14', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV15', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV16', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV17', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV18', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV29', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV57', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV58', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV59', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV60', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV61', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV62', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV63', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV64', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV66', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV67', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV68', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV71', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV73', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV74', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV75', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV76', @id_tipo, UTC_TIMESTAMP(6)),
    ('RYCV77', @id_tipo, UTC_TIMESTAMP(6)),
    ('SBKT91', @id_tipo, UTC_TIMESTAMP(6)),
    ('SHKZ50', @id_tipo, UTC_TIMESTAMP(6)),
    ('SVFD23', @id_tipo, UTC_TIMESTAMP(6)),
    ('SVFD25', @id_tipo, UTC_TIMESTAMP(6)),
    ('SVFD28', @id_tipo, UTC_TIMESTAMP(6)),
    ('SVFD29', @id_tipo, UTC_TIMESTAMP(6)),
    ('SVFD30', @id_tipo, UTC_TIMESTAMP(6)),
    ('SWDL36', @id_tipo, UTC_TIMESTAMP(6)),
    ('SWDL37', @id_tipo, UTC_TIMESTAMP(6)),
    ('SWDL55', @id_tipo, UTC_TIMESTAMP(6)),
    ('SWDL56', @id_tipo, UTC_TIMESTAMP(6)),
    ('SWDL58', @id_tipo, UTC_TIMESTAMP(6)),
    ('SWDL59', @id_tipo, UTC_TIMESTAMP(6)),
    ('SWDL61', @id_tipo, UTC_TIMESTAMP(6)),
    ('SWDL62', @id_tipo, UTC_TIMESTAMP(6)),
    ('SXLS15', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK15', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK16', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK17', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK18', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK19', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK20', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK21', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK22', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK23', @id_tipo, UTC_TIMESTAMP(6)),
    ('TFWK38', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFS58', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFS61', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFS64', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFS68', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFS87', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFS94', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFT93', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFT94', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFT95', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFT96', @id_tipo, UTC_TIMESTAMP(6)),
    ('TGFT97', @id_tipo, UTC_TIMESTAMP(6)),
    ('TJZJ26', @id_tipo, UTC_TIMESTAMP(6)),
    ('TJZJ27', @id_tipo, UTC_TIMESTAMP(6)),
    ('TJZJ28', @id_tipo, UTC_TIMESTAMP(6)),
    ('TJZK87', @id_tipo, UTC_TIMESTAMP(6)),
    ('TJZL14', @id_tipo, UTC_TIMESTAMP(6)),
    ('TJZL19', @id_tipo, UTC_TIMESTAMP(6)),
    ('TJZL21', @id_tipo, UTC_TIMESTAMP(6));

-- camion_pequeño: 1 vehículos
SET @id_tipo := (SELECT id_tipo FROM tipos_vehiculo WHERE nombre = 'camion_pequeño' ORDER BY id_tipo LIMIT 1);
INSERT IGNORE INTO vehiculos (asset_id, id_tipo, actualizado_en) VALUES
    ('LZPY94', @id_tipo, UTC_TIMESTAMP(6));

-- camioneta: 9 vehículos
SET @id_tipo := (SELECT id_tipo FROM tipos_vehiculo WHERE nombre = 'camioneta' ORDER BY id_tipo LIMIT 1);
INSERT IGNORE INTO vehiculos (asset_id, id_tipo, actualizado_en) VALUES
    ('LCSS42', @id_tipo, UTC_TIMESTAMP(6)),
    ('LCSS52', @id_tipo, UTC_TIMESTAMP(6)),
    ('LVGJ25', @id_tipo, UTC_TIMESTAMP(6)),
    ('LXJW25', @id_tipo, UTC_TIMESTAMP(6)),
    ('RGFK68', @id_tipo, UTC_TIMESTAMP(6)),
    ('RGFK71', @id_tipo, UTC_TIMESTAMP(6)),
    ('SBKT94', @id_tipo, UTC_TIMESTAMP(6)),
    ('SBKT96', @id_tipo, UTC_TIMESTAMP(6)),
    ('VWWH15', @id_tipo, UTC_TIMESTAMP(6));

-- minibus: 1 vehículos
SET @id_tipo := (SELECT id_tipo FROM tipos_vehiculo WHERE nombre = 'minibus' ORDER BY id_tipo LIMIT 1);
INSERT IGNORE INTO vehiculos (asset_id, id_tipo, actualizado_en) VALUES
    ('GCFX14', @id_tipo, UTC_TIMESTAMP(6));
