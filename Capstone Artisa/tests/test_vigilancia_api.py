"""Vigilancia de 7 días: huella por % de la flota y alertas por zonas poco habituales."""

from datetime import datetime, timedelta
import json
import time
import unittest
from unittest.mock import patch

from endpoints import vigilancia_api as v
from tests.test_rutas_api import FakeCursor

AHORA = datetime(2026, 10, 19, 15, 0)


def camino(asset_id, inicio, minutos, lat0=-22.40, lat1=-22.60, lon=-68.90, speed=60.0):
    """Reportes cada minuto en línea recta (norte a sur)."""
    return [{"asset_id": asset_id, "report_date": inicio + timedelta(minutes=k),
             "latitude": lat0 + (lat1 - lat0) * k / max(1, minutos - 1), "longitude": lon, "speed": speed}
            for k in range(minutos)]


def flota_con_desvio(desvio_lon=-68.50, termina=AHORA):
    """20 camiones por el camino habitual; el último sale 40 km al este y sigue allí."""
    filas = []
    for k in range(20):
        filas += camino(f"T{k:02d}", AHORA - timedelta(days=2, hours=k), 30)
    desvio = camino("T19", termina - timedelta(minutes=9), 10, lat0=-22.50, lat1=-22.52, lon=desvio_lon)
    return filas + desvio


def cursor_con(filas, faenas=()):
    catalogo = [{"asset_id": f"T{k:02d}"} for k in range(20)]
    return FakeCursor([
        ("FROM vehiculos AS v INNER JOIN tipos_vehiculo AS t ON t.id_tipo = v.id_tipo\n           WHERE t.nombre = 'camion'\"", catalogo),
        ("WHERE t.nombre = 'camion'", catalogo),
        ("FROM raw_gps USE INDEX", []),
        ("FROM telemetria_api USE INDEX", filas),
        ("FROM faenas", list(faenas)),
        ("FROM telemetria_api\n                    WHERE asset_id=%s", [{"asset_name": "Lampa-919", "driver_name": "JUAN",
                                                                      "driver_last_name": "PÉREZ", "location": "Sierra Gorda"}]),
    ])


