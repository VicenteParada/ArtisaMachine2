import csv
import io
import unittest

from ETLs.transform import transform
from common.validation import features


ROW = {"id": "art-001", "origen": "  ANTOFAGASTA ", "destino": "copiapó", "distancia_km": "120,5",
       "carga_kg": "8500", "paradas": "2", "duracion_horas": "3,25"}


def source(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(ROW))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


class TransformationTests(unittest.TestCase):
    def test_normalizes_decimal_comma_cities_and_identifier(self):
        rows, rejected, version = transform(source([ROW]))
        self.assertEqual(rows[0], {"id": "ART-001", "origen": "Antofagasta", "destino": "Copiapó",
                                  "distancia_km": 120.5, "carga_kg": 8500.0, "paradas": 2, "duracion_horas": 3.25})
        self.assertEqual(rejected, [])
        self.assertEqual(len(version), 64)

    def test_rejects_duplicate_nonfinite_negative_missing_city_and_fractional_stops(self):
        raw = source([ROW, ROW, {**ROW, "id": "B", "carga_kg": "NaN"},
                      {**ROW, "id": "C", "distancia_km": "-1"},
                      {**ROW, "id": "D", "destino": " "}, {**ROW, "id": "E", "paradas": "1.5"}])
        rows, rejected, _ = transform(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rejected), 5)
        self.assertEqual([item["fila"] for item in rejected], [3, 4, 5, 6, 7])

    def test_rejects_empty_source_and_wrong_headers(self):
        for raw in (source([]), b"wrong,header\na,b\n", source([{**ROW, "destino": ""}])):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                transform(raw)

    def test_prediction_validation_rejects_missing_boolean_nan_and_invalid_ranges(self):
        valid = {"distancia_km": 100, "carga_kg": 8000, "paradas": 2}
        for invalid in ({}, {**valid, "distancia_km": True}, {**valid, "distancia_km": float("inf")},
                        {**valid, "paradas": 0.5}, {**valid, "carga_kg": 40001}, {**valid, "carga_kg": None}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                features(invalid)


if __name__ == "__main__":
    unittest.main()
