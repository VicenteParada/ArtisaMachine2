import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common.map_provider import DEFAULT_SIMULATION_PATH, MapDataError, load_processed_map


def distance_km(first, second):
    lon1, lat1 = first
    lon2, lat2 = second
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    return 6371.0088 * 2 * asin(min(1.0, sqrt(value)))


class ProcessedMapProviderTests(unittest.TestCase):
    def test_contract_separates_routes_trips_stops_vehicles_and_readings(self):
        data = load_processed_map()

        self.assertEqual(data["source_mode"], "SIMULATION_PROCESSED")
        self.assertEqual(data["summary"]["trip_count"], 43)
        self.assertEqual(data["summary"]["route_count"], 4)
        self.assertEqual(data["summary"]["vehicle_count"], 8)
        self.assertEqual(data["summary"]["stop_count"], 6)
        self.assertEqual(
            data["summary"]["route_categories"],
            {"HIGH": 1, "MEDIUM": 1, "LOW": 2},
        )

        route_by_id = {route["route_id"]: route for route in data["routes"]}
        self.assertEqual(route_by_id["RUTA_001"]["usage_count"], 24)
        self.assertEqual(route_by_id["RUTA_001"]["usage_level"], "HIGH")
        self.assertEqual(route_by_id["RUTA_001"]["color"], "#14532d")
        self.assertEqual(route_by_id["RUTA_002"]["usage_level"], "MEDIUM")
        self.assertEqual(route_by_id["RUTA_002"]["color"], "#f97316")
        self.assertEqual(route_by_id["RUTA_003"]["usage_level"], "LOW")
        self.assertEqual(route_by_id["RUTA_003"]["color"], "#dc2626")
        self.assertEqual(route_by_id["RUTA_004"]["usage_count"], 0)

        for route in data["routes"]:
            coordinates = route["geometry"]["coordinates"]
            self.assertGreater(len(coordinates), 500)
            longest_step = max(
                distance_km(first, second)
                for first, second in zip(coordinates, coordinates[1:])
            )
            self.assertLess(longest_step, 15)

    def test_reading_density_cannot_change_route_usage_or_color(self):
        baseline = load_processed_map()
        raw = json.loads(DEFAULT_SIMULATION_PATH.read_text(encoding="utf-8"))
        raw["readings"][0]["reading_count"] = 999999

        with TemporaryDirectory() as directory:
            path = Path(directory) / "mapa.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            changed = load_processed_map(path)

        route_fields = lambda data: [
            (
                route["route_id"],
                route["usage_count"],
                route["usage_level"],
                route["color"],
            )
            for route in data["routes"]
        ]
        self.assertEqual(route_fields(changed), route_fields(baseline))
        self.assertNotEqual(
            changed["summary"]["reading_count"],
            baseline["summary"]["reading_count"],
        )

    def test_usage_count_must_equal_explicit_trip_count(self):
        raw = json.loads(DEFAULT_SIMULATION_PATH.read_text(encoding="utf-8"))
        raw["routes"][0]["usage_count"] += 1

        with TemporaryDirectory() as directory:
            path = Path(directory) / "mapa-invalido.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(
                MapDataError,
                "usage_count no coincide",
            ):
                load_processed_map(path)


if __name__ == "__main__":
    unittest.main()