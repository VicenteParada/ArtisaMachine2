from datetime import datetime, timedelta
import unittest

try:
    from machine_learning.rutas import ViajeRuta, clasificar_niveles, construir_rutas, dentro_geojson
except ModuleNotFoundError as error:  # numpy/sklearn se validan dentro del contenedor Machine
    if error.name not in {"numpy", "sklearn"}:
        raise
    construir_rutas = None

A = (-33.400, -70.600)
B = (-33.250, -70.600)
C = (-33.400, -70.400)
FAENA_A = {"id": 7, "nombre": "Faena Lampa", "geojson": {"type": "Polygon", "coordinates": [[
    [-70.605, -33.405], [-70.595, -33.405], [-70.595, -33.395], [-70.605, -33.395], [-70.605, -33.405]]]}}


def recorrido(desde, hasta, desvio_lon=0.0, ruido=0.0, n=30):
    puntos = []
    for k in range(n + 1):
        f = k / n
        curva = desvio_lon * (1 - abs(2 * f - 1))  # se aleja y vuelve: otra calle
        puntos.append((desde[0] + (hasta[0] - desde[0]) * f + ruido * ((k % 3) - 1),
                       desde[1] + (hasta[1] - desde[1]) * f + curva))
    return puntos


def viaje(asset, n, desde, hasta, **kw):
    t = datetime(2026, 9, 1, 12) + timedelta(hours=n)
    puntos = recorrido(desde, hasta, **kw)
    return ViajeRuta(asset_id=asset, inicio=t, fin=t + timedelta(minutes=30), origen=puntos[0],
                     destino=puntos[-1], km=17.0, puntos=len(puntos), con_saltos=False, tramos=[puntos])


@unittest.skipIf(construir_rutas is None, "numpy/scikit-learn se validan dentro del contenedor Machine")
class RouteTests(unittest.TestCase):
    def setUp(self):
        self.viajes = []
        n = 0
        for i in range(12):   # camino directo A-B, en ambos sentidos y con ruido GPS
            ida = i % 2 == 0
            self.viajes.append(viaje(f"T{i % 4}", n, A if ida else B, B if ida else A, ruido=0.0003)); n += 1
        for i in range(4):    # desvío real: 4 km al este
            self.viajes.append(viaje(f"T{i}", n, A, B, desvio_lon=0.045)); n += 1
        for i in range(2):    # A-C poco usado
            self.viajes.append(viaje("T9", n, A, C)); n += 1

    def test_routes_are_separated_by_origin_destination_and_path(self):
        rutas, lugares, metricas = construir_rutas(self.viajes, [FAENA_A])
        conteos = sorted((r["usage_count"] for r in rutas), reverse=True)
        self.assertEqual(conteos, [12, 4, 2])
        directa, desvio, ac = rutas
        self.assertEqual(directa["unique_vehicle_count"], 4)
        self.assertIn("Faena Lampa", directa["label"])
        self.assertEqual(directa["usage_level"], "HIGH")
        self.assertEqual(ac["usage_level"], "LOW")
        self.assertEqual(metricas["viajes_clasificados"], 18)

    def test_geometry_is_a_real_observed_trip(self):
        rutas, _, _ = construir_rutas(self.viajes, [FAENA_A])
        observados = {tuple((round(lon, 6), round(lat, 6)) for lat, lon in v.tramos[0]) for v in self.viajes}
        observados |= {tuple(reversed(t)) for t in observados}
        for ruta in rutas:
            self.assertIn(tuple(map(tuple, ruta["geometry"]["coordinates"])), observados)
            self.assertEqual(ruta["geometry_source"], "viaje_real_medoide")

    def test_levels_by_percentile(self):
        niveles, umbrales = clasificar_niveles([1, 1, 1, 2, 3, 5, 8, 13, 40])
        self.assertEqual(niveles[0], "LOW")
        self.assertEqual(niveles[-1], "HIGH")
        self.assertIn("MEDIUM", niveles)
        self.assertEqual(clasificar_niveles([3, 3, 3])[0], ["MEDIUM"] * 3)

    def test_point_in_faena(self):
        self.assertTrue(dentro_geojson(-33.4, -70.6, FAENA_A["geojson"]))
        self.assertFalse(dentro_geojson(-33.3, -70.6, FAENA_A["geojson"]))


if __name__ == "__main__":
    unittest.main()
