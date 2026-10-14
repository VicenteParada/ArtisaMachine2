"""Endpoints de rutas: contrato del mapa y recorrido observado de un camión."""

from datetime import date, datetime
import json
import unittest

from endpoints import prediccion_api, rutas_api


class FakeCursor:
    def __init__(self, handlers):
        self.handlers = handlers
        self.rows = []

    def execute(self, query, params=None):
        for needle, result in self.handlers:
            if needle in query:
                self.rows = result(params) if callable(result) else result
                return
        raise AssertionError(query)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


VERSION = {"id": 4, "desde": date(2026, 7, 1), "hasta": date(2026, 9, 28), "activa": 1,
           "creado_en": datetime(2026, 10, 6),
           "metricas_json": json.dumps({"viajes_clasificados": 18, "camiones": 5,
                                        "por_nivel": {"HIGH": 1, "LOW": 1},
                                        "umbrales": {"percentil_33": 2.0, "percentil_67": 6.67}}),
           "lugares_json": "{}"}
RUTA = {"route_id": "R-ABC123", "label": "Faena Lampa ↔ Lugar 1", "origen": "F7", "destino": "L0",
        "usage_count": 12, "unique_vehicle_count": 4, "observed_points": 360, "km_mediana": 16.6,
        "usage_level": "HIGH", "geometry_source": "viaje_real_medoide",
        "geometria": json.dumps({"type": "LineString", "coordinates": [[-70.6, -33.4], [-70.6, -33.25]]}),
        "primer_viaje": datetime(2026, 7, 2), "ultimo_viaje": datetime(2026, 9, 27)}


class MapContractTests(unittest.TestCase):
    def test_pending_without_active_version(self):
        cursor = FakeCursor([("FROM rutas_version", [])])
        self.assertIsNone(rutas_api.classified_map(cursor))
        self.assertIsNone(rutas_api.classified_dashboard(cursor))

    def test_classified_map_matches_frontend_contract(self):
        cursor = FakeCursor([
            ("FROM rutas_version", lambda p: [dict(VERSION)]),
            ("FROM rutas_clasificadas WHERE", [RUTA]),
            ("FROM rutas_vehiculo", [{"asset_id": "RJXS30", "route_id": "R-ABC123"}]),
            ("FROM vehiculos", [{"asset_id": "RJXS30"}]),
            ("FROM telemetria_api WHERE asset_id", [{"asset_id": "RJXS30", "asset_name": "Lampa-826",
                                                      "report_date": datetime(2026, 10, 6, 17), "latitude": -33.3,
                                                      "longitude": -70.6, "speed": 40.0, "heading": 5,
                                                      "ignition": 1, "event_name": "Reporte"}]),
            ("FROM raw_gps WHERE asset_id", []),
            ("driver_name IS NOT NULL", [{"driver_name": "JULIO", "driver_last_name": "PÉREZ"}]),
            ("validos", [{"n": 500}]),
        ])
        payload = rutas_api.classified_map(cursor)
        for key in ("routes", "trips", "vehicles", "readings"):
            self.assertIsInstance(payload[key], list)
        self.assertNotEqual(payload["source_mode"], "PENDING_MACHINE")
        route = payload["routes"][0]
        self.assertEqual(route["geometry"]["type"], "LineString")
        self.assertEqual((route["usage_label"], route["color"]), ("Más usada", "#14532d"))
        self.assertFalse(route["map_matching"])
        vehicle = payload["vehicles"][0]
        self.assertEqual((vehicle["vehicle_id"], vehicle["route_id"], vehicle["fuente"]), ("RJXS30", "R-ABC123", "api"))
        # El último reporte no trae conductor: se usa el último informado en las 24 h previas.
        self.assertEqual((vehicle["patente"], vehicle["driver_name"], vehicle["driver_last_name"]),
                         ("RJXS30", "JULIO", "PÉREZ"))
        self.assertEqual(payload["ranking"]["most_used"]["route_id"], "R-ABC123")
        self.assertIn("6.7", payload["metadata"]["classification"])

    def test_dashboard_levels(self):
        cursor = FakeCursor([("FROM rutas_version", lambda p: [dict(VERSION)]), ("validos", [{"n": 7}])])
        dash = rutas_api.classified_dashboard(cursor)
        self.assertEqual(dash["rutas"], {"HIGH": 1, "MEDIUM": 0, "LOW": 1})
        self.assertEqual(dash["operacion"]["viajes"], 18)


