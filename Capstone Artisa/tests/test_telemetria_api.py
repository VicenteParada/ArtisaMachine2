"""Pruebas del conector a la API de la empresa, incluida una caída real de red.

Se levanta un servidor HTTP local que imita la API SQL (POST {"sql"} -> {"rows"})
con un reloj en hora de Chile (UTC-3). Se apaga y se vuelve a encender para
comprobar que el conector no pierde ni duplica reportes.
"""

from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import unittest

from telemetria_api.source import (
    ApiConfig, ApiSourceError, build_query, detect_offset, fetch_rows, transform_rows,
)
from telemetria_api.worker import backoff_seconds, run_cycle


SOURCE_OFFSET = timedelta(hours=-3)
FMT = "%Y-%m-%d %H:%M:%S.%f"


class FakeSource:
    """Tabla 'reportes' del origen, con fechas en hora local como DATETIME naive."""

    def __init__(self, utc_now):
        self.utc_now = utc_now
        self.rows = []

    @property
    def now(self):
        return self.utc_now + SOURCE_OFFSET

    def add(self, report_id, native_date, asset="RJXS30", name="Lampa-826", lat=-33.4, lon=-70.6):
        self.rows.append({"report_id": report_id, "asset_id": asset, "asset_name": name,
                          "report_date": native_date, "latitude": lat, "longitude": lon,
                          "speed": 40, "heading": 90, "ignition": 1, "event_name": "Reporte por tiempo"})

    def query(self, sql):
        if " FROM " not in sql:  # consulta de reloj del origen
            return [{"ahora_origen": self.now.strftime(FMT)}]
        assert "to_char(r.report_date, 'YYYY-MM-DD HH24:MI:SS.US')" in sql
        limit = int(re.search(r"LIMIT (\d+)$", sql).group(1))
        future = int(re.search(r"LOCALTIMESTAMP \+ INTERVAL '(\d+) minutes'", sql).group(1))
        rows = [r for r in self.rows if r["report_date"] <= self.now + timedelta(minutes=future)]
        keyset = re.search(r"r\.report_date > '([^']+)' OR \(r\.report_date = '[^']+' AND r\.report_id::text > '([^']*)'\)", sql)
        since = re.search(r"r\.report_date >= '([^']+)'", sql)
        initial = re.search(r"LOCALTIMESTAMP - INTERVAL '(\d+) hours'", sql)
        if keyset:
            key = (datetime.strptime(keyset.group(1), FMT), keyset.group(2))
            rows = [r for r in rows if (r["report_date"], r["report_id"]) > key]
        elif since:
            moment = datetime.strptime(since.group(1), FMT)
            rows = [r for r in rows if r["report_date"] >= moment]
        elif initial:
            rows = [r for r in rows if r["report_date"] >= self.now - timedelta(hours=int(initial.group(1)))]
        rows.sort(key=lambda r: (r["report_date"], r["report_id"]))
        out = []
        for r in rows[:limit]:
            item = {k: v for k, v in r.items() if k != "report_date"}
            item["report_date_origen"] = r["report_date"].strftime(FMT)
            item["ahora_origen"] = self.now.strftime(FMT)
            out.append(item)
        return out


