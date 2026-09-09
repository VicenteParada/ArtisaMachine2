from datetime import datetime, timedelta
import unittest

from common.telemetry_map import analyze_telemetry


class TelemetryMapTests(unittest.TestCase):
    def test_builds_one_complete_geojson_route_and_preserves_map_elements(self):
        start = datetime(2026, 3, 31, 10)
        rows = [
            {"report_id": "1", "asset_id": "A", "asset_name": "Camion A", "report_date": start,
             "latitude": -23.10, "longitude": -70.10, "speed": 25, "event_name": "Reporte por tiempo"},
            {"report_id": "2", "asset_id": "A", "asset_name": "Camion A", "report_date": start + timedelta(minutes=1),
             "latitude": -23.13, "longitude": -70.11, "speed": 18, "event_name": "Inicio de detenido"},
            {"report_id": "3", "asset_id": "A", "asset_name": "Camion A", "report_date": start + timedelta(minutes=1),
             "latitude": -23.13, "longitude": -70.11, "speed": 18, "event_name": "Inicio de detenido"},
            {"report_id": "4", "asset_id": "B", "asset_name": "Camion B", "report_date": start,
             "latitude": -23.10, "longitude": -70.10, "speed": 25, "event_name": "Reporte por tiempo"},
            {"report_id": "5", "asset_id": "B", "asset_name": "Camion B", "report_date": start + timedelta(minutes=1),
             "latitude": -23.13, "longitude": -70.11, "speed": 18, "event_name": "Reporte por tiempo"},
        ]

        result = analyze_telemetry(rows)

        self.assertEqual(result["resumen"]["reportes"], 4)
        self.assertEqual(result["resumen"]["vehiculos"], 2)
        self.assertEqual(result["resumen"]["paradas"], 1)
        self.assertEqual(result["resumen"]["rutas"], 1)
        route = result["rutas"][0]
        self.assertEqual(route["type"], "Feature")
        self.assertEqual(route["geometry"]["type"], "LineString")
        self.assertEqual(route["properties"]["usage_count"], 2)
        self.assertEqual(route["properties"]["usage_level"], "HIGH")
        self.assertEqual(route["properties"]["color"], "#14532d")
        self.assertEqual(result["ranking"]["mas_usado"]["properties"]["route_id"], route["properties"]["route_id"])
        self.assertEqual(result["paradas_por_vehiculo"][0]["asset_id"], "A")
        self.assertEqual(len(result["vehiculos_actuales"]), 2)
        self.assertEqual(len(result["recorridos"]), 2)
        self.assertTrue(all(trip["route_id"] == route["properties"]["route_id"] for trip in result["recorridos"]))
        self.assertEqual(len(result["paradas_geograficas"]), 1)
        vehicle_a = next(vehicle for vehicle in result["vehiculos_actuales"] if vehicle["asset_id"] == "A")
        self.assertEqual(vehicle_a["report_date"], start + timedelta(minutes=1))
        self.assertEqual(vehicle_a["latitud"], -23.13)
        self.assertEqual(result["ultima_posicion"], start + timedelta(minutes=1))

    def test_classifies_whole_routes_once_as_high_medium_and_low(self):
        start = datetime(2026, 9, 9, 8)
        rows = []
        report_number = 1

        def add_trip(asset, base_latitude, base_longitude):
            nonlocal report_number
            points = [
                (base_latitude, base_longitude),
                (base_latitude + 0.015, base_longitude + 0.008),
                (base_latitude + 0.030, base_longitude + 0.025),
            ]
            for offset, (latitude, longitude) in enumerate(points):
                rows.append({
                    "report_id": str(report_number),
                    "asset_id": asset,
                    "asset_name": asset,
                    "report_date": start + timedelta(minutes=offset),
                    "latitude": latitude,
                    "longitude": longitude,
                    "speed": 35,
                    "event_name": "Reporte por tiempo",
                })
                report_number += 1

        for asset in ("H1", "H2", "H3"):
            add_trip(asset, -23.10, -70.10)
        for asset in ("M1", "M2"):
            add_trip(asset, -25.10, -69.10)
        add_trip("L1", -27.10, -68.10)

        result = analyze_telemetry(rows)
        routes = result["rutas"]
        by_level = {route["properties"]["usage_level"]: route for route in routes}

        self.assertEqual(result["resumen"]["categorias_ruta"], {"HIGH": 1, "MEDIUM": 1, "LOW": 1})
        self.assertEqual(by_level["HIGH"]["properties"]["usage_count"], 3)
        self.assertEqual(by_level["HIGH"]["properties"]["color"], "#14532d")
        self.assertEqual(by_level["MEDIUM"]["properties"]["usage_count"], 2)
        self.assertEqual(by_level["MEDIUM"]["properties"]["color"], "#f97316")
        self.assertEqual(by_level["LOW"]["properties"]["usage_count"], 1)
        self.assertEqual(by_level["LOW"]["properties"]["color"], "#dc2626")
        for route in routes:
            self.assertGreaterEqual(len(route["geometry"]["coordinates"]), 2)
            self.assertTrue(route["properties"]["route_id"].startswith("RUTA_"))
            self.assertFalse(route["properties"]["map_matching"])

    def test_accepts_a_loop_using_accumulated_distance(self):
        start = datetime(2026, 9, 9, 8)
        rows = [
            {"report_id": "1", "asset_id": "LOOP", "report_date": start,
             "latitude": -23.10, "longitude": -70.10, "speed": 20},
            {"report_id": "2", "asset_id": "LOOP", "report_date": start + timedelta(minutes=1),
             "latitude": -23.11, "longitude": -70.09, "speed": 20},
            {"report_id": "3", "asset_id": "LOOP", "report_date": start + timedelta(minutes=2),
             "latitude": -23.10, "longitude": -70.10, "speed": 20},
        ]
        self.assertEqual(analyze_telemetry(rows)["resumen"]["rutas"], 1)

    def test_groups_gps_variations_and_different_sampling_as_one_route(self):
        start = datetime(2026, 9, 9, 8)
        rows = []
        paths = {
            "A": [
                (-23.0990, -70.0990),
                (-23.0850, -70.0840),
                (-23.0700, -70.0670),
                (-23.0500, -70.0500),
            ],
            "B": [
                (-23.1010, -70.1010),
                (-23.0920, -70.0910),
                (-23.0820, -70.0800),
                (-23.0700, -70.0660),
                (-23.0600, -70.0580),
                (-23.0520, -70.0480),
            ],
        }
        report = 1
        for asset, points in paths.items():
            for minute, (latitude, longitude) in enumerate(points):
                rows.append({
                    "report_id": str(report),
                    "asset_id": asset,
                    "asset_name": asset,
                    "report_date": start + timedelta(minutes=minute),
                    "latitude": latitude,
                    "longitude": longitude,
                    "speed": 35,
                })
                report += 1

        result = analyze_telemetry(rows)

        self.assertEqual(result["resumen"]["recorridos"], 2)
        self.assertEqual(result["resumen"]["rutas"], 1)
        self.assertEqual(result["rutas"][0]["properties"]["usage_count"], 2)
        self.assertEqual(result["rutas"][0]["properties"]["unique_vehicle_count"], 2)
        self.assertTrue(all(
            trip["geometry"]["type"] == "LineString"
            and len(trip["geometry"]["coordinates"]) >= 2
            and trip["distance_km"] > 0
            for trip in result["recorridos"]
        ))

    def test_keeps_opposite_directions_as_different_routes(self):
        start = datetime(2026, 9, 9, 8)
        forward = [
            (-23.10, -70.10),
            (-23.08, -70.08),
            (-23.06, -70.06),
            (-23.04, -70.04),
        ]
        rows = []
        report = 1
        for asset, points in (("IDA", forward), ("VUELTA", list(reversed(forward)))):
            for minute, (latitude, longitude) in enumerate(points):
                rows.append({
                    "report_id": str(report),
                    "asset_id": asset,
                    "report_date": start + timedelta(minutes=minute),
                    "latitude": latitude,
                    "longitude": longitude,
                    "speed": 35,
                })
                report += 1

        result = analyze_telemetry(rows)

        self.assertEqual(result["resumen"]["recorridos"], 2)
        self.assertEqual(result["resumen"]["rutas"], 2)
        self.assertEqual(
            sorted(route["properties"]["usage_count"] for route in result["rutas"]),
            [1, 1],
        )

    def test_same_endpoints_do_not_merge_different_corridors(self):
        start = datetime(2026, 9, 9, 8)
        paths = {
            "NORTE": [
                (-23.10, -70.10),
                (-23.04, -70.08),
                (-23.04, -70.02),
                (-23.10, -70.00),
            ],
            "SUR": [
                (-23.10, -70.10),
                (-23.16, -70.08),
                (-23.16, -70.02),
                (-23.10, -70.00),
            ],
        }
        rows = []
        report = 1
        for asset, points in paths.items():
            for minute, (latitude, longitude) in enumerate(points):
                rows.append({
                    "report_id": str(report),
                    "asset_id": asset,
                    "report_date": start + timedelta(minutes=minute * 5),
                    "latitude": latitude,
                    "longitude": longitude,
                    "speed": 35,
                })
                report += 1

        result = analyze_telemetry(rows)

        self.assertEqual(result["resumen"]["recorridos"], 2)
        self.assertEqual(result["resumen"]["rutas"], 2)
        self.assertTrue(all(
            route["properties"]["usage_count"] >= 1 for route in result["rutas"]
        ))

    def test_current_rows_preserve_latest_position_for_assets_outside_route_window(self):
        start = datetime(2026, 9, 9, 8)
        route_rows = [
            {"report_id": "1", "asset_id": "A", "asset_name": "Camion A", "report_date": start,
             "latitude": -23.10, "longitude": -70.10, "speed": 20},
            {"report_id": "2", "asset_id": "A", "asset_name": "Camion A", "report_date": start + timedelta(minutes=1),
             "latitude": -23.12, "longitude": -70.12, "speed": 25},
        ]
        current_rows = route_rows + [
            {"report_id": "3", "asset_id": "B", "asset_name": "Camion B", "report_date": start - timedelta(days=2),
             "latitude": -33.45, "longitude": -70.66, "speed": 0},
        ]

        result = analyze_telemetry(route_rows, current_rows=current_rows)

        self.assertEqual(result["resumen"]["vehiculos"], 2)
        self.assertEqual({vehicle["asset_id"] for vehicle in result["vehiculos_actuales"]}, {"A", "B"})
        self.assertEqual(result["resumen"]["recorridos"], 1)
        self.assertEqual(result["rutas"][0]["properties"]["usage_count"], 1)

    def test_splits_impossible_gps_jump_instead_of_drawing_a_diagonal(self):
        start = datetime(2026, 9, 9, 8)
        rows = [
            {"report_id": "1", "asset_id": "A", "report_date": start,
             "latitude": -23.10, "longitude": -70.10, "speed": 20},
            {"report_id": "2", "asset_id": "A", "report_date": start + timedelta(seconds=1),
             "latitude": -23.08, "longitude": -70.08, "speed": 20},
        ]

        result = analyze_telemetry(rows)

        self.assertEqual(result["resumen"]["rutas"], 0)
        self.assertEqual(result["resumen"]["reportes"], 2)

    def test_empty_dataset_has_no_bounds_or_ranking(self):
        result = analyze_telemetry([])
        self.assertIsNone(result["limites"])
        self.assertIsNone(result["ranking"]["mas_usado"])
        self.assertEqual(result["resumen"]["paradas"], 0)
        self.assertEqual(result["resumen"]["rutas"], 0)
        self.assertEqual(result["rutas"], [])
        self.assertEqual(result["vehiculos_actuales"], [])
        self.assertIsNone(result["ultima_posicion"])


if __name__ == "__main__":
    unittest.main()