class HuellaTests(unittest.TestCase):
    def test_road_cells_are_continuous_between_reports(self):
        # Dos reportes a 2 km (un minuto a 120 km/h): las celdas intermedias también cuentan.
        puntos = {"A": [(AHORA, -22.40, -68.90, 120.0), (AHORA + timedelta(minutes=1), -22.418, -68.90, 120.0)]}
        conteo, propias = v.huella(puntos)
        self.assertEqual(propias["A"], set(conteo))
        self.assertGreaterEqual(len(conteo), 4)
        self.assertEqual(set(conteo.values()), {1})

    def test_too_many_cells_are_drawn_coarser(self):
        conteo = {(i, 0): 1 for i in range(10)} | {(1, 1): 7}
        with patch.object(v, "MAX_CELDAS", 6):
            escala, celdas = v.celdas_mapa(conteo)
        self.assertEqual(escala, v.ESCALA // 2)
        self.assertIn([0, 0, 7], celdas)      # la celda gruesa conserva el mayor conteo
        self.assertEqual(len(celdas), 5)

    def test_gps_jumps_are_dropped(self):
        filas = camino("T00", AHORA - timedelta(hours=1), 3, lat1=-22.42)
        filas.insert(1, {"asset_id": "T00", "report_date": filas[0]["report_date"] + timedelta(seconds=30),
                         "latitude": -33.4, "longitude": -70.6, "speed": 60})  # Santiago en 30 s
        cursor = FakeCursor([("FROM raw_gps USE INDEX", []), ("FROM telemetria_api USE INDEX", filas)])
        puntos = v.leer_reportes(cursor, AHORA - timedelta(days=7), AHORA, {"T00"})["T00"]
        self.assertEqual(len(puntos), 3)
        self.assertTrue(all(p[1] > -23 for p in puntos))

    def test_percent_uses_the_cell_and_its_neighbors(self):
        zonas = v.Zonas({v.celda(-22.5, -68.9): 10}, 20)
        self.assertEqual(zonas.pct(-22.5, -68.9), 50)
        self.assertEqual(zonas.pct(-22.5 + 1 / v.ESCALA, -68.9), 50)    # celda vecina
        self.assertEqual(zonas.pct(-22.5 + 3 / v.ESCALA, -68.9), 0)     # ~1,5 km
        self.assertAlmostEqual(zonas.distancia(-22.5, -68.5), 41.1, delta=1.5)
        # Un camión no cuenta para sí mismo: en su propia celda solo ve a los demás.
        propia = v.Zonas({v.celda(-22.5, -68.5): 1}, 20)
        self.assertEqual(propia.pct(-22.5, -68.5, {v.celda(-22.5, -68.5)}), 0)


class AlertasTests(unittest.TestCase):
    def setUp(self):
        self.vehiculos = patch.object(v.rutas_api, "latest_positions",
                                      return_value=[{"vehicle_id": "T19"}, {"vehicle_id": "T00"}])
        self.vehiculos.start()

    def tearDown(self):
        self.vehiculos.stop()

    def test_truck_far_from_the_usual_corridor_raises_an_active_alert(self):
        data = v.vigilancia(cursor_con(flota_con_desvio()), AHORA)
        self.assertEqual(data["camiones"], 20)
        alerta, = data["alertas"]
        self.assertEqual((alerta["patente"], alerta["estado"], alerta["reportes"]), ("T19", "activa", 10))
        self.assertAlmostEqual(alerta["km_al_corredor"], 41, delta=2)
        self.assertAlmostEqual(alerta["corredor"]["km"], 41, delta=2)
        self.assertAlmostEqual(alerta["corredor"]["punto"][1], -68.9, delta=0.01)  # sobre el camino habitual
        self.assertEqual((alerta["nombre"], alerta["conductor"], alerta["ubicacion"]),
                         ("Lampa-919", "JUAN PÉREZ", "Sierra Gorda"))
        self.assertLess(alerta["pct_zona"], v.UMBRAL_PCT)
        self.assertEqual(data["resumen"]["activas"], 1)
        self.assertEqual([x["en_alerta"] for x in data["vehiculos"]], [True, False])
        self.assertTrue(all(len(c) == 3 for c in data["celdas"]))

    def test_old_episode_without_new_reports_is_without_signal(self):
        data = v.vigilancia(cursor_con(flota_con_desvio(termina=AHORA - timedelta(hours=2))), AHORA)
        self.assertEqual(data["alertas"][0]["estado"], "sin_senal")

    def test_back_on_the_corridor_closes_the_alert(self):
        filas = flota_con_desvio(termina=AHORA - timedelta(minutes=30))
        filas += camino("T19", AHORA - timedelta(minutes=10), 5, lat0=-22.45, lat1=-22.46)
        self.assertEqual(v.vigilancia(cursor_con(filas), AHORA)["alertas"][0]["estado"], "terminada")

    def test_inside_a_worksite_there_is_no_alert(self):
        faena = {"nombre": "Faena Este", "geojson": json.dumps({"type": "Polygon", "coordinates": [[
            [-68.6, -22.6], [-68.4, -22.6], [-68.4, -22.4], [-68.6, -22.4], [-68.6, -22.6]]]})}
        self.assertEqual(v.vigilancia(cursor_con(flota_con_desvio(), [faena]), AHORA)["alertas"], [])

    def test_two_reports_or_a_stopped_truck_are_not_enough(self):
        filas = [f for f in flota_con_desvio() if not (f["asset_id"] == "T19" and f["longitude"] == -68.50)]
        filas += camino("T19", AHORA - timedelta(minutes=2), 2, lat0=-22.50, lat1=-22.51, lon=-68.50)
        self.assertEqual(v.vigilancia(cursor_con(filas), AHORA)["alertas"], [])
        detenido = [dict(f, speed=0.0) if f["longitude"] == -68.50 else f for f in flota_con_desvio()]
        self.assertEqual(v.vigilancia(cursor_con(detenido), AHORA)["alertas"], [])

    def test_pending_without_reports(self):
        self.assertIsNone(v.vigilancia(cursor_con([]), AHORA))

    def test_a_week_of_the_whole_fleet_is_fast(self):
        # ~100 camiones reportando cada 3 min durante 7 días (~340 mil reportes), como la flota real.
        filas = []
        for k in range(100):
            lon = -68.9 - (k % 10) * 0.05
            for dia in range(7):
                filas += camino(f"T{k % 20:02d}" if k < 20 else f"X{k}", AHORA - timedelta(days=dia, hours=8), 160,
                                lat0=-22.0, lat1=-23.5, lon=lon)
        catalogo = {f["asset_id"] for f in filas}
        cursor = FakeCursor([("FROM raw_gps USE INDEX", []), ("FROM telemetria_api USE INDEX", filas)])
        comienzo = time.monotonic()
        por_camion = v.leer_reportes(cursor, AHORA - timedelta(days=7), AHORA, catalogo)
        conteo, propias = v.huella(por_camion)
        zonas = v.Zonas(conteo, len(por_camion))
        for asset_id, puntos in por_camion.items():
            v.episodios(puntos, zonas, [], propias[asset_id])
        self.assertLess(time.monotonic() - comienzo, 20)


if __name__ == "__main__":
    unittest.main()
