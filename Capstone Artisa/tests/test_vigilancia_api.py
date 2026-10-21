"""Camiones en vivo sobre las rutas aprendidas: nivel de cada posición y alertas en N5/N6."""

from datetime import date, datetime, timedelta
import json
import time
import unittest
from unittest.mock import patch

from endpoints import vigilancia_api as v
from tests.test_rutas_api import FakeCursor

AHORA = datetime(2026, 10, 19, 15, 0)
VERSION = {"id": 9, "desde": date(2026, 7, 12), "hasta": date(2026, 10, 9), "activa": 1,
           "creado_en": datetime(2026, 10, 10, 1, 0),
           "metricas_json": json.dumps({"viajes_clasificados": 300, "camiones": 20, "lugares": 4,
                                        "parametros": {"metodo": "camiones-jenks-v1"},
                                        "umbrales": {"cortes": [{"nivel": "N1", "desde": 15, "hasta": 20},
                                                                {"nivel": "N2", "desde": 3, "hasta": 14},
                                                                {"nivel": "N5", "desde": 1, "hasta": 2}]}}),
           "lugares_json": "{}"}


def linea(lon, lat0=-22.40, lat1=-22.60):
    return json.dumps({"type": "LineString", "coordinates": [[lon, lat0], [lon, lat1]]})


RUTAS = [
    {"route_id": "R-TRONCAL", "label": "Faena Norte ↔ Calama", "usage_level": "N1", "usage_count": 280,
     "unique_vehicle_count": 20, "km_mediana": 22.0, "geometria": linea(-68.90),
     "primer_viaje": datetime(2026, 7, 12), "ultimo_viaje": datetime(2026, 10, 9)},
    {"route_id": "R-MINIMA", "label": "Calama ↔ Punto aislado 3", "usage_level": "N5", "usage_count": 2,
     "unique_vehicle_count": 1, "km_mediana": 22.0, "geometria": linea(-68.70),
     "primer_viaje": datetime(2026, 8, 1), "ultimo_viaje": datetime(2026, 8, 2)},
]


def camino(asset_id, inicio, minutos, lat0=-22.40, lat1=-22.60, lon=-68.90, speed=60.0):
    """Reportes cada minuto en línea recta (norte a sur)."""
    return [{"asset_id": asset_id, "report_date": inicio + timedelta(minutes=k),
             "latitude": lat0 + (lat1 - lat0) * k / max(1, minutos - 1), "longitude": lon, "speed": speed}
            for k in range(minutos)]


def flota(desvio_lon=-68.50, termina=AHORA, minutos=12):
    """20 camiones por la troncal; T19 sale de ella y sigue por desvio_lon hasta `termina`."""
    filas = []
    for k in range(20):
        filas += camino(f"T{k:02d}", AHORA - timedelta(days=2, hours=k), 30)
    return filas + camino("T19", termina - timedelta(minutes=minutos - 1), minutos, lat0=-22.50, lat1=-22.52,
                          lon=desvio_lon)


def cursor_con(filas, faenas=(), version=VERSION):
    catalogo = [{"asset_id": f"T{k:02d}"} for k in range(20)]
    return FakeCursor([
        ("FROM rutas_version", lambda _: [dict(version)] if version else []),
        ("FROM rutas_clasificadas", RUTAS),
        ("WHERE t.nombre = 'camion'", catalogo),
        ("FROM raw_gps USE INDEX", []),
        ("FROM telemetria_api USE INDEX", filas),
        ("FROM faenas", list(faenas)),
        ("FROM telemetria_api\n                    WHERE asset_id=%s", [{"asset_name": "Lampa-919", "driver_name": "JUAN",
                                                                      "driver_last_name": "PÉREZ", "location": "Sierra Gorda"}]),
    ])


