"""Dashboard de flota: reportes por patente y mes desde el primer cierre."""

from datetime import date
import unittest

from endpoints import dashboard_api
from tests.test_rutas_api import FakeCursor

HOY = date(2026, 10, 15)
ESTADO = [
    {"mes": "2026-04", "dias": 20, "cerrados": 20, "reportes": 1000, "descartados": 7,
     "desde": date(2026, 4, 11), "hasta": date(2026, 4, 30)},
    {"mes": "2026-10", "dias": 15, "cerrados": 13, "reportes": 900, "descartados": 3,
     "desde": date(2026, 10, 1), "hasta": date(2026, 10, 15)},
]
CATALOGO = [{"asset_id": "AB1234"}, {"asset_id": "CD5678"}, {"asset_id": "EF9012"}]
MENSUAL = [
    {"mes": "2026-04", "asset_id": "AB1234", "asset_name": "906", "reportes": 600, "km": 1200.04,
     "viajes": 40, "dias_activos": 18, "ultimo": date(2026, 4, 30)},
    {"mes": "2026-10", "asset_id": "AB1234", "asset_name": "906", "reportes": 500, "km": 900.0,
     "viajes": 30, "dias_activos": 15, "ultimo": date(2026, 10, 15)},
    {"mes": "2026-10", "asset_id": "CD5678", "asset_name": "912", "reportes": 400, "km": 300.0,
     "viajes": 9, "dias_activos": 10, "ultimo": date(2026, 10, 10)},
    {"mes": "2026-10", "asset_id": "ZZ0000", "asset_name": "camioneta", "reportes": 99, "km": 1.0,
     "viajes": 1, "dias_activos": 1, "ultimo": date(2026, 10, 15)},
]


class FleetDashboardTests(unittest.TestCase):
    def cursor(self):
        return FakeCursor([
            ("FROM reporte_diario_estado", ESTADO),
            ("FROM vehiculos", CATALOGO),
            ("FROM reporte_diario GROUP BY", MENSUAL),
        ])

    def test_months_from_first_day_to_today(self):
        data = dashboard_api.fleet_dashboard(self.cursor(), HOY)
        self.assertEqual(data["meses"], ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"])
        self.assertEqual((data["desde"], data["mes_en_curso"]), ("2026-04-11", "2026-10"))

    def test_monthly_reports_per_plate_only_catalog_trucks(self):
        data = dashboard_api.fleet_dashboard(self.cursor(), HOY)
        a, c, e = data["camiones"]  # ordenados por total de reportes
        self.assertEqual((a["patente"], a["nombre"], a["total"]), ("AB1234", "906", 1100))
        self.assertEqual(a["reportes"], [600, 0, 0, 0, 0, 0, 500])
        self.assertEqual(a["km"][0], 1200.0)
        self.assertEqual((c["patente"], c["total"]), ("CD5678", 400))
        self.assertEqual((e["patente"], e["total"], e["ultimo_dia"]), ("EF9012", 0, None))
        self.assertNotIn("ZZ0000", [x["patente"] for x in data["camiones"]])  # no es camión
        octubre = data["flota"][-1]
        self.assertEqual((octubre["reportes"], octubre["camiones_activos"], octubre["dias_cerrados"]), (900, 2, 13))
        self.assertEqual(data["totales"]["reportes"], 1500)
        self.assertEqual(data["totales"]["descartados"], 10)

    def test_trucks_without_recent_reports(self):
        data = dashboard_api.fleet_dashboard(self.cursor(), HOY)
        # Primero los que nunca reportaron; después los de más días sin reportar.
        self.assertEqual([(c["patente"], c["dias_sin_reportes"]) for c in data["sin_reportar"]],
                         [("EF9012", None), ("CD5678", 5)])

    def test_pending_without_closed_days(self):
        self.assertIsNone(dashboard_api.fleet_dashboard(FakeCursor([("FROM reporte_diario_estado", [])]), HOY))

    def test_truck_days(self):
        cursor = FakeCursor([("FROM reporte_diario WHERE asset_id", [
            {"fecha": date(2026, 4, 11), "reportes": 300, "km_observados": 120.04, "viajes": 3}])])
        self.assertEqual(dashboard_api.truck_days(cursor, "AB1234"),
                         {"patente": "AB1234", "dias": [["2026-04-11", 300, 120.0, 3]]})


if __name__ == "__main__":
    unittest.main()
