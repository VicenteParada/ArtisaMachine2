import unittest
from unittest.mock import MagicMock, patch

try:
    from machine_learning.telemetry import cycle, train_hotspots
except ModuleNotFoundError as error:
    if error.name not in {"numpy", "sklearn"}:
        raise
    train_hotspots = None


class TelemetryModelTests(unittest.TestCase):
    @unittest.skipIf(train_hotspots is None, "scikit-learn se valida dentro del contenedor Machine")
    def test_cycle_queries_only_camiones_with_current_limit_and_order(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"ultimo_mongo_id": "new-version", "validos": 112}
        rows = [{"asset_id": "CAMION-1", "latitude": -23.0, "longitude": -70.0, "speed": 10}]
        cursor.fetchall.return_value = rows
        metrics = {"zonas_detectadas": 1, "reportes_entrenamiento": 1, "cobertura_pct": 100}

        with patch("machine_learning.telemetry.connect_db", return_value=connection), patch(
            "machine_learning.telemetry.train_hotspots", return_value=(metrics, [])
        ) as train, patch("builtins.print"):
            version = cycle("old-version", radius_km=0.5, min_samples=3, max_rows=42)

        query, parameters = cursor.execute.call_args_list[1].args
        self.assertIn("INNER JOIN vehiculos AS v ON v.asset_id = gps.asset_id", query)
        self.assertIn("INNER JOIN tipos_vehiculo AS t ON t.id_tipo = v.id_tipo", query)
        self.assertIn("WHERE t.nombre = 'CAMION'", query)
        self.assertIn("ORDER BY gps.report_date DESC LIMIT %s", query)
        self.assertEqual(parameters, (42,))
        train.assert_called_once_with(rows, radius_km=0.5, min_samples=3)
        self.assertEqual(version, "new-version")

    @unittest.skipIf(train_hotspots is None, "scikit-learn se valida dentro del contenedor Machine")
    def test_discovers_two_zones_and_noise(self):
        rows = []
        for index in range(5):
            rows.append({"asset_id": "A", "latitude": -23.0 + index * 0.0001,
                         "longitude": -70.0 + index * 0.0001, "speed": 10})
            rows.append({"asset_id": "B", "latitude": -23.1 + index * 0.0001,
                         "longitude": -70.1 + index * 0.0001, "speed": 20})
        rows.append({"asset_id": "C", "latitude": -25.0, "longitude": -72.0, "speed": 0})

        metrics, zones = train_hotspots(rows, radius_km=0.5, min_samples=3)

        self.assertEqual(metrics["zonas_detectadas"], 2)
        self.assertEqual(metrics["puntos_ruido"], 1)
        self.assertEqual(metrics["reportes_entrenamiento"], 11)
        self.assertEqual(len(zones), 2)
        self.assertEqual(sorted(zone["reportes"] for zone in zones), [5, 5])


if __name__ == "__main__":
    unittest.main()