class FakeApiServer:
    def __init__(self, source, port=0):
        source_ref = source

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                payload = json.dumps({"rows": source_ref.query(body["sql"])}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        ThreadingHTTPServer.allow_reuse_address = True
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class FakeDatabase:
    """Equivalente en memoria de save_page/load_state (misma semántica)."""

    def __init__(self):
        self.state = {"cursor_fecha": None, "cursor_id": None}
        self.table = {}
        self.rejected = 0

    def load(self, config):
        return dict(self.state)

    def save(self, accepted, rejected, raw_count, new_cursor, warning):
        for row in accepted:
            self.table.setdefault(row["report_id"], row)
        self.rejected += len(rejected)
        stored = (self.state["cursor_fecha"], self.state["cursor_id"] or "")
        if new_cursor and (stored[0] is None or new_cursor > stored):
            self.state["cursor_fecha"], self.state["cursor_id"] = new_cursor


def make_config(url, **changes):
    values = dict(
        url=url, table="reportes", timezone_name="auto", native_dates=True,
        auth_header="Authorization", auth_value="", timeout_seconds=3, batch_size=1000,
        interval_seconds=30, overlap_seconds=300, initial_lookback_hours=24, start_utc=None,
        max_future_minutes=10, max_pages_per_cycle=50, retry_min_seconds=5, retry_max_seconds=60,
    )
    values.update(changes)
    return ApiConfig(**values)


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config("http://api.local:3003/sql")

    def test_postgres_query_uses_source_clock_and_native_dates(self):
        sql = build_query(self.config)
        self.assertIn("to_char(r.report_date, 'YYYY-MM-DD HH24:MI:SS.US') AS report_date_origen", sql)
        self.assertIn("to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS.US') AS ahora_origen", sql)
        self.assertIn("r.report_date >= LOCALTIMESTAMP - INTERVAL '24 hours'", sql)
        self.assertIn("r.report_date <= LOCALTIMESTAMP + INTERVAL '10 minutes'", sql)
        self.assertTrue(sql.endswith("ORDER BY r.report_date, r.report_id::text LIMIT 1000"))
        self.assertNotIn("DATE_FORMAT", sql)
        self.assertNotIn("NOW(6)", sql)

    def test_keyset_escapes_identifiers(self):
        sql = build_query(self.config, after=(datetime(2026, 10, 6, 12, 0), "a'b\\c"))
        self.assertIn("r.report_id::text > 'a''b\\c'", sql)  # PostgreSQL: la barra no se duplica
        self.assertIn("r.report_date > '2026-10-06 12:00:00.000000'", sql)

    def test_mysql_dialect_still_supported(self):
        sql = build_query(make_config("http://x/sql", dialect="mysql"))
        self.assertIn("DATE_FORMAT(r.report_date, '%Y-%m-%d %H:%i:%s.%f')", sql)
        self.assertIn("r.report_date >= NOW(6) - INTERVAL 24 HOUR", sql)
        self.assertTrue(sql.endswith("ORDER BY r.report_date, r.report_id LIMIT 1000"))

    def test_rejects_unsafe_table_name(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"COMPANY_API_URL": "http://x:3003/sql", "COMPANY_API_TABLE": "reportes; DROP"}):
            with self.assertRaises(ValueError):
                ApiConfig.from_env()
        with patch.dict(os.environ, {"COMPANY_API_URL": ""}):
            self.assertIsNone(ApiConfig.from_env())

    def test_origin_never_contains_credentials(self):
        config = make_config("http://user:secret@10.0.0.5:3003/sql?token=abc")
        self.assertEqual(config.origin, "http://10.0.0.5:3003")


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config("http://api.local/sql")

    def row(self, **changes):
        value = {"report_id": "R1", "asset_id": "RJXS30", "asset_name": "Lampa-826",
                 "report_date_origen": "2026-10-06 09:00:00.000000", "latitude": -33.4,
                 "longitude": -70.6, "speed": 50, "heading": 10, "ignition": "1",
                 "event_name": "Reporte", "ahora_origen": "2026-10-06 09:01:00.000000"}
        value.update(changes)
        return value

    def test_converts_source_local_time_to_utc(self):
        offset = detect_offset([self.row()], datetime(2026, 10, 6, 12, 1, 7))
        self.assertEqual(offset, timedelta(hours=-3))
        accepted, rejected = transform_rows([self.row()], self.config, offset)
        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["report_date"], datetime(2026, 10, 6, 12, 0))
        self.assertIs(accepted[0]["ignition"], True)

    def test_bad_positions_are_rejected_but_keep_their_key(self):
        accepted, rejected = transform_rows([
            self.row(report_id="A", latitude=0, longitude=0),
            self.row(report_id="B", latitude=None),
            self.row(report_id="C", asset_id=None),
            self.row(report_id="D", speed=999, heading=-5),
        ], self.config, timedelta(0))
        self.assertEqual([r["report_id"] for r in accepted], ["D"])
        self.assertIsNone(accepted[0]["speed"])  # valor accesorio dudoso: se anula, no se descarta la posición
        self.assertEqual(len(rejected), 3)
        self.assertTrue(all(item["clave"] is not None for item in rejected))

    def test_backoff_grows_and_is_capped(self):
        delays = [backoff_seconds(n, self.config) for n in range(1, 10)]
        self.assertLess(delays[0], 7)
        self.assertTrue(all(d <= 60 * 1.2 for d in delays))
        self.assertGreater(delays[-1], 45)


class HttpErrorTests(unittest.TestCase):
    def serve(self, status, body, content_type="application/json"):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return make_config(f"http://127.0.0.1:{server.server_address[1]}/sql")

    def kind_of(self, config):
        with self.assertRaises(ApiSourceError) as caught:
            fetch_rows(config, "SELECT 1")
        return caught.exception.kind

    def test_error_kinds(self):
        self.assertEqual(self.kind_of(self.serve(500, "boom")), "error_origen")
        self.assertEqual(self.kind_of(self.serve(401, "no")), "error_configuracion")
        self.assertEqual(self.kind_of(self.serve(400, "bad sql")), "error_configuracion")
        self.assertEqual(self.kind_of(self.serve(200, "<html>portal wifi</html>", "text/html")), "respuesta_invalida")
        self.assertEqual(self.kind_of(self.serve(200, '{"error": "tabla no existe"}')), "error_origen")
        self.assertEqual(self.kind_of(make_config("http://127.0.0.1:9/sql")), "sin_conexion")

    def test_accepts_rows_object(self):
        self.assertEqual(fetch_rows(self.serve(200, '{"rows": [{"a": 1}]}'), "SELECT 1"), [{"a": 1}])


