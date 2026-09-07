from datetime import datetime, timedelta
import unittest

from common.telemetry_map import analyze_telemetry


class TelemetryMapTests(unittest.TestCase):
    def test_counts_heat_segments_and_explicit_stops_without_duplicate_positions(self):
        start = datetime(2026, 3, 31, 10)
        rows = [
            {"report_id": "1", "asset_id": "A", "asset_name": "Camión A", "report_date": start,
             "latitude": -23.10, "longitude": -70.10, "event_name": "Reporte por tiempo"},
            {"report_id": "2", "asset_id": "A", "asset_name": "Camión A", "report_date": start + timedelta(minutes=1),
             "latitude": -23.16, "longitude": -70.10, "event_name": "Inicio de detenido"},
            {"report_id": "3", "asset_id": "A", "asset_name": "Camión A", "report_date": start + timedelta(minutes=1),
             "latitude": -23.16, "longitude": -70.10, "event_name": "Inicio de detenido"},
            {"report_id": "4", "asset_id": "B", "asset_name": "Camión B", "report_date": start,
             "latitude": -23.10, "longitude": -70.10, "event_name": "Reporte por tiempo"},
            {"report_id": "5", "asset_id": "B", "asset_name": "Camión B", "report_date": start + timedelta(minutes=1),
             "latitude": -23.16, "longitude": -70.10, "event_name": "Reporte por tiempo"},
        ]
        result = analyze_telemetry(rows)
        self.assertEqual(result["resumen"]["reportes"], 4)
        self.assertEqual(result["resumen"]["vehiculos"], 2)
        self.assertEqual(result["resumen"]["paradas"], 1)
        self.assertEqual(result["resumen"]["tramos"], 1)
        self.assertEqual(result["ranking"]["mas_usado"]["pasos"], 2)
        self.assertEqual(result["paradas_por_vehiculo"][0]["asset_id"], "A")

    def test_empty_dataset_has_no_bounds_or_ranking(self):
        result = analyze_telemetry([])
        self.assertIsNone(result["limites"])
        self.assertIsNone(result["ranking"]["mas_usado"])
        self.assertEqual(result["resumen"]["paradas"], 0)


if __name__ == "__main__":
    unittest.main()
