"""Corte diario de 00:00 a 00:00 hora Chile: sin pérdidas en el corte ni con datos atrasados."""

from datetime import date, datetime, timedelta
import unittest

from common import dias
from machine_learning import diario_worker
from machine_learning.diario import resumir


def punto(asset, t, lat, lon, rid=None, name="Lampa-826"):
    return {"asset_id": asset, "asset_name": name, "report_id": rid or f"{asset}-{t.isoformat()}",
            "report_date": t, "latitude": lat, "longitude": lon}


class DayBoundsTests(unittest.TestCase):
    def test_day_is_midnight_to_midnight_in_chile(self):
        inicio, fin = dias.limites_utc(date(2026, 10, 6))   # Chile en UTC-3 (horario de verano)
        self.assertEqual(inicio, datetime(2026, 10, 6, 3))
        self.assertEqual(fin, datetime(2026, 10, 7, 3))
        inicio, fin = dias.limites_utc(date(2026, 7, 1))    # invierno: UTC-4
        self.assertEqual(inicio, datetime(2026, 7, 1, 4))

    def test_dst_days_are_contiguous(self):
        # Cada día empieza exactamente donde termina el anterior: ningún reporte queda
        # fuera ni en dos días, incluso en los cambios de hora (días de 23 y 25 horas).
        dia = date(2026, 1, 1)
        while dia < date(2027, 1, 1):
            self.assertEqual(dias.limites_utc(dia)[1], dias.limites_utc(dia + timedelta(days=1))[0])
            dia += timedelta(days=1)

    def test_report_belongs_to_local_day(self):
        self.assertEqual(dias.dia_local(datetime(2026, 10, 7, 2, 59, 59)), date(2026, 10, 6))  # 23:59:59 Chile
        self.assertEqual(dias.dia_local(datetime(2026, 10, 7, 3, 0, 0)), date(2026, 10, 7))    # 00:00 Chile


class SummaryTests(unittest.TestCase):
    def test_same_rules_as_notebook(self):
        t0 = datetime(2026, 10, 6, 12)
        filas = []
        # 20 reportes cada minuto avanzando ~500 m (30 km/h): todo se une.
        for i in range(20):
            filas.append(punto("A", t0 + timedelta(minutes=i), -33.4 + i * 0.0045, -70.6))
        # Pausa de 45 min (nuevo viaje, no se une) y otro tramo.
        filas.append(punto("A", t0 + timedelta(minutes=64), -33.2, -70.6))
        filas.append(punto("A", t0 + timedelta(minutes=65), -33.1955, -70.6))
        # Salto imposible de GPS (100 km en 1 min): no suma km.
        filas.append(punto("A", t0 + timedelta(minutes=66), -32.3, -70.6))
        # Basura: 0,0, repetido de la ETL, mismo reporte en las dos tablas.
        filas.append(punto("A", t0 + timedelta(minutes=67), 0, 0))
        filas.append(dict(filas[0]))
        filas.append(dict(filas[1], report_id="otro-id-misma-hora"))
        resumenes, descartados = resumir(filas)
        self.assertEqual(descartados, 1)
        (a,) = resumenes
        self.assertEqual(a.reportes, 23)
        self.assertEqual(a.viajes, 2)
        self.assertEqual(a.tramos_unidos, 20)
        self.assertAlmostEqual(a.km_observados, 0.5 * 20, delta=0.6)
        self.assertEqual(a.primer_reporte, t0)

    def test_empty_day(self):
        self.assertEqual(resumir([]), ([], 0))


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        if "MIN(report_date)" in query:
            self.result = [{"primero": self.db["primero"]}]
        elif "FROM telemetria_api_estado" in query:
            self.result = [self.db["conector"]] if self.db["conector"] else []
        elif "FROM reporte_diario_estado" in query:
            self.result = [dict(v, fecha=k) for k, v in self.db["estados"].items()]
        elif "MAX(recibido_en)" in query:
            inicio, fin = params
            self.result = [{"recibido": self.db["recibidos"].get(dias.dia_local(inicio))}]
        else:
            raise AssertionError(query)

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        pass


class CloseCycleTests(unittest.TestCase):
    def setUp(self):
        self.db = {"primero": datetime(2026, 10, 4, 15), "estados": {}, "recibidos": {},
                   "conector": {"estado": "conectado", "ultimo_exito": None}}
        self.done = []

    def procesar(self, connection, dia, estado, recalculo=False):
        self.done.append((dia, estado, recalculo))
        self.db["estados"][dia] = {"estado": estado, "calculado_en": self.now,
                                   "ultimo_recibido": self.db["recibidos"].get(dia)}

    def run_at(self, now, api_ok_until=None):
        self.now = now
        if self.db["conector"]:
            self.db["conector"]["ultimo_exito"] = api_ok_until or now
        self.done = []
        diario_worker.ciclo(FakeConnection(self.db), now, procesar=self.procesar)
        return [(d.isoformat(), e) for d, e, _ in self.done]

    def test_closes_pending_days_and_keeps_today_open(self):
        # 6 de octubre 12:00 Chile (15:00 UTC); datos desde el 4.
        done = self.run_at(datetime(2026, 10, 6, 15))
        self.assertEqual(done, [("2026-10-04", "cerrado"), ("2026-10-05", "cerrado"), ("2026-10-06", "en_curso")])

    def test_midnight_waits_grace_and_api_confirmation(self):
        self.run_at(datetime(2026, 10, 6, 15))
        # 00:05 del 7 (03:05 UTC): aún en el margen de gracia -> el 6 no se cierra.
        done = self.run_at(datetime(2026, 10, 7, 3, 5))
        self.assertNotIn(("2026-10-06", "cerrado"), done)
        # 00:20, pero la API está caída desde las 23:50 del 6: queda esperando datos.
        done = self.run_at(datetime(2026, 10, 7, 3, 20), api_ok_until=datetime(2026, 10, 7, 2, 50))
        self.assertIn(("2026-10-06", "esperando_datos"), done)
        # Vuelve la API a las 02:00: recupera los reportes y el día se cierra.
        done = self.run_at(datetime(2026, 10, 7, 5, 0))
        self.assertIn(("2026-10-06", "cerrado"), done)
        self.assertIn(("2026-10-07", "en_curso"), done)

    def test_late_reports_reopen_calculation(self):
        self.db["recibidos"][date(2026, 10, 5)] = datetime(2026, 10, 6, 3, 10)
        self.run_at(datetime(2026, 10, 6, 15))
        self.assertEqual(self.run_at(datetime(2026, 10, 6, 15, 1)), [])
        # Un GPS envía tarde un reporte del día 5: se recalcula ese día, sigue cerrado.
        self.db["recibidos"][date(2026, 10, 5)] = datetime(2026, 10, 6, 15, 30)
        done = self.run_at(datetime(2026, 10, 6, 15, 31))
        self.assertIn(("2026-10-05", "cerrado"), done)
        self.assertTrue(any(r for d, e, r in self.done if d == date(2026, 10, 5)))

    def test_without_company_api_only_waits_for_midnight_grace(self):
        self.db["conector"] = None
        done = self.run_at(datetime(2026, 10, 7, 3, 16))
        self.assertIn(("2026-10-06", "cerrado"), done)

    def test_nothing_to_do_without_data(self):
        self.db["primero"] = None
        self.assertEqual(self.run_at(datetime(2026, 10, 6, 15)), [])


if __name__ == "__main__":
    unittest.main()
