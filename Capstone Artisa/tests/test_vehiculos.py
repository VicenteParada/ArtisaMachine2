import unittest

from common.vehiculos import (
    TIPO_CAMION, TIPO_SIN_CLASIFICAR, es_camion, registrar_nuevos, sql_es_camion, tipo_por_nombre,
)


class FakeCursor:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.calls = []
        self._result = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        if query.startswith("SELECT asset_id FROM vehiculos WHERE asset_id IN"):
            self._result = [{"asset_id": a} for a in params if a in self.existing]

    def executemany(self, query, params):
        self.calls.append((query, list(params)))

    def fetchall(self):
        return self._result


class VehicleClassificationTests(unittest.TestCase):
    def test_names_of_real_fleet(self):
        self.assertEqual(tipo_por_nombre("Lampa-826"), TIPO_CAMION)
        self.assertEqual(tipo_por_nombre("Codelco-Calama-859"), TIPO_CAMION)
        self.assertEqual(tipo_por_nombre("716-Camioneta-CesarM"), "camioneta")
        self.assertEqual(tipo_por_nombre("Lampa-Furgón"), "furgon")
        self.assertEqual(tipo_por_nombre("Mantención"), "mantencion")
        self.assertEqual(tipo_por_nombre(None), TIPO_SIN_CLASIFICAR)
        self.assertEqual(tipo_por_nombre("   "), TIPO_SIN_CLASIFICAR)
        self.assertTrue(es_camion("Lampa-826"))
        self.assertFalse(es_camion("716-Camioneta-CesarM"))

    def test_sql_filter_uses_catalog_and_rejects_injection(self):
        clause = sql_es_camion("gps.asset_id")
        self.assertIn("gps.asset_id IN (SELECT v.asset_id FROM vehiculos AS v", clause)
        self.assertIn("t.nombre = 'camion'", clause)
        with self.assertRaises(ValueError):
            sql_es_camion("asset_id); DROP TABLE raw_gps; --")

    def test_registers_only_missing_vehicles_without_overwriting(self):
        cursor = FakeCursor(existing={"RJXS30"})
        added = registrar_nuevos(cursor, {"RJXS30": "Lampa-1", "NEW001": "Lampa-2", "NEW002": "X-Camioneta"})
        self.assertEqual(added, 2)
        inserts = [c for c in cursor.calls if c[0].startswith("INSERT IGNORE INTO vehiculos")]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(sorted(inserts[0][1]), [("NEW001", "camion"), ("NEW002", "camioneta")])
        self.assertNotIn("UPDATE", " ".join(c[0] for c in cursor.calls))

    def test_nothing_to_register(self):
        cursor = FakeCursor(existing={"A"})
        self.assertEqual(registrar_nuevos(cursor, {"A": "Lampa-1"}), 0)
        self.assertEqual(registrar_nuevos(cursor, {}), 0)


if __name__ == "__main__":
    unittest.main()
