"""Conexiones independientes por operación; credenciales solo desde el entorno."""

import os
from pathlib import Path

import pymysql


def connect_db(autocommit=True, read_timeout=15):
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "mariadb"),
        user=os.environ.get("DB_USER", "artisa_app"),
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "artisa"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=autocommit,
        connect_timeout=3,
        read_timeout=read_timeout,
        write_timeout=5,
    )


def _statements(path):
    # Los archivos no usan ";" dentro de comentarios ni de textos.
    for statement in path.read_text(encoding="utf-8").split(";"):
        code = "\n".join(line for line in statement.splitlines() if not line.strip().startswith("--"))
        if code.strip():
            yield statement


def initialize_db():
    # El esquema contiene únicamente CREATE TABLE IF NOT EXISTS, sin borrar datos.
    # El catálogo de vehículos solo agrega filas faltantes (INSERT IGNORE / NOT EXISTS).
    db_dir = Path(__file__).resolve().parent.parent / "DB"
    with connect_db() as connection, connection.cursor() as cursor:
        for name in ("schema.sql", "vehiculos_data.sql"):
            path = db_dir / name
            if path.exists():
                for statement in _statements(path):
                    cursor.execute(statement)

