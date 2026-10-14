"""Camiones: estado en vivo, paradas entre viajes y ficha del día."""

from datetime import date, datetime, timedelta
import json
import unittest
from unittest.mock import patch

from endpoints import camiones_api
from tests.test_rutas_api import FakeCursor

T = datetime(2026, 10, 14, 11)  # 08:00 hora de Chile


def viaje(inicio_min, fin_min, origen=(-22.45, -68.92), destino=(-22.60, -68.80)):
    return {"inicio": T + timedelta(minutes=inicio_min), "fin": T + timedelta(minutes=fin_min),
            "origen": origen, "destino": destino}


class EstadoTests(unittest.TestCase):
    def test_status_from_age_and_speed(self):
        self.assertEqual(camiones_api.estado(None, None), "sin_datos")
        self.assertEqual(camiones_api.estado(40, 60), "sin_senal")   # dato viejo: no se afirma que va en ruta
        self.assertEqual(camiones_api.estado(2, 60), "en_ruta")
        self.assertEqual(camiones_api.estado(2, 0), "detenido")


class FlotaTests(unittest.TestCase):
    def test_fleet_status_today_and_series(self):
        ahora = T + timedelta(minutes=10)
        hoy = date(2026, 10, 14)
        ultimo = [{"asset_id": "AB1234", "asset_name": "906", "report_date": T + timedelta(minutes=6),  # hace 4 min
                   "latitude": -22.45, "longitude": -68.92, "speed": 62, "heading": 90, "ignition": 1,
                   "event_name": "Posición", "driver_name": "Juan", "driver_last_name": "Pérez"}]
        cursor = FakeCursor([
            ("FROM telemetria_api AS gps", ultimo),
            ("FROM raw_gps AS gps", []),
            ("FROM vehiculos", [{"asset_id": "AB1234"}, {"asset_id": "CD5678"}]),
            ("FROM rutas_version", []),
            ("FROM reporte_diario WHERE", [
                {"fecha": hoy, "asset_id": "AB1234", "asset_name": "906", "km_observados": 50.04, "viajes": 2,
                 "minutos_detenido": 30.2, "primer_reporte": T, "ultimo_reporte": T + timedelta(minutes=6)},
                {"fecha": hoy - timedelta(days=1), "asset_id": "AB1234", "asset_name": "906", "km_observados": 80.0,
                 "viajes": 3, "minutos_detenido": 10, "primer_reporte": T, "ultimo_reporte": T}]),
        ])
        flota = camiones_api.fleet(cursor, ahora)
        a, b = flota["camiones"]
        self.assertEqual((a["estado"], a["edad_minutos"], a["conductor"]), ("en_ruta", 4.0, "Juan Pérez"))
        self.assertEqual(a["hoy"]["km"], 50.0)
        self.assertEqual([p["km"] for p in a["serie"]][-2:], [80.0, 50.0])
        self.assertEqual(len(a["serie"]), camiones_api.SERIE_DIAS)
        self.assertEqual((b["estado"], b["hoy"]), ("sin_datos", None))
        self.assertEqual(flota["resumen"], {"total": 2, "en_ruta": 1, "detenido": 0, "sin_senal": 0,
                                            "sin_datos": 1, "km_hoy": 50.0, "viajes_hoy": 2})


class LineaDeTiempoTests(unittest.TestCase):
    def test_stops_are_the_gaps_between_trips(self):
        viajes = [viaje(30, 90), viaje(150, 200, origen=(-22.60, -68.80), destino=None), viaje(260, 300)]
        eventos = camiones_api.linea_de_tiempo(viajes, T, T + timedelta(minutes=400))
        self.assertEqual([e["tipo"] for e in eventos], ["parada", "parada", "sin_datos", "parada"])
        self.assertEqual(eventos[0]["hasta"] - eventos[0]["desde"], timedelta(minutes=30))  # antes del 1.er viaje
        self.assertEqual(eventos[1]["lugar"], (-22.60, -68.80))                           # destino del viaje 1
        self.assertIsNone(eventos[2]["lugar"])                                            # destino desconocido
        self.assertEqual(eventos[3]["hasta"], T + timedelta(minutes=400))                 # hasta el último reporte

    def test_short_edges_are_not_stops(self):
        eventos = camiones_api.linea_de_tiempo([viaje(5, 90)], T, T + timedelta(minutes=95))
        self.assertEqual(eventos, [])
        self.assertEqual(camiones_api.linea_de_tiempo([], T, T), [])


