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

    def test_error_by_horizon_covers_each_forecast_day(self):
        matriz, lista = matriz_semanal(dias=70, celdas=50)
        prueba = list(range(50, 70))
        errores = prediccion.error_por_horizonte({"metodo": "promedio_dia_semana", "modelo": None},
                                                 matriz, lista, prueba, 15)
        self.assertEqual(len(errores), 15)
        self.assertTrue(all(e is not None and e >= 0 for e in errores[:15]))

    def test_route_segments_follow_the_road_cells(self):
        celdas = prediccion.trazar(-22.455, -68.925, -22.455, -68.895)  # 3 km hacia el este
        self.assertEqual(celdas[0], prediccion.celda(-22.455, -68.925))
        self.assertEqual(celdas[-1], prediccion.celda(-22.455, -68.895))
        self.assertEqual(len(celdas), 4)  # celdas vecinas, sin saltos
        self.assertTrue(all(abs(a[1] - b[1]) <= 1 and abs(a[0] - b[0]) <= 1 for a, b in zip(celdas, celdas[1:])))

    def test_counts_distinct_trucks_per_route_segment(self):
        t = datetime(2026, 10, 6, 15)
        def pasada(camion, desfase):
            # El mismo camino, con reportes en puntos distintos: debe dar los mismos tramos.
            return [{"asset_id": camion, "report_date": t + timedelta(minutes=k), "speed": 60,
                     "latitude": -22.455, "longitude": -68.925 + desfase + 0.012 * k} for k in range(3)]
        filas = pasada("A", 0.0) + pasada("A", 0.0) + pasada("B", 0.003)
        filas.append({"asset_id": "C", "report_date": t, "speed": 60, "latitude": -22.455, "longitude": -68.925})
        filas.append({"asset_id": "C", "report_date": t + timedelta(minutes=1), "speed": 60,
                      "latitude": -20.0, "longitude": -68.925})  # salto de GPS: no es tramo
        filas.append({"asset_id": "D", "report_date": t, "speed": 60, "latitude": -22.455, "longitude": -68.925})
        filas.append({"asset_id": "D", "report_date": t + timedelta(minutes=30), "speed": 60,
                      "latitude": -22.455, "longitude": -68.913})  # 30 min sin reportes: no es tramo
        conteo = prediccion.contar_tramos(filas)
        tramo = prediccion.celda(-22.455, -68.915) + prediccion.celda(-22.455, -68.905)
        self.assertEqual(conteo[tramo], 2)  # A y B, aunque A aparezca dos veces
        self.assertEqual(max(conteo.values()), 2)

    def test_complete_route_gets_one_value(self):
        # Ruta de 3 km hacia el este: 3 tramos. Mediana de lo predicho en ellos.
        geometria = {"type": "LineString", "coordinates": [[-68.925, -22.455], [-68.895, -22.455]]}
        lineas = prediccion.lineas_ruta(geometria)
        ruta = {"route_id": "R-1", "lineas": lineas, "tramos": set()}
        for (la0, lo0), (la1, lo1) in zip(lineas[0], lineas[0][1:]):
            ruta["tramos"] |= prediccion.tramos_segmento(la0, lo0, la1, lo1)
        self.assertEqual(len(ruta["tramos"]), 3)
        celdas = sorted(ruta["tramos"]) + [(1, 1, 1, 2)]
        dia = date(2026, 10, 16)
        pronostico = {dia: np.array([2.0, 8.0, 3.0, 50.0])}  # el último tramo no es de la ruta
        self.assertEqual(prediccion.valores_rutas([ruta], celdas, pronostico), {"R-1": {dia: 3.0}})
        # Un tramo de la ruta sin predicción cuenta como 0.
        self.assertEqual(prediccion.valores_rutas([ruta], celdas[1:], {dia: np.array([8.0, 3.0, 50.0])}),
                         {"R-1": {dia: 3.0}})

    def test_simplify_keeps_shape(self):
        recta = [(0.0, 0.001 * k) for k in range(20)]
        self.assertEqual(prediccion.simplificar(recta), [recta[0], recta[-1]])
        curva = recta[:10] + [(0.001 * k, 0.009) for k in range(1, 10)]
        self.assertEqual(prediccion.simplificar(curva), [curva[0], curva[9], curva[-1]])  # se queda la esquina

    def test_publishes_only_days_with_few_errors(self):
        # Error por distancia: se publica hasta el último día con error <= 1,3 x el primero.
        errores = [1.0, 1.05, 1.1, 1.2, 1.25, 1.28, 1.3, 1.35, 1.5, 1.6]
        self.assertEqual(prediccion.dias_confiables(errores, 15), 7)
        self.assertEqual(prediccion.dias_confiables([1.0] * 15, 15), 15)
        self.assertEqual(prediccion.dias_confiables([1.0, 2.0, 3.0], 15), 7)   # mínimo 7 días
        self.assertEqual(prediccion.dias_confiables(None, 9), 9)

    def test_forecast_starts_tomorrow_even_if_yesterday_is_not_closed(self):
        hoy = date(2026, 10, 15)
        fechas, pasados = prediccion.fechas_pronostico(date(2026, 10, 13), hoy)  # el 14 aún sin cerrar
        self.assertEqual((fechas[0], fechas[-1], pasados), (date(2026, 10, 14), date(2026, 10, 30), 2))
        publicados = [f for f in fechas if f > hoy]
        self.assertEqual((publicados[0], len(publicados)), (date(2026, 10, 16), prediccion.DIAS_ADELANTE))
        # Con el día de ayer cerrado solo hoy se encadena sin publicarse.
        self.assertEqual(prediccion.fechas_pronostico(date(2026, 10, 14), hoy)[1], 1)
        # El mínimo de días publicados se cuenta después de los que no se publican.
        self.assertEqual(prediccion.dias_confiables([1.0, 2.0, 3.0], 17, minimo=2 + 7), 9)

    def test_real_error_compares_previous_forecast_with_closed_day(self):
        lista = [date(2026, 10, 13), date(2026, 10, 14)]
        celdas = [(-2246, -6893), (-2245, -6893)]
        matriz = np.array([[5, 8], [1, 0]], dtype=np.float32)          # lo que pasó el 14: 8 y 0
        anterior = {"id": 7, "base_hasta": date(2026, 10, 13)}
        predicho = [{"fecha": date(2026, 10, 14), "celda_lat": -2246, "celda_lon": -6893, "valor": 6.0},
                    {"fecha": date(2026, 10, 14), "celda_lat": -2200, "celda_lon": -6800, "valor": 2.0}]

        class Cursor:
            def execute(self, sql, params):
                self.params = params
            def fetchall(self):
                return predicho
        cursor = Cursor()
        real = prediccion.error_real(cursor, anterior, "calor", celdas, matriz, lista)
        # Errores: |6-8|=2, |0-0|=0 y 2 donde se esperaba actividad y no hubo ninguna.
        self.assertEqual(real, {"fecha": "2026-10-14", "mae": round(4 / 3, 3), "esperado": 8, "real": 8})
        self.assertEqual(cursor.params, (7, date(2026, 10, 14), date(2026, 10, 14)))
        # Sin días nuevos cerrados no hay medición nueva.
        self.assertIsNone(prediccion.error_real(cursor, {"id": 7, "base_hasta": date(2026, 10, 14)},
                                                "calor", celdas, matriz, lista))

    def test_horizon_counts_discarded_days(self):
        # Se publicó hasta el 20, pero se intentó hasta el 27: no hay que recalcular cada 15 min.
        version = {"hasta": date(2026, 10, 20), "metricas": {"horizonte_hasta": "2026-10-27"}}
        self.assertEqual(prediccion.horizonte(version), "2026-10-27")
        self.assertEqual(prediccion.horizonte({"hasta": date(2026, 10, 20), "metricas": {}}), "2026-10-20")

    def test_new_trucks_change_the_fingerprint(self):
        self.assertNotEqual(prediccion.huella(["A", "B"]), prediccion.huella(["A", "B", "C"]))
        self.assertEqual(prediccion.huella(["B", "A"]), prediccion.huella(["A", "B"]))


if __name__ == "__main__":
    unittest.main()
