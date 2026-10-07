"""Revisión de la clasificación de rutas con los datos reales.

Dentro del contenedor (no carga el PC más que el propio cálculo):
    docker compose exec machine-diario python -m machine_learning.validar_rutas
    docker compose exec machine-diario python -m machine_learning.validar_rutas --ahora
    docker compose exec machine-diario python -m machine_learning.validar_rutas --geojson > rutas.geojson

--ahora     recalcula y publica una versión nueva antes de mostrarla.
--geojson   imprime las rutas activas como GeoJSON (se puede abrir en geojson.io o QGIS).
"""

import json
import sys

from common.db import connect_db
from machine_learning import rutas_job


def main(argv):
    with connect_db(autocommit=False, read_timeout=900) as connection:
        if "--ahora" in argv:
            rutas_job.ejecutar(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM rutas_version WHERE activa=1 ORDER BY id DESC LIMIT 1")
            version = cursor.fetchone()
            if not version:
                print("Todavía no hay una clasificación activa. Requiere días cerrados con viajes "
                      "(usa --ahora para intentar calcularla ya).")
                return 1
            cursor.execute("SELECT * FROM rutas_clasificadas WHERE version_id=%s ORDER BY usage_count DESC, route_id",
                           (version["id"],))
            rutas = cursor.fetchall()
    metricas = json.loads(version["metricas_json"])
    lugares = json.loads(version["lugares_json"])
    if "--geojson" in argv:
        print(json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": json.loads(r["geometria"]),
             "properties": {k: r[k] for k in ("route_id", "label", "usage_count", "unique_vehicle_count",
                                              "usage_level", "km_mediana", "geometry_source")}}
            for r in rutas]}, ensure_ascii=False))
        return 0
    print(f"Versión {version['id']} · viajes del {version['desde']} al {version['hasta']} · creada {version['creado_en']} UTC")
    print(f"Viajes leídos: {metricas.get('viajes_leidos')} · clasificados: {metricas.get('viajes_clasificados')} · "
          f"sin origen/destino: {metricas.get('viajes_sin_origen_o_destino')} · cortos: {metricas.get('viajes_cortos')}")
    print(f"Camiones: {metricas.get('camiones')} · lugares: {metricas.get('lugares')} · "
          f"pares origen-destino: {metricas.get('pares_origen_destino')} · rutas: {metricas.get('rutas')}")
    print(f"Por nivel: {metricas.get('por_nivel')} · rutas de un solo viaje: {metricas.get('rutas_de_un_solo_viaje')}")
    print(f"Umbrales: {metricas.get('umbrales')}")
    print(f"Parámetros: {metricas.get('parametros')} · cálculo: {metricas.get('segundos')} s")
    print("\nRutas más usadas:")
    for r in rutas[:15]:
        print(f"  {r['route_id']}  {r['usage_level']:<6} {r['usage_count']:>5} recorridos  "
              f"{r['unique_vehicle_count']:>3} camiones  {r['km_mediana']:>7.1f} km  {r['label']}")
    print("\nLugares con más llegadas y salidas:")
    for lugar in sorted(lugares.values(), key=lambda l: -l["extremos"])[:10]:
        print(f"  {lugar['clave']:<10} {lugar['nombre']:<28} {lugar['extremos']:>6}  "
              f"({lugar['latitud']}, {lugar['longitud']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