class FichaTests(unittest.TestCase):
    def test_truck_day_detail(self):
        dia = date(2026, 10, 14)
        resumen = {"fecha": dia, "asset_name": "906", "reportes": 300, "primer_reporte": T,
                   "ultimo_reporte": T + timedelta(minutes=400), "km_observados": 123.45, "viajes": 1,
                   "paradas": 2, "minutos_detenido": 120.4}
        anterior = dict(resumen, fecha=dia - timedelta(days=1), km_observados=80.0)
        geometria = json.dumps([[[-68.92, -22.45], [-68.80, -22.60]]])
        viaje_fila = {"inicio": T + timedelta(minutes=30), "fin": T + timedelta(minutes=90),
                      "origen_lat": -22.45, "origen_lon": -68.92, "destino_lat": -22.60, "destino_lon": -68.80,
                      "puntos": 60, "km": 21.04, "con_saltos": 0, "abierto_inicio": 0, "abierto_fin": 0,
                      "geometria": geometria}
        reportes = [{"report_id": f"r{i}", "asset_name": "906", "report_date": T + timedelta(minutes=i),
                     "latitude": -22.45 - i * 0.001, "longitude": -68.92, "driver_name": "Juan",
                     "driver_last_name": "Pérez"} for i in range(5)]
        cursor = FakeCursor([
            ("FROM vehiculos", [{"asset_id": "AB1234"}]),
            ("FROM reporte_diario_estado", [{"estado": "en_curso"}]),
            ("FROM reporte_diario WHERE", [anterior, resumen]),
            ("FROM viajes_camion", [viaje_fila]),
            ("FROM faenas", lambda p: [{"nombre": "Faena Calama"}] if p[0] == "POINT(-68.92 -22.45)" else []),
            ("SELECT location FROM telemetria_api", [{"location": "Calama, Antofagasta"}]),
            ("FROM telemetria_api WHERE asset_id=%s AND report_date", reportes),
            ("FROM raw_gps WHERE asset_id=%s AND report_date", []),
        ])
        with patch.object(camiones_api.dias, "hoy", return_value=dia):
            ficha = camiones_api.truck_detail(cursor, "AB1234", dia)
        self.assertEqual(ficha["nombre"], "906")
        self.assertEqual(ficha["conductor"], "Juan Pérez")
        self.assertEqual(ficha["resumen"]["km"], 123.5)
        self.assertEqual(ficha["resumen"]["minutos_activo"], 400)
        self.assertEqual(ficha["viajes"][0]["lineas"], [[[-22.45, -68.92], [-22.60, -68.80]]])  # lat, lon
        self.assertEqual([(e["tipo"], e["nombre"]) for e in ficha["eventos"]],
                         [("parada", "Faena Calama"), ("parada", "Calama, Antofagasta")])
        self.assertEqual([h["km"] for h in ficha["historial"]], [80.0, 123.5])
        self.assertEqual(len(ficha["recorrido"]["lineas"]), 1)

    def test_history_window_ends_today_whatever_day_is_shown(self):
        hoy, dia = date(2026, 10, 14), date(2026, 10, 12)
        consultas = []
        def historial(params):
            consultas.append(params)
            return []
        cursor = FakeCursor([
            ("FROM vehiculos", [{"asset_id": "AB1234"}]),
            ("FROM reporte_diario_estado", []),
            ("FROM reporte_diario WHERE", historial),
            ("FROM viajes_camion", []),
            ("FROM telemetria_api WHERE asset_id=%s AND report_date", []),
            ("FROM raw_gps WHERE asset_id=%s AND report_date", []),
        ])
        with patch.object(camiones_api.dias, "hoy", return_value=hoy):
            camiones_api.truck_detail(cursor, "AB1234", dia)
        self.assertEqual(consultas[0], (hoy - timedelta(days=29), hoy, "AB1234"))

    def test_unknown_truck(self):
        cursor = FakeCursor([("FROM vehiculos", [{"asset_id": "AB1234"}])])
        self.assertIsNone(camiones_api.truck_detail(cursor, "ZZ9999", date(2026, 10, 14)))


if __name__ == "__main__":
    unittest.main()
