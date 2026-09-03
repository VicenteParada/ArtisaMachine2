"""Extrae CSV, normaliza valores y registra filas descartadas sin detener la carga."""

import csv
import hashlib
import io
import re

from common.validation import features, number


HEADERS = {"id", "origen", "destino", "distancia_km", "carga_kg", "paradas", "duracion_horas"}


def transform(raw):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if not HEADERS.issubset(reader.fieldnames or []):
        raise ValueError("El CSV no contiene las columnas requeridas")
    accepted, rejected, seen = [], [], set()
    for row_number, row in enumerate(reader, start=2):
        try:
            identifier = (row.get("id") or "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9_-]{1,32}", identifier):
                raise ValueError("id ausente o invalido")
            if identifier in seen:
                raise ValueError("id duplicado en el origen")
            cities = {}
            for field in ("origen", "destino"):
                value = " ".join((row.get(field) or "").split()).title()
                if not 1 <= len(value) <= 80:
                    raise ValueError(f"{field}: nombre vacio o demasiado largo")
                cities[field] = value
            accepted.append({
                "id": identifier, **cities, **features(row),
                "duracion_horas": number(row.get("duracion_horas"), "duracion_horas", 0.01, 100),
            })
            seen.add(identifier)
        except ValueError as error:
            rejected.append({"fila": row_number, "motivo": str(error)})
    if not accepted:
        raise ValueError("El origen no contiene viajes validos; se conserva la carga anterior")
    return accepted, rejected, hashlib.sha256(raw).hexdigest()
