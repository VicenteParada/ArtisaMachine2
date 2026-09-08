import unittest

try:
    from modelo_machine.telemetry import train_hotspots
except ModuleNotFoundError as error:
    if error.name not in {"numpy", "sklearn"}:
        raise
    train_hotspots = None


class TelemetryModelTests(unittest.TestCase):
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