class OutageRecoveryTests(unittest.TestCase):
    def test_survives_disconnection_without_losing_or_duplicating(self):
        utc_now = datetime(2026, 10, 6, 15, 0, 0)
        source = FakeSource(utc_now)
        start = source.now - timedelta(hours=3)
        for i in range(2500):  # un reporte cada ~4 s durante 3 horas
            source.add(f"R{i:05d}", start + timedelta(seconds=4 * i))
        server = FakeApiServer(source)
        port = server.port
        config = make_config(f"http://127.0.0.1:{port}/sql")
        db = FakeDatabase()
        cycle = lambda: run_cycle(config, save=db.save, load=db.load, now=lambda: source.utc_now)

        # 1) Primera conexión: trae las 3 horas en varias páginas.
        self.assertTrue(cycle())
        self.assertEqual(len(db.table), 2500)
        self.assertEqual(db.table["R00000"]["report_date"], start - SOURCE_OFFSET)  # guardado en UTC

        # 2) Se corta el Wi-Fi: la API deja de responder mientras siguen llegando reportes.
        server.stop()
        for i in range(2500, 3700):
            source.add(f"R{i:05d}", start + timedelta(seconds=4 * i))
        source.utc_now += timedelta(hours=2)
        with self.assertRaises(ApiSourceError) as caught:
            cycle()
        self.assertEqual(caught.exception.kind, "sin_conexion")
        self.assertEqual(len(db.table), 2500)  # nada se perdió ni se corrompió

        # 3) Vuelve la red: recupera todo lo acumulado durante el corte.
        server = FakeApiServer(source, port=port)
        self.addCleanup(server.stop)
        self.assertTrue(cycle())
        self.assertEqual(len(db.table), 3700)

        # 4) Un GPS envía con atraso un reporte de hace 2 minutos: la relectura lo captura.
        late_date = db.state["cursor_fecha"] - timedelta(minutes=2)
        source.add("LATE-1", late_date)
        # 5) Un GPS con el reloj adelantado 2 días no debe mover el cursor al futuro.
        source.add("FUTURE-1", source.now + timedelta(days=2))
        # 6) Una posición inválida se rechaza sin detener el avance.
        source.add("ZERO-1", db.state["cursor_fecha"] + timedelta(seconds=1), lat=0, lon=0)
        self.assertTrue(cycle())
        self.assertIn("LATE-1", db.table)
        self.assertNotIn("FUTURE-1", db.table)
        self.assertNotIn("ZERO-1", db.table)
        self.assertLessEqual(db.state["cursor_fecha"], source.now + timedelta(minutes=10))
        self.assertEqual(len(db.table), 3701)

        # 7) Repetir ciclos sin datos nuevos no duplica nada.
        cycle()
        cycle()
        self.assertEqual(len(db.table), 3701)


class BackfillTests(unittest.TestCase):
    def test_first_connection_recovers_gap_since_start(self):
        utc_now = datetime(2026, 10, 6, 17, 0)
        source = FakeSource(utc_now)
        # Hueco desde el fin del histórico: un reporte cada 30 min durante 27 días.
        hist_end_utc = datetime(2026, 9, 8, 23, 59, 55)
        for i in range(27 * 48):
            source.add(f"G{i:05d}", hist_end_utc + SOURCE_OFFSET + timedelta(minutes=30 * i + 1))
        source.add("ANTES", hist_end_utc + SOURCE_OFFSET - timedelta(hours=5))
        server = FakeApiServer(source)
        self.addCleanup(server.stop)
        config = make_config(f"http://127.0.0.1:{server.port}/sql", start_utc=datetime(2026, 9, 8, 23, 0))
        db = FakeDatabase()
        while not run_cycle(config, save=db.save, load=db.load, now=lambda: source.utc_now):
            pass
        self.assertEqual(len(db.table), 27 * 48)
        self.assertNotIn("ANTES", db.table)
        self.assertEqual(min(r["report_date"] for r in db.table.values()), hist_end_utc + timedelta(minutes=1))

    def test_start_is_chile_time(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"COMPANY_API_URL": "http://x:3003/sql", "COMPANY_API_START": "2026-09-08 20:00"}):
            self.assertEqual(ApiConfig.from_env().start_utc, datetime(2026, 9, 8, 23, 0))


if __name__ == "__main__":
    unittest.main()