class JourneyTests(unittest.TestCase):
    def test_journey_splits_gps_jumps_and_uses_chile_time(self):
        base = datetime(2026, 10, 6, 15)  # 12:00 hora de Chile
        rows = [{"report_id": f"r{i}", "asset_name": "Lampa-826", "report_date": base.replace(minute=i),
                 "latitude": -33.4 + i * 0.001, "longitude": -70.6, "driver_name": None, "driver_last_name": None}
                for i in range(10)]
        rows[5] = dict(rows[5], latitude=-20.0)  # salto de GPS
        cursor = FakeCursor([
            ("MAX(report_date)", lambda p: [{"ultimo": base.replace(minute=9)}]),
            ("FROM telemetria_api WHERE asset_id=%s AND report_date", rows),
            ("FROM raw_gps WHERE asset_id=%s AND report_date", []),
        ])
        journey = rutas_api.truck_journey(cursor, "RJXS30")
        self.assertEqual(journey["fecha"], "2026-10-06")
        self.assertEqual(journey["inicio"], "2026-10-06 12:00:00")
        self.assertEqual(journey["geometry"]["type"], "MultiLineString")
        self.assertEqual(len(journey["geometry"]["coordinates"]), 2)
        self.assertTrue(all(-34 < lat < -33 for path in journey["geometry"]["coordinates"] for _, lat in path))
        self.assertEqual(journey["cortes"], 0)  # un salto de GPS no es un corte de señal

    def test_journey_counts_signal_gaps(self):
        base = datetime(2026, 10, 6, 15)
        minutos = [0, 1, 2, 20, 21, 50]  # dos cortes: 18 y 29 min sin reportes
        rows = [{"report_id": f"r{m}", "asset_name": "Lampa-826", "report_date": base.replace(minute=m),
                 "latitude": -33.4 + m * 0.0001, "longitude": -70.6, "driver_name": None, "driver_last_name": None}
                for m in minutos]
        cursor = FakeCursor([
            ("MAX(report_date)", lambda p: [{"ultimo": base.replace(minute=50)}]),
            ("FROM telemetria_api WHERE asset_id=%s AND report_date", rows),
            ("FROM raw_gps WHERE asset_id=%s AND report_date", []),
        ])
        journey = rutas_api.truck_journey(cursor, "RJXS30")
        self.assertEqual(journey["cortes"], 2)
        self.assertEqual(journey["minutos_sin_senal"], 47.0)


class PredictionApiTests(unittest.TestCase):
    def test_pending_without_version(self):
        cursor = FakeCursor([("FROM prediccion_version", [])])
        self.assertIsNone(prediccion_api.prediction(cursor))

    def test_groups_cells_by_day_with_cell_centers(self):
        version = {"id": 2, "creado_en": datetime(2026, 10, 13, 4), "base_hasta": date(2026, 10, 12),
                   "desde": date(2026, 10, 13), "hasta": date(2026, 10, 20), "camiones": 112,
                   "metricas_json": json.dumps({"metodo": "lightgbm", "mae_modelo": 1.2, "mae_referencia": 1.5,
                                                "huella_camiones": "x"})}
        celdas = [{"fecha": date(2026, 10, 13), "celda_lat": -2246, "celda_lon": -6893, "valor": 12.0},
                  {"fecha": date(2026, 10, 13), "celda_lat": -2245, "celda_lon": -6893, "valor": 3.5},
                  {"fecha": date(2026, 10, 14), "celda_lat": -2246, "celda_lon": -6893, "valor": 10.0}]
        cursor = FakeCursor([("FROM prediccion_version", [version]), ("FROM prediccion_celda", celdas)])
        data = prediccion_api.prediction(cursor)
        self.assertEqual([d["fecha"] for d in data["dias"]], ["2026-10-13", "2026-10-14"])
        self.assertEqual(data["dias"][0]["celdas"][0], [-22.455, -68.925, 12.0])
        self.assertEqual(data["dias"][0]["total"], 16)
        self.assertEqual(data["dias"][0]["elementos"], 2)
        self.assertEqual(data["metricas"]["metodo"], "lightgbm")
        self.assertNotIn("huella_camiones", data["metricas"])


class RoutePredictionApiTests(unittest.TestCase):
    def test_route_segments_as_lines(self):
        version = {"id": 3, "creado_en": datetime(2026, 10, 13, 4), "base_hasta": date(2026, 10, 12),
                   "desde": date(2026, 10, 13), "hasta": date(2026, 10, 27), "camiones": 116,
                   "metricas_json": json.dumps({"metodo": "promedio_dia_semana"})}
        tramos = [{"fecha": date(2026, 10, 13), "a_lat": -2246, "a_lon": -6893, "b_lat": -2246, "b_lon": -6892,
                   "valor": 4.0}]
        forma = [[-22.4551, -68.9249], [-22.4553, -68.9201], [-22.4549, -68.9151]]
        trazos = [{"a_lat": -2246, "a_lon": -6893, "b_lat": -2246, "b_lon": -6892, "puntos": json.dumps(forma)}]
        cursor = FakeCursor([("FROM prediccion_version", [version]), ("FROM prediccion_tramo", tramos),
                             ("FROM prediccion_trazo", trazos)])
        data = prediccion_api.prediction(cursor, "rutas")
        self.assertEqual(data["tipo"], "rutas")
        celda = data["dias"][0]["celdas"][0]
        self.assertEqual(celda, [-22.455, -68.925, -22.455, -68.915, 4.0])
        # La web busca la forma con los cuatro primeros valores unidos por comas (como JavaScript).
        self.assertEqual(data["trazos"][",".join(repr(v) for v in celda[:4])], forma)
        self.assertIn("-22.455,-68.925,-22.455,-68.915", data["trazos"])


if __name__ == "__main__":
    unittest.main()
