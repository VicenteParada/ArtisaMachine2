"""Asistente /api/chat: validación, contexto agregado sin datos personales y Gemini simulado."""

from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import os
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from endpoints import api, chat_api, llm

CAMIONES = {
    "status": "ok", "hoy": "2026-10-20", "reciente_minutos": 15, "en_ruta_kmh": 5,
    "resumen": {"total": 2, "en_ruta": 1, "detenido": 1, "sin_senal": 0, "sin_datos": 0,
                "km_hoy": 210.5, "viajes_hoy": 7},
    "camiones": [
        {"asset_id": "AB1234", "nombre": "906", "estado": "en_ruta", "lat": -33.456789, "lon": -70.654321,
         "conductor": "Juan Pérez", "hoy": {"km": 180.2, "viajes": 5, "minutos_detenido": 40}},
        {"asset_id": "CD5678", "nombre": "912", "estado": "detenido", "lat": -33.111111, "lon": -70.222222,
         "conductor": "Ana Soto", "hoy": {"km": 30.3, "viajes": 2, "minutos_detenido": 300}},
    ],
}
DASHBOARD = {
    "status": "ok", "hoy": "2026-10-20", "desde": "2026-04-11", "sin_reportes_dias": 2,
    "totales": {"reportes": 1500, "km": 2400.0, "camiones": 2, "dias": 35, "dias_cerrados": 33, "descartados": 10},
    "flota": [{"mes": "2026-10", "reportes": 900, "km": 1200.0, "camiones_activos": 2, "dias": 20,
               "dias_cerrados": 19, "descartados": 3}],
    "camiones": [{"patente": "AB1234", "nombre": "906", "total": 1100, "km": [1200.0, 900.0],
                  "ultimo_dia": "2026-10-20", "dias_sin_reportes": 0}],
    "sin_reportar": [{"patente": "EF9012", "nombre": None, "ultimo_dia": None, "dias_sin_reportes": None}],
}
GEOMETRIA = {"type": "LineString", "coordinates": [[-70.612345, -33.412345], [-70.6, -33.25]]}
MAPA = {
    "status": "ok", "summary": {"trip_count": 18, "vehicle_count": 5, "route_count": 2},
    "routes": [
        {"route_id": "R-1", "label": "Faena Lampa ↔ Lugar 1", "usage_count": 12, "unique_vehicle_count": 4,
         "usage_level": "HIGH", "km_mediana": 16.6, "origen": "F7", "destino": "L0", "geometry": GEOMETRIA},
        {"route_id": "R-2", "label": "Lugar 2 ↔ Lugar 3", "usage_count": 5, "unique_vehicle_count": 2,
         "usage_level": "LOW", "km_mediana": 4.2, "origen": "L2", "destino": "L3", "geometry": GEOMETRIA},
    ],
    "vehicles": [{"vehicle_id": "AB1234", "driver_name": "Juan", "driver_last_name": "Pérez",
                  "latitude": -33.456789, "longitude": -70.654321}],
    "metadata": {"window": {"desde": "2026-07-01", "hasta": "2026-09-28"},
                 "metricas": {"umbrales": {"percentil_33": 2.0, "percentil_67": 6.67}}},
}
PREDICCION = {
    "status": "ok", "generado_en": "2026-10-20T03:30:00Z", "base_hasta": "2026-10-19",
    "metricas": {"metodo": "lightgbm", "mae_publicado": 3.2, "errores": {"lightgbm": 3.2}},
    "dias": [{"fecha": "2026-10-21", "total": 5400, "elementos": 3, "celdas": [[-33.455, -70.655, 120.0]]}],
}
PREDICCION_RUTAS = {
    "status": "ok", "generado_en": "2026-10-20T03:30:00Z", "base_hasta": "2026-10-19", "metricas": {},
    "rutas": {"R-1": {"label": "Faena Lampa ↔ Lugar 1", "lineas": [[[-33.4, -70.6]]]}},
    "dias": [{"fecha": "2026-10-21", "total": 6, "elementos": 1, "celdas": [["R-1", 4.5]]}],
}
ESTADO = {
    "status": "ok", "etl_historico": True,
    "componentes": {"etl": "ok", "mariadb": "ok", "machine": "pending", "endpoints": "ok", "telemetria_api": "pending"},
    "etl": {"estado": "ejecutando", "validos": 9927497, "leidos": 10852000, "rechazos": {"x": 1}},
    "telemetria_api": None, "catalogo_vehiculos": {"camion": 113},
}
FUENTES = {"/api/estado": ESTADO, "/api/camiones": CAMIONES, "/api/dashboard": DASHBOARD, "/api/mapa": MAPA,
           "/api/prediccion": PREDICCION, "/api/prediccion-rutas": PREDICCION_RUTAS}
