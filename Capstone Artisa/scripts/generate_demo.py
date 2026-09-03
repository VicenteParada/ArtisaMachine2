"""Genera un CSV sintético reproducible; nunca sobrescribe un origen existente."""

import csv
from pathlib import Path
import random


def main():
    randomizer = random.Random(42)
    cities = ["Antofagasta", "Calama", "Mejillones", "Tocopilla", "Iquique", "Copiapó"]
    rows = []
    for index in range(1, 121):
        origin, destination = randomizer.sample(cities, 2)
        distance = round(randomizer.uniform(30, 900), 1)
        load = randomizer.randint(1000, 24000)
        stops = randomizer.randint(0, 6)
        # Relación ficticia y ruido para demostrar entrenamiento y evaluación.
        duration = round(0.5 + distance/62 + load/40000 + stops*0.3 + randomizer.gauss(0, 0.18), 2)
        rows.append({"id": f"ART-{index:03}", "origen": f" {origin.upper()} ", "destino": destination,
                     "distancia_km": str(distance).replace(".", ",") if index % 5 == 0 else distance,
                     "carga_kg": load, "paradas": stops, "duracion_horas": duration})
    rows.extend([
        dict(rows[0]),
        {**rows[1], "id": "INVALIDO-1", "distancia_km": -10},
        {**rows[2], "id": "INVALIDO-2", "carga_kg": "sin dato"},
        {**rows[3], "id": "INVALIDO-3", "destino": ""},
    ])
    folder = Path(__file__).resolve().parent.parent / "origen"
    folder.mkdir(exist_ok=True)
    with (folder / "viajes.csv").open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("Origen de demostracion creado: 124 filas, 120 validas y 4 descartables.")


if __name__ == "__main__":
    main()
