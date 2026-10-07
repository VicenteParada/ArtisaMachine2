from datetime import datetime
import unittest

from endpoints import api


class OneRowCursor:
    def __init__(self, row):
        self.row = row
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


def state(**changes):
    row = {"estado": "conectado", "ultimo_error": None, "fallos_consecutivos": 0, "leidos": 10,
           "validos": 9, "rechazados": 1, "ultimo_exito": datetime(2026, 10, 6, 15),
           "ultimo_dato": datetime(2026, 10, 6, 15), "desconectado_desde": None, "advertencia": None,
           "exito_hace": 20, "dato_hace": 60, "revision_hace": 20}
    row.update(changes)
    return row


class CompanyApiStateTests(unittest.TestCase):
    def classify(self, **changes):
        return api.company_api_state(OneRowCursor(state(**changes)))

    def test_live_and_quiet(self):
        self.assertEqual(self.classify()["conexion"], "en_vivo")
        self.assertEqual(self.classify(dato_hace=7200)["conexion"], "sin_reportes_recientes")

    def test_disconnected_states(self):
        self.assertEqual(self.classify(estado="sin_conexion", exito_hace=600)["conexion"], "reconectando")
        # Aunque el último estado diga conectado, sin contacto reciente se informa caída.
        self.assertEqual(self.classify(exito_hace=10_000)["conexion"], "reconectando")
        self.assertEqual(self.classify(estado="error_configuracion", exito_hace=None)["conexion"],
                         "error_configuracion")
        self.assertEqual(self.classify(revision_hace=5000)["conexion"], "servicio_detenido")

    def test_not_configured_keeps_previous_pending_contract(self):
        self.assertIsNone(api.company_api_state(OneRowCursor(None)))
        self.assertIsNone(api.company_api_state(OneRowCursor(state(estado="sin_configurar"))))
        self.assertEqual(api.latest_telemetry(OneRowCursor(None), 30),
                         {"status": "pending", "source": "company_api", "reportes": []})

    def test_limit_validation(self):
        self.assertEqual(api.telemetry_limit("limite=30"), 30)
        self.assertEqual(api.telemetry_limit(""), 30)
        self.assertIsNone(api.telemetry_limit("limite=0"))
        self.assertIsNone(api.telemetry_limit("limite=500"))
        self.assertIsNone(api.telemetry_limit("limite=abc"))


class LatestTelemetryTests(unittest.TestCase):
    def test_filters_trucks_without_subquery(self):
        from datetime import timedelta
        now = datetime(2026, 10, 6, 18)
        rows = [{"report_id": f"r{i}", "asset_id": "CAMIONETA" if i % 2 else "RJXS30", "asset_name": "x",
                 "report_date": now - timedelta(seconds=i), "latitude": -33.4, "longitude": -70.6,
                 "speed": 1, "heading": 0, "ignition": 1, "event_name": "e", "edad_segundos": i} for i in range(400)]

        class Cursor(OneRowCursor):
            def execute(self, query, params=None):
                super().execute(query, params)
                self.last = query
                self.params = params

            def fetchall(self):
                if "tipos_vehiculo" in self.last:
                    return [{"asset_id": "RJXS30"}]
                if "FROM telemetria_api" in self.last:
                    batch = self.params[-1]
                    pool = [r for r in rows if not self.params[:-1] or r["report_date"] < self.params[0]]
                    return pool[:batch]
                return []

        cursor = Cursor(state())
        result = api.latest_telemetry(cursor, 30)
        self.assertEqual(len(result["reportes"]), 30)
        self.assertTrue(all(r["asset_id"] == "RJXS30" for r in result["reportes"]))
        self.assertTrue(all("IN (SELECT" not in q for q, _ in cursor.queries))


if __name__ == "__main__":
    unittest.main()
