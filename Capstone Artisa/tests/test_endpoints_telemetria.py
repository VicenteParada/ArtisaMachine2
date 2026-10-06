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


if __name__ == "__main__":
    unittest.main()
