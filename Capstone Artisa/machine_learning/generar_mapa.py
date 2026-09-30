"""Mapa base con las faenas marcadas, para ir agregando encima las rutas de
camiones cuando exista el matching faena<->viaje. Por ahora solo dibuja las
faenas (poligono real, leido de MariaDB); llamar a agregar_rutas_camiones
mas adelante, cuando haya datos de rutas que dibujar.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import folium

from common.db import connect_db


SALIDA = Path(__file__).resolve().parent / "mapa_operativo.html"


def cargar_faenas(cursor):
    cursor.execute(
        "SELECT id, nombre, area_m2, ST_AsText(geocerca) AS poligono_wkt FROM faenas ORDER BY id"
    )
    faenas = []
    for row in cursor.fetchall():
        # 'POLYGON((lon lat,lon lat,...))' -> [(lat, lon), ...] en el orden que espera folium
        puntos_lonlat = row["poligono_wkt"][len("POLYGON(("):-2].split(",")
        puntos = [(float(lat), float(lon)) for lon, lat in
                  (par.strip().split(" ") for par in puntos_lonlat)]
        faenas.append({"id": row["id"], "nombre": row["nombre"], "area_m2": row["area_m2"], "puntos": puntos})
    return faenas


def construir_mapa_base(faenas):
    if not faenas:
        raise ValueError("No hay faenas en la base de datos")
    todos_los_puntos = [punto for faena in faenas for punto in faena["puntos"]]
    centro_lat = sum(lat for lat, _ in todos_los_puntos) / len(todos_los_puntos)
    centro_lon = sum(lon for _, lon in todos_los_puntos) / len(todos_los_puntos)

    # "OpenStreetMap" y "CartoDB positron" (los tiles por defecto de folium) ahora
    # piden registro/API key para uso embebido. Esri no pide nada de eso.
    mapa = folium.Map(
        location=[centro_lat, centro_lon],
        zoom_start=9,
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
    )
    for faena in faenas:
        etiqueta = f"{faena['nombre']}<br>area: {faena['area_m2']:.0f} m2"
        folium.Polygon(
            locations=faena["puntos"],
            color="green",
            weight=2,
            fill=True,
            fill_opacity=0.2,
            popup=etiqueta,
        ).add_to(mapa)
        # El poligono se achica hasta desaparecer al alejar el zoom; el marcador
        # mantiene el mismo tamano en pantalla sin importar el nivel de zoom.
        centro_faena_lat = sum(lat for lat, _ in faena["puntos"]) / len(faena["puntos"])
        centro_faena_lon = sum(lon for _, lon in faena["puntos"]) / len(faena["puntos"])
        folium.Marker(
            [centro_faena_lat, centro_faena_lon],
            tooltip=faena["nombre"],
            popup=etiqueta,
            icon=folium.Icon(color="green", icon="industry", prefix="fa"),
        ).add_to(mapa)
    return mapa


def agregar_rutas_camiones(mapa, viajes):
    """Placeholder: dibuja cada viaje de camion como una polilinea sobre el mapa base.
    Se completa cuando exista el matching faena<->viaje (origen/destino por geocerca)."""
    raise NotImplementedError("Falta el matching de viajes por faena")


if __name__ == "__main__":
    with connect_db() as connection, connection.cursor() as cursor:
        faenas = cargar_faenas(cursor)
    mapa = construir_mapa_base(faenas)
    SALIDA.parent.mkdir(exist_ok=True)
    mapa.save(str(SALIDA))
    print(f"OK: {len(faenas)} faenas dibujadas -> {SALIDA}")
