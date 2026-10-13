"""Predicción de actividad: conteo por celda, entrenamiento sin fuga y pronóstico."""

from datetime import date, datetime, timedelta
import unittest

try:
    import numpy as np
    from machine_learning import prediccion
except ModuleNotFoundError as error:  # numpy se valida dentro del contenedor Machine
    if error.name != "numpy":
        raise
    prediccion = None

try:
    import lightgbm  # noqa: F401
    HAY_LIGHTGBM = True
except ModuleNotFoundError:
    HAY_LIGHTGBM = False


def matriz_semanal(dias=56, celdas=300, semilla=1):
    """Actividad con patrón semanal claro: lunes a viernes mucho, fin de semana poco."""
    rng = np.random.default_rng(semilla)
    base = rng.gamma(2.0, 10.0, size=celdas)
    lista = [date(2026, 8, 17) + timedelta(days=k) for k in range(dias)]
    factor = np.array([1.0 if d.weekday() < 5 else 0.15 for d in lista])
    matriz = rng.poisson(np.outer(base, factor)).astype(np.float32)
    return matriz, lista


@unittest.skipIf(prediccion is None, "numpy se valida dentro del contenedor Machine")
class PrediccionTests(unittest.TestCase):
    def test_counts_moving_reports_once_per_cell(self):
        t = datetime(2026, 10, 6, 15)
        filas = [
            {"asset_id": "A", "report_date": t, "latitude": -22.4561, "longitude": -68.9234, "speed": 60},
            {"asset_id": "A", "report_date": t, "latitude": -22.4561, "longitude": -68.9234, "speed": 60},  # repetido
            {"asset_id": "B", "report_date": t, "latitude": -22.4502, "longitude": -68.9201, "speed": 40},
            {"asset_id": "C", "report_date": t, "latitude": -22.4502, "longitude": -68.9201, "speed": 1},   # detenido
            {"asset_id": "D", "report_date": t, "latitude": 0.0, "longitude": 0.0, "speed": 80},          # GPS inválido
        ]
        conteo = prediccion.contar_filas(filas)
        self.assertEqual(sum(conteo.values()), 2)
        self.assertEqual(conteo[prediccion.celda(-22.4561, -68.9234)], 2)  # misma celda de 0,01°
        self.assertEqual(prediccion.celda(-22.4561, -68.9234), (-2246, -6893))
        self.assertAlmostEqual(prediccion.centro(-2246), -22.455)

    def test_features_only_look_at_previous_days(self):
        matriz, lista = matriz_semanal(dias=20, celdas=5)
        d = 14
        X = prediccion.features(matriz, d, lista[d].weekday())
        alterada = matriz.copy()
        alterada[:, d:] = 9999  # el día predicho y los siguientes no deben influir
        np.testing.assert_array_equal(X, prediccion.features(alterada, d, lista[d].weekday()))
        np.testing.assert_array_equal(X[:, prediccion.FEATURES.index("lag7")], matriz[:, d - 7])

    @unittest.skipUnless(HAY_LIGHTGBM, "LightGBM se valida dentro del contenedor Machine")
    def test_weekly_pattern_beats_flat_average_and_days_differ(self):
        matriz, lista = matriz_semanal()
        predictor, metricas = prediccion.entrenar(matriz, lista)
        errores = metricas["errores"]
        self.assertEqual(set(errores), {"lightgbm", "promedio_dia_semana", "promedio_historico"})
        self.assertEqual(metricas["metodo"], min(errores, key=errores.get))
        self.assertNotEqual(metricas["metodo"], "promedio_historico")  # hay patrón semanal
        self.assertIn(metricas["objetivo"], prediccion.OBJETIVOS)
        futuras = [lista[-1] + timedelta(days=k) for k in range(1, 9)]
        pronostico = prediccion.pronosticar(predictor, matriz, lista, futuras)
        self.assertEqual(sorted(pronostico), futuras)
        laboral = [pronostico[f].sum() for f in futuras if f.weekday() < 5]
        finde = [pronostico[f].sum() for f in futuras if f.weekday() >= 5]
        self.assertGreater(min(laboral), 2 * max(finde))  # distingue días, no repite el mismo mapa

    @unittest.skipUnless(HAY_LIGHTGBM, "LightGBM se valida dentro del contenedor Machine")
    def test_publishes_the_lowest_error_and_says_why(self):
        rng = np.random.default_rng(7)  # ruido puro, sin patrón
        lista = [date(2026, 8, 17) + timedelta(days=k) for k in range(30)]
        matriz = rng.poisson(5, size=(200, 30)).astype(np.float32)
        predictor, metricas = prediccion.entrenar(matriz, lista)
        errores = metricas["errores"]
        self.assertEqual(predictor["metodo"], metricas["metodo"])
        self.assertEqual(metricas["mae_publicado"], min(errores.values()))
        if metricas["metodo"] != "lightgbm":
            self.assertIsNone(predictor["modelo"])
            self.assertTrue(metricas["motivo"])

    def test_averages_forecast_without_lightgbm(self):
        matriz, lista = matriz_semanal(dias=35, celdas=4)
        futura = lista[-1] + timedelta(days=1)
        hist = prediccion.pronosticar({"metodo": "promedio_historico", "modelo": None}, matriz, lista, [futura])
        np.testing.assert_allclose(hist[futura], matriz.mean(axis=1), rtol=1e-5)
        semana = prediccion.pronosticar({"metodo": "promedio_dia_semana", "modelo": None}, matriz, lista, [futura])
        mismos = [len(lista) - 7 * k for k in range(1, 5)]  # mismo día de la semana, 4 semanas
        np.testing.assert_allclose(semana[futura], matriz[:, mismos].mean(axis=1), rtol=1e-5)

    def test_new_trucks_change_the_fingerprint(self):
        self.assertNotEqual(prediccion.huella(["A", "B"]), prediccion.huella(["A", "B", "C"]))
        self.assertEqual(prediccion.huella(["B", "A"]), prediccion.huella(["A", "B"]))


if __name__ == "__main__":
    unittest.main()
