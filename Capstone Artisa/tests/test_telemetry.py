from datetime import datetime
import json
import unittest

from ETLs.telemetry import TelemetryConfig, build_query, transform_rows


CONFIG = TelemetryConfig(
    url="http://source.test:3003/sql",
    start=datetime(2026, 3, 31),
    end=datetime(2026, 4, 1),
    batch_size=500,
    max_total_rows=5000,
    timeout_seconds=30,
)


def row(identifier="R-1", **changes):
    value = {
        "report_id": identifier,
        "sequential_id": 10,
        "asset_id": "VEH-1",
        "asset_name": "Vehiculo 1",
        "report_date": "2026-03-31T10:48:28Z",
        "latitude": -33.45,
        "longitude": -70.66,
        "speed": 42,
        "gps_speed": 41.5,
        "heading": 180,
        "odometer": 1000.5,
        "ignition": True,
        "event_id": 1,
        "event_name": "Reporte por tiempo",
        "gps_validity": 1,
        "gps_satellites": 9,
        "gps_dop": 1.2,
    }
    value.update(changes)
    return value


class TelemetryTests(unittest.TestCase):
    def test_builds_keyset_query_without_offset(self):
        first = build_query(CONFIG)
        following = build_query(CONFIG, datetime(2026, 3, 31, 10, 48, 28), "R'2", 100)
        self.assertIn("ORDER BY report_date, report_id LIMIT 500", first)
        self.assertNotIn("OFFSET", first)
        self.assertIn("report_id > 'R''2'", following)
        self.assertIn("LIMIT 100", following)

    def test_normalizes_valid_telemetry_for_mariadb(self):
        accepted, rejected = transform_rows([row()])
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["report_date"], datetime(2026, 3, 31, 10, 48, 28))
        self.assertEqual(accepted[0]["latitude"], -33.45)
        self.assertTrue(accepted[0]["ignition"])
        self.assertEqual(json.loads(accepted[0]["raw_json"])["report_id"], "R-1")

    def test_rejects_duplicate_missing_asset_and_invalid_coordinates(self):
        accepted, rejected = transform_rows([
            row(),
            row(),
            row("R-2", asset_id=""),
            row("R-3", latitude=-100),
        ])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 3)
        self.assertIn("duplicado", rejected[0]["motivo"])
        self.assertIn("asset_id", rejected[1]["motivo"])
        self.assertIn("latitude", rejected[2]["motivo"])


if __name__ == "__main__":
    unittest.main()
