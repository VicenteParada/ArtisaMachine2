"use strict";

(() => {
  const REQUIRED_ARRAYS = ["routes", "trips", "stops", "vehicles", "readings"];

  function asArray(value, field) {
    if (!Array.isArray(value)) throw new Error("El contrato del mapa no contiene " + field + ".");
    return value;
  }

  function routeFeature(route) {
    if (!route || !route.route_id || !route.geometry) {
      throw new Error("El contrato contiene una ruta incompleta.");
    }
    return {
      type: "Feature",
      properties: {
        route_id: route.route_id,
        label: route.label || route.route_id,
        usage_count: Number(route.usage_count || 0),
        unique_vehicle_count: Number(route.unique_vehicle_count || 0),
        usage_level: route.usage_level,
        usage_label: route.usage_label,
        color: route.color,
        geometry_source: route.geometry_source,
        observed_points: Number(route.observed_points || 0),
        map_matching: Boolean(route.map_matching)
      },
      geometry: route.geometry
    };
  }

  function adaptMapPayload(payload) {
    if (!payload || typeof payload !== "object") {
      throw new Error("La API del mapa no entregó un objeto válido.");
    }
    for (const field of REQUIRED_ARRAYS) asArray(payload[field], field);

    const routes = payload.routes.map(routeFeature);
    const routeById = new Map(
      routes.map((route) => [route.properties.route_id, route])
    );
    const ranking = payload.ranking || {};
    const rankedRoute = (value) => {
      if (!value) return null;
      return routeById.get(value.route_id) || routeFeature(value);
    };

    return Object.freeze({
      schemaVersion: payload.schema_version,
      sourceMode: payload.source_mode,
      snapshotDate: payload.snapshot_date,
      description: payload.description,
      routes,
      trips: [...payload.trips],
      stops: [...payload.stops],
      vehicles: [...payload.vehicles],
      readings: [...payload.readings],
      summary: {...(payload.summary || {})},
      bounds: payload.bounds ? {...payload.bounds} : null,
      ranking: {
        mostUsed: rankedRoute(ranking.most_used),
        leastUsed: rankedRoute(ranking.least_used)
      },
      metadata: {...(payload.metadata || {})}
    });
  }

  window.ArtisaMapData = Object.freeze({adaptMapPayload});
})();