PRIVADOS = ("Juan", "Pérez", "Ana Soto", "-33.45", "-70.65", "-33.41", "-70.61", "-33.11")


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def gemini_reply(text):
    return FakeResponse(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())


class AllowAll:
    def acquire(self):
        return 0


class ReadQuestionTests(unittest.TestCase):
    def test_valid_question_is_normalized(self):
        self.assertEqual(chat_api.read_question(json.dumps({"pregunta": "  ¿Cuántos\n camiones? "}).encode()),
                         ("¿Cuántos camiones?", None))

    def test_invalid_questions(self):
        for raw in (b"no es json", b"[]", b'{"pregunta": 3}', b'{"pregunta": "   "}', b"\xff"):
            question, error = chat_api.read_question(raw)
            self.assertIsNone(question)
            self.assertTrue(error)
        _, error = chat_api.read_question(json.dumps({"pregunta": "a" * 501}).encode())
        self.assertIn("500", error)
        self.assertEqual(chat_api.read_question(json.dumps({"pregunta": "a" * 500}).encode())[0], "a" * 500)


class ContextTests(unittest.TestCase):
    def test_readme_sections_bring_business_rules(self):
        docs = chat_api.documentation()
        for text in ("## Reglas clave", "percentil 67", "Parada:", "## Web", "LightGBM"):
            self.assertIn(text, docs)
        self.assertNotIn("mermaid", docs)
        self.assertNotIn("## Seguridad", docs)

    def test_summary_only_aggregates_without_personal_data(self):
        summary = chat_api.data_summary(FUENTES.get)
        sistema = summary["sistema"]
        self.assertEqual(sistema["reportes_gps_guardados"], {"historico_json": 9927497, "api_empresa": None})
        self.assertEqual((sistema["carga_historico"], sistema["conexion_api_empresa"]), ("ejecutando", "sin configurar"))
        self.assertEqual(sistema["servicios"]["machine"], "pending")
        flota = summary["flota_ahora"]
        self.assertEqual(flota["resumen"]["km_hoy"], 210.5)
        self.assertEqual([c["patente"] for c in flota["mas_km_hoy"]], ["AB1234", "CD5678"])
        self.assertEqual(summary["reportes_diarios"]["camiones_con_mas_reportes"][0]["km"], 2100.0)
        rutas = summary["rutas_clasificadas"]
        self.assertEqual(rutas["mas_usadas"][0]["ruta"], "Faena Lampa ↔ Lugar 1")
        self.assertEqual(rutas["menos_usadas"][0]["viajes"], 5)
        self.assertEqual(rutas["rutas_por_nivel"], {"verde (más usada)": 1, "rojo (poco usada)": 1})
        self.assertEqual(summary["prediccion_actividad"]["por_dia"][0]["total"], 5400)
        self.assertEqual(summary["prediccion_actividad"]["metricas"], {"metodo": "lightgbm", "mae_publicado": 3.2})
        self.assertEqual(summary["prediccion_rutas"]["por_dia"][0]["con_mas_camiones"],
                         [{"ruta": "Faena Lampa ↔ Lugar 1", "camiones": 4.5}])
        text = json.dumps(summary, ensure_ascii=False)
        for private in (*PRIVADOS, "conductor", "driver", "coordinates", "lat", "lon"):
            self.assertNotIn(private, text)

    def test_pending_and_failing_sources(self):
        def fetch(path):
            if path == "/api/estado":
                raise RuntimeError("caída")
            if path == "/api/mapa":
                return api.PENDING_RESPONSES["/api/mapa"]
            return {"status": "pending"} if path == "/api/dashboard" else None

        with mock.patch("builtins.print"):
            summary = chat_api.data_summary(fetch)
        self.assertEqual(summary["sistema"], "sin datos")
        self.assertEqual(summary["reportes_diarios"], "pendiente")
        self.assertEqual(summary["rutas_clasificadas"], "pendiente")
        self.assertEqual(summary["flota_ahora"], "sin datos")


