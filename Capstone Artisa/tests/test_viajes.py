from datetime import datetime, timedelta
import unittest

from machine_learning.viajes import detectar_paradas, empalmar, extraer_viajes

A = (-33.40, -70.60)
B = (-33.25, -70.60)   # ~16,6 km al norte


def quieto(t, lugar, minutos):
    return [(t + timedelta(minutes=m), lugar[0] + (m % 3) * 0.0001, lugar[1]) for m in range(minutos)]


def manejar(t, desde, hasta, minutos):
    return [(t + timedelta(minutes=m), desde[0] + (hasta[0] - desde[0]) * m / minutos,
             desde[1] + (hasta[1] - desde[1]) * m / minutos) for m in range(1, minutos)]


def jornada(t0):
    puntos = quieto(t0, A, 30)
    t = puntos[-1][0]
    puntos += manejar(t, A, B, 20)
    t = puntos[-1][0] + timedelta(minutes=1)
    puntos += quieto(t, B, 40)
    t = puntos[-1][0]
    puntos += manejar(t, B, A, 20)
    t = puntos[-1][0] + timedelta(minutes=1)
    puntos += quieto(t, A, 30)
    return puntos


class TripTests(unittest.TestCase):
    def test_trips_between_stops(self):
        puntos = jornada(datetime(2026, 10, 6, 12))
        self.assertEqual(len(detectar_paradas(puntos)), 3)
        viajes = extraer_viajes("RJXS30", puntos)
        self.assertEqual(len(viajes), 2)
        ida, vuelta = viajes
        self.assertAlmostEqual(ida.origen[0], A[0], places=2)
        self.assertAlmostEqual(ida.destino[0], B[0], places=2)
        self.assertAlmostEqual(vuelta.destino[0], A[0], places=2)
        self.assertAlmostEqual(ida.km, 16.6, delta=0.6)
        self.assertFalse(ida.con_saltos)
        # La geometría es de puntos reales simplificados: una recta queda en pocos puntos.
        self.assertLessEqual(len(ida.tramos[0]), 4)

    def test_gps_jump_is_not_drawn(self):
        puntos = jornada(datetime(2026, 10, 6, 12))
        k = 40
        puntos[k] = (puntos[k][0], -20.0, -70.0)   # salto de GPS a 1.500 km
        ida = extraer_viajes("X", puntos)[0]
        self.assertTrue(ida.con_saltos)
        self.assertGreaterEqual(len(ida.tramos), 2)
        for tramo in ida.tramos:
            self.assertTrue(all(-34 < lat < -33 for lat, _ in tramo) or len(tramo) == 1)
        self.assertLess(ida.km, 20)

    def test_long_gap_cuts_trip_without_destination(self):
        t0 = datetime(2026, 10, 6, 12)
        puntos = quieto(t0, A, 30) + manejar(t0 + timedelta(minutes=29), A, B, 20)
        corte = puntos[-1][0] + timedelta(hours=2)
        puntos += [(corte + timedelta(minutes=m), B[0] + 0.01 * m / 20, B[1]) for m in range(20)]
        puntos += quieto(puntos[-1][0], (B[0] + 0.01, B[1]), 30)
        viajes = extraer_viajes("X", puntos)
        self.assertIsNone(viajes[0].destino)
        self.assertTrue(all(v.destino is None or v.origen is None or v is viajes[-1] for v in viajes))

    def test_midnight_split_is_stitched(self):
        medianoche = datetime(2026, 10, 7, 3)  # 00:00 de Chile en UTC
        puntos = quieto(medianoche - timedelta(minutes=40), A, 30)
        puntos += manejar(puntos[-1][0], A, B, 20)
        puntos += quieto(puntos[-1][0] + timedelta(minutes=1), B, 30)
        antes = [p for p in puntos if p[0] < medianoche]
        despues = [p for p in puntos if p[0] >= medianoche]
        dia1 = extraer_viajes("X", antes, medianoche - timedelta(days=1), medianoche)
        dia2 = extraer_viajes("X", despues, medianoche, medianoche + timedelta(days=1))
        self.assertTrue(dia1[-1].abierto_fin)
        self.assertTrue(dia2[0].abierto_inicio)
        (unido,) = [v for v in empalmar(sorted(dia1 + dia2, key=lambda v: v.inicio)) if v.km > 1]
        self.assertIsNotNone(unido.origen)
        self.assertIsNotNone(unido.destino)
        self.assertAlmostEqual(unido.km, 16.6, delta=0.8)
        self.assertFalse(unido.con_saltos)


if __name__ == "__main__":
    unittest.main()