class ModeloTests(unittest.TestCase):
    def setUp(self):
        v._CACHE.update(version=None, modelo=None, mapa=None)

    def test_learned_routes_with_levels_and_table(self):
        data = v.rutas_aprendidas(cursor_con([]))
        self.assertEqual([r["id"] for r in data["rutas"]], ["R-TRONCAL", "R-MINIMA"])
        self.assertEqual(data["total_rutas"], 2)
        self.assertEqual(data["rutas"][0]["lineas"], [[[-22.4, -68.9], [-22.6, -68.9]]])
        tabla = {n["nivel"]: n for n in data["niveles"]}
        self.assertEqual(list(tabla), ["N1", "N2", "N5", "N6"])   # N3/N4: el modelo aprendió 2 grupos
        self.assertEqual((tabla["N1"]["desde"], tabla["N1"]["hasta"], tabla["N1"]["rutas"]), (15, 20, 1))
        self.assertEqual([n["nivel"] for n in data["niveles"] if n["alerta"]], ["N5", "N6"])
        self.assertEqual((data["datos_hasta"], data["proxima_con_datos_hasta"]), ("2026-10-09", "2026-10-16"))

    def test_shared_roads_are_drawn_once_with_the_best_level(self):
        # Una ruta mínima que va por la misma carretera que la troncal no agrega otra línea: cada
        # tramo aparece una vez, con el mejor nivel, y dice qué rutas lo usan (la troncal primero).
        global RUTAS
        original = RUTAS
        RUTAS = original + [dict(original[1], route_id="R-MINIMA-2", geometria=linea(-68.90))]
        try:
            data = v.rutas_aprendidas(cursor_con([]))
        finally:
            RUTAS = original
        red = data["red"]
        compartidos = [t for t in red["tramos"] if t[3] == 2]
        self.assertTrue(compartidos)
        self.assertTrue(all(t[2] == 1 and t[4] == [0, 2] for t in compartidos))
        pares = {(min(a, b), max(a, b)) for a, b, *_ in red["tramos"]}
        self.assertEqual(len(pares), len(red["tramos"]))   # sin tramos repetidos
        self.assertTrue(all(abs(lon + 68.90) < 1e-6 or abs(lon + 68.70) < 1e-6 for _, lon in red["nodos"]))

    def test_position_level_uses_the_cell_and_its_neighbors(self):
        _, mapa = v.leer_modelo(cursor_con([]))
        self.assertEqual(mapa.en(-22.5, -68.9), (1, "R-TRONCAL"))
        self.assertEqual(mapa.en(-22.5, -68.9 + 1 / v.ESCALA)[0], 1)     # celda vecina
        self.assertEqual(mapa.en(-22.5, -68.70), (5, "R-MINIMA"))
        self.assertEqual(mapa.en(-22.5, -68.50), (6, None))               # fuera de ruta
        self.assertAlmostEqual(mapa.distancia(-22.5, -68.5), 41.1, delta=1.5)

    def test_pending_without_a_learned_model(self):
        self.assertIsNone(v.rutas_aprendidas(cursor_con([], version=None)))
        self.assertIsNone(v.vigilancia(cursor_con(flota(), version=None), AHORA))