class RateLimiterTests(unittest.TestCase):
    def test_per_minute_and_per_day(self):
        now = [0.0]
        limiter = chat_api.RateLimiter(per_minute=2, per_day=3, clock=lambda: now[0])
        self.assertEqual((limiter.acquire(), limiter.acquire()), (0, 0))
        self.assertEqual(limiter.acquire(), 60)
        now[0] = 61
        self.assertEqual(limiter.acquire(), 0)
        now[0] = 200
        self.assertGreater(limiter.acquire(), 3600)  # cuota diaria agotada
        now[0] = 86401
        self.assertEqual(limiter.acquire(), 0)


@mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba", "GEMINI_MODEL": ""})
class AnswerTests(unittest.TestCase):
    def setUp(self):
        llm._used.model = None

    def test_answer_sends_rules_and_aggregates(self):
        calls = []

        def generate(system, question):
            calls.append((system, question))
            return "Hoy la flota lleva 210,5 km."

        status, payload = chat_api.answer("¿Cuántos km hoy?", FUENTES.get, generate, AllowAll())
        self.assertEqual(status, 200)
        self.assertEqual(payload["respuesta"], "Hoy la flota lleva 210,5 km.")
        self.assertEqual(payload["modelo"], "gemini-flash-latest")
        system, question = calls[0]
        self.assertEqual(question, "¿Cuántos km hoy?")
        self.assertIn("Nunca inventes", system)
        self.assertIn("percentil 67", system)
        self.assertIn('"km_hoy": 210.5', system)
        for private in PRIVADOS:
            self.assertNotIn(private, system)

    def test_missing_key_is_clear_and_skips_model(self):
        generate = mock.Mock()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            status, payload = chat_api.answer("hola", FUENTES.get, generate, AllowAll())
        self.assertEqual(status, 503)
        self.assertIn("GEMINI_API_KEY", payload["error"])
        generate.assert_not_called()

    def test_model_failure_and_rate_limit(self):
        def failing(system, question):
            raise llm.LLMUnavailable("Gemini no está disponible en este momento.")

        self.assertEqual(chat_api.answer("hola", FUENTES.get, failing, AllowAll()),
                         (503, {"status": "unavailable", "error": "Gemini no está disponible en este momento."}))
        limiter = chat_api.RateLimiter(per_minute=1, per_day=10, clock=lambda: 0.0)
        limiter.acquire()
        status, payload = chat_api.answer("hola", FUENTES.get, mock.Mock(), limiter)
        self.assertEqual(status, 429)
        self.assertIn("60 s", payload["error"])


@mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba", "GEMINI_MODEL": "gemini-x"})
class GeminiClientTests(unittest.TestCase):
    def test_request_and_response(self):
        seen = {}

        def opener(request, timeout):
            seen["request"], seen["timeout"] = request, timeout
            return gemini_reply("Respuesta")

        self.assertEqual(llm.generate("reglas", "pregunta", opener), "Respuesta")
        request = seen["request"]
        self.assertEqual(request.full_url,
                         "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent")
        self.assertEqual(request.get_header("X-goog-api-key"), "clave-de-prueba")
        self.assertNotIn("clave-de-prueba", request.full_url)
        body = json.loads(request.data)
        self.assertEqual(body["system_instruction"]["parts"][0]["text"], "reglas")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "pregunta")

    def test_errors_become_clear_messages(self):
        def raising(error):
            def opener(request, timeout):
                raise error
            return opener

        cases = [
            (HTTPError("u", 429, "quota", {}, None), "límite gratuito"),
            (HTTPError("u", 400, "bad", {}, None), "GEMINI_API_KEY"),
            (HTTPError("u", 500, "err", {}, None), "no está disponible"),
            (URLError("sin red"), "conexión"),
            (TimeoutError(), "tardó demasiado"),
        ]
        for error, message in cases:
            with self.assertRaises(llm.LLMUnavailable) as raised:
                llm.generate("s", "q", raising(error), sleep=lambda seconds: None)
            self.assertIn(message, str(raised.exception))
        with self.assertRaises(llm.LLMUnavailable):
            llm.generate("s", "q", lambda request, timeout: FakeResponse(b'{"candidates": []}'))

    def test_retries_once_when_gemini_is_busy(self):
        replies = [HTTPError("u", 503, "busy", {}, None), gemini_reply("Listo")]

        def opener(request, timeout):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        waits = []
        self.assertEqual(llm.generate("s", "q", opener, sleep=waits.append), "Listo")
        self.assertEqual(waits, [llm.RETRY_SECONDS])
        self.assertEqual(llm.model_name(), "gemini-x")

    def test_falls_back_without_retrying_on_quota_or_hang(self):
        for error in (HTTPError("u", 429, "quota", {}, None), TimeoutError()):
            urls, waits = [], []

            def opener(request, timeout):
                urls.append(request.full_url)
                if "gemini-x" in request.full_url:
                    raise error
                return gemini_reply("Con respaldo")

            self.assertEqual(llm.generate("s", "q", opener, sleep=waits.append), "Con respaldo")
            self.assertEqual([u.split("/models/")[1].split(":")[0] for u in urls], ["gemini-x", llm.FALLBACK_MODEL])
            self.assertEqual(waits, [])  # ni cuota ni cuelgue mejoran esperando

    def test_falls_back_to_lite_model_when_main_is_saturated(self):
        urls = []

        def opener(request, timeout):
            urls.append(request.full_url)
            if "gemini-x" in request.full_url:
                raise HTTPError("u", 503, "busy", {}, None)
            return gemini_reply("Con respaldo")

        self.assertEqual(llm.generate("s", "q", opener, sleep=lambda seconds: None), "Con respaldo")
        self.assertEqual([u.split("/models/")[1].split(":")[0] for u in urls],
                         ["gemini-x", "gemini-x", llm.FALLBACK_MODEL])
        self.assertEqual(llm.model_name(), llm.FALLBACK_MODEL)

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "  "}):
            self.assertFalse(llm.configured())
            with self.assertRaises(llm.LLMUnavailable):
                llm.generate("s", "q", mock.Mock())


@mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba"})
class ChatEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body):
        request = Request(self.url + path, data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_post_chat_uses_cached_results(self):
        with mock.patch.object(api, "chat_source", FUENTES.get), \
                mock.patch.object(chat_api, "limiter", AllowAll()), \
                mock.patch.object(llm, "generate", return_value="Hay 1 camión en ruta.") as generate, \
                mock.patch.object(api.Handler, "log_message"):
            status, payload = self.post("/api/chat", json.dumps({"pregunta": "¿En ruta?"}).encode())
        self.assertEqual((status, payload["respuesta"]), (200, "Hay 1 camión en ruta."))
        self.assertIn('"en_ruta": 1', generate.call_args.args[0])

    def test_post_validation(self):
        with mock.patch.object(api.Handler, "log_message"):
            self.assertEqual(self.post("/api/otra", b"{}")[0], 404)
            self.assertEqual(self.post("/api/chat", b"x" * 5000)[0], 413)
            status, payload = self.post("/api/chat", b'{"pregunta": ""}')
        self.assertEqual((status, payload["error"]), (400, "Escribe una pregunta."))


if __name__ == "__main__":
    unittest.main()
