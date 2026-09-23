import unittest
from ETLs.telemetry import transform_rows


def row(identifier="R-1", **changes):
    value = {
        "_id": "507f1f77bcf86cd799439011",
        "report_id": identifier,
        "asset_id": "RVZB95",
        "asset_name": "Codelco-Calama-868",
        "report_date": "2026-04-01T00:00:12Z",
        "latitude": -22.35549,
        "longitude": -68.92048,
        "location": "S/N, Calama, Provincia de El Loa, Region de Antofagasta, Chile",
        "speed": 35,
        "cartography_limit_speed": 0,
        "event_name": "Reporte por tiempo",
        "driver_name": "JULIO",
        "driver_last_name": "CRISTIAN PEÑAILILLO MONTAÑO",
        "driver_document_number": "14.113.804-9",
    }
    value.update(changes)
    return value


class TelemetryTests(unittest.TestCase):
    def test_normalizes_valid_telemetry_for_mariadb(self):
        from datetime import datetime
        accepted, rejected = transform_rows([row()])
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["mongo_id"], "507f1f77bcf86cd799439011")
        self.assertEqual(accepted[0]["report_date"], datetime(2026, 4, 1, 0, 0, 12))
        self.assertEqual(accepted[0]["latitude"], -22.35549)
        self.assertEqual(accepted[0]["driver_document_number"], "14.113.804-9")

    def test_accepts_missing_driver_info(self):
        accepted, rejected = transform_rows([
            row(driver_name=None, driver_last_name=None, driver_document_number=None),
        ])
        self.assertEqual(rejected, [])
        self.assertIsNone(accepted[0]["driver_name"])

    def test_rejects_missing_mongo_id(self):
        accepted, rejected = transform_rows([row(_id=None)])
        self.assertEqual(accepted, [])
        self.assertIn("_id", rejected[0]["motivo"])

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