class AlertasTests(unittest.TestCase):
    def setUp(self):
        v._CACHE.update(version=None, modelo=None, mapa=None)
        posiciones = [{"vehicle_id": "T19", "latitude": -22.52, "longitude": -68.50,
                       "timestamp": (AHORA - timedelta(minutes=1)).isoformat() + "Z"},
                      {"vehicle_id": "T00", "latitude": -22.5, "longitude": -68.9,
                       "timestamp": (AHORA - timedelta(minutes=2)).isoformat() + "Z"},
                      {"vehicle_id": "T01", "latitude": -22.5, "longitude": -68.9,
                       "timestamp": (AHORA - timedelta(days=1)).isoformat() + "Z"}]
        self.vehiculos = patch.object(v.rutas_api, "latest_positions", return_value=posiciones)
        self.vehiculos.start()

    def tearDown(self):
        self.vehiculos.stop()

    def test_truck_off_every_learned_route_raises_an_active_alert(self):
        data = v.vigilancia(cursor_con(flota()), AHORA)
        alerta, = data["alertas"]
        self.assertEqual((alerta["patente"], alerta["estado"], alerta["nivel"], alerta["reportes"]),
                         ("T19", "activa", "N6", 12))
        self.assertAlmostEqual(alerta["km_al_corredor"], 41, delta=2)
        self.assertAlmostEqual(alerta["corredor"]["punto"][1], -68.9, delta=0.01)  # sobre la troncal
        self.assertEqual((alerta["nombre"], alerta["conductor"], alerta["ubicacion"]),
                         ("Lampa-919", "JUAN PÉREZ", "Sierra Gorda"))
        self.assertEqual(data["resumen"]["activas"], 1)
        self.assertEqual(data["modelo"]["version"], 9)
        self.assertNotIn("rutas", data["modelo"])  # las líneas van en /api/rutas-aprendidas
        t19, t00, t01 = data["vehiculos"]
        self.assertEqual((t19["en_alerta"], t19["nivel"]), (True, "N6"))
        self.assertEqual((t00["en_alerta"], t00["nivel"], t00["ruta_label"]), (False, "N1", "Faena Norte ↔ Calama"))
        self.assertIsNone(t01["nivel"])  # posición de ayer: no se clasifica
        self.assertEqual(data["resumen"]["camiones_por_nivel"], {"N6": 1, "N1": 1})

    def test_truck_on_a_minimal_route_raises_an_alert_with_that_route(self):
        alerta, = v.vigilancia(cursor_con(flota(desvio_lon=-68.70)), AHORA)["alertas"]
        self.assertEqual((alerta["nivel"], alerta["ruta"], alerta["ruta_label"]),
                         ("N5", "R-MINIMA", "Calama ↔ Punto aislado 3"))

    def test_old_episode_without_new_reports_is_without_signal(self):
        data = v.vigilancia(cursor_con(flota(termina=AHORA - timedelta(hours=2))), AHORA)
        self.assertEqual(data["alertas"][0]["estado"], "sin_senal")

    def test_back_on_a_usual_route_closes_the_alert(self):
        filas = flota(termina=AHORA - timedelta(minutes=30))
        filas += camino("T19", AHORA - timedelta(minutes=10), 5, lat0=-22.45, lat1=-22.46)
        self.assertEqual(v.vigilancia(cursor_con(filas), AHORA)["alertas"][0]["estado"], "terminada")

    def test_inside_a_worksite_there_is_no_alert(self):
        faena = {"nombre": "Faena Este", "geojson": json.dumps({"type": "Polygon", "coordinates": [[
            [-68.6, -22.6], [-68.4, -22.6], [-68.4, -22.4], [-68.6, -22.4], [-68.6, -22.6]]]})}
        self.assertEqual(v.vigilancia(cursor_con(flota(), [faena]), AHORA)["alertas"], [])

    def test_short_stopped_or_nearby_detours_are_not_alerts(self):
        self.assertEqual(v.vigilancia(cursor_con(flota(minutos=8)), AHORA)["alertas"], [])   # 8 min
        detenido = [dict(f, speed=0.0) if f["longitude"] == -68.50 else f for f in flota()]
        self.assertEqual(v.vigilancia(cursor_con(detenido), AHORA)["alertas"], [])
        # 12 minutos fuera, pero a ~1,5 km de la troncal.
        self.assertEqual(v.vigilancia(cursor_con(flota(desvio_lon=-68.885)), AHORA)["alertas"], [])

    def test_a_week_of_the_whole_fleet_is_fast(self):
        # ~100 camiones reportando cada minuto ~2,7 h al día durante 7 días, sobre 300 rutas.
        global RUTAS
        original = RUTAS
        RUTAS = original + [dict(original[0], route_id=f"R{k}", usage_level="N2", geometria=linea(-69.0 - k * 0.01))
                            for k in range(300)]
        try:
            filas = []
            for k in range(100):
                for dia in range(7):
                    filas += camino(f"T{k % 20:02d}" if k < 20 else f"X{k}", AHORA - timedelta(days=dia, hours=8), 160,
                                    lat0=-22.0, lat1=-23.5, lon=-68.9 - (k % 10) * 0.05)
            comienzo = time.monotonic()
            v.vigilancia(cursor_con(filas), AHORA)
            self.assertLess(time.monotonic() - comienzo, 20)
        finally:
            RUTAS = original


if __name__ == "__main__":
    unittest.main()
