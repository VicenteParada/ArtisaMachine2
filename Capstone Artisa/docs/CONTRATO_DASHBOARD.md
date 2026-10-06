# Contrato del Dashboard diario

El Dashboard muestra jornadas **cerradas**. Sin `fecha`, `GET /api/dashboard`
elige el último día con reportes anterior a hoy en `America/Santiago`; con
`?fecha=YYYY-MM-DD` consulta ese día. `dias=7` o `dias=30` elige la longitud
de la tendencia (7 por defecto); no cambia los totales del día seleccionado.
La fecha de hoy se rechaza. El intervalo
es semiabierto: desde las 00:00 locales incluidas hasta las 00:00 locales del
día siguiente excluidas. La API convierte ambos límites a UTC para consultar
`raw_gps.report_date`, que el ETL guarda como `DATETIME` UTC sin zona. El límite
UTC puede cambiar con el horario de verano.

La respuesta existente entrega `reportes` y `vehiculos` de camiones en `raw_gps`.
`vehiculos` cuenta `asset_id` distintos clasificados como camión por el último
nombre GPS no vacío del día; `reportes` suma los registros de esos camiones.
Se excluyen camionetas, furgones y nombres sin tipo reconocible. Esta
clasificación por nombre es provisional hasta disponer de un catálogo de tipos
de vehículo. Estos datos son **históricos**, nunca
estado actual de la flota. `status` es `ready` cuando hay reportes y `empty`
cuando el día no tiene ninguno. La API no crea ni modifica tablas.

## Datos de rutas que faltan

El punto de integración es `dashboard_routes(cursor, fecha, days)` en
`endpoints/api.py`. Hoy devuelve `status: "pending"` y valores vacíos. Cuando
Machine publique resultados diarios en MariaDB, conectar ahí la lectura de la
tabla acordada, conservando esta estructura JSON:

```json
{
  "rutas": {
    "status": "ready",
    "metrica": "vehiculos_unicos_por_ruta",
    "niveles": {"HIGH": 3, "MEDIUM": 4, "LOW": 5},
    "mas_usadas": [
      {"route_id": "R-1", "nombre": "Ruta ejemplo", "vehiculos_unicos": 12, "nivel": "HIGH"}
    ],
    "menos_usadas": [
      {"route_id": "R-2", "nombre": "Otra ruta ejemplo", "vehiculos_unicos": 1, "nivel": "LOW"}
    ],
    "tendencia": [
      {"fecha": "2026-09-02", "pasos_camion_ruta": 18}
    ]
  }
}
```

Los valores del ejemplo son ilustrativos. `niveles` contiene el número de
rutas observadas y clasificadas ese día en cada categoría. Cada
`vehiculos_unicos` cuenta cada `asset_id` una sola vez por ruta y día,
aunque tenga muchos puntos GPS o repita el trayecto. Los rankings incluyen
solo rutas observadas: una ruta sin datos no debe aparecer como «poco usada».
`mas_usadas` y `menos_usadas` pueden traer hasta ocho rutas cada una. El
frontend las ordena y usa `nivel` para mantener verde, naranja y rojo.
La clasificación `HIGH`/`MEDIUM`/`LOW` debe venir del mismo cálculo que alimenta
el mapa; el Dashboard no define umbrales propios.

`tendencia` cubre hasta `days` días cerrados, terminando en `fecha`.
`pasos_camion_ruta` es la suma diaria de pares distintos `(ruta, asset_id)`;
el mismo camión puede contar en dos rutas diferentes. Si el cálculo de rutas
de un día aún no terminó, mantener `status: "pending"` para ese día y no
publicar ceros como si fueran resultados definitivos. Los reportes atrasados
deben asignarse a la jornada de su `report_date` y permitir recalcular su
resumen de forma idempotente.

La tabla y el proceso de inyección de rutas todavía no están definidos en el
repositorio. Hasta acordarlos, la API muestra los conteos GPS reales y deja
los cuatro gráficos de rutas claramente pendientes; no infiere rutas a partir
de la cantidad de reportes GPS.
