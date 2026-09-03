"""Conexiones independientes por operación; credenciales solo desde el entorno."""

import os
from pathlib import Path

import pymysql


def connect_db(autocommit=True):
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "mariadb"),
        user=os.environ.get("DB_USER", "artisa_app"),
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "artisa"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=autocommit,
        connect_timeout=3,
        read_timeout=5,
        write_timeout=5,
    )


def initialize_db():
    # El esquema contiene únicamente CREATE TABLE IF NOT EXISTS, sin borrar datos.
    schema = Path(__file__).resolve().parent.parent / "DB" / "schema.sql"
    with connect_db() as connection, connection.cursor() as cursor:
        for statement in schema.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                cursor.execute(statement)

