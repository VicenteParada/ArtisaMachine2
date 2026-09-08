"use strict";

const $ = (id) => document.getElementById(id);
const format = (value, decimals = 0) => Number(value).toLocaleString("es-CL", {maximumFractionDigits: decimals});
const formatUtc = (value) => value ? new Intl.DateTimeFormat("es-CL", {
  dateStyle: "short", timeStyle: "medium", timeZone: "UTC"
}).format(new Date(value)) : "Sin fecha";
let trips = [];
let expanded = false;
let refreshing = false;

async function api(path, options = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(10000)});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "El servicio está temporalmente ocupado.");
  return payload;
}

function syncDatasetView() {
  const simulatedHashes = new Set(["#datos-simulados", "#viajes", "#simulador"]);
  const view = simulatedHashes.has(window.location.hash)
    ? "simulated"
    : (window.location.hash === "#mapa-calor" ? "heatmap" : "real");
  for (const space of document.querySelectorAll("[data-dataset-view]")) {
    space.hidden = space.dataset.datasetView !== view;
  }
  for (const tab of document.querySelectorAll("[data-dataset-tab]")) {
    const selected = tab.dataset.datasetTab === view;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-current", selected ? "page" : "false");
  }
  const overview = window.location.hash === "#flujo";
  $("overview-link").classList.toggle("active", overview);
  for (const link of document.querySelectorAll(".sidebar [data-dataset-link]")) {
    link.classList.toggle("active", !overview && link.dataset.datasetLink === view);
  }
  if (view === "heatmap" && fleetMap) window.setTimeout(fitPendingMap, 0);
}

const svgNode = (name, attributes = {}) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
};

function heatColor(intensity) {
  if (intensity >= 0.8) return "#c84630";
  if (intensity >= 0.55) return "#e88836";
  if (intensity >= 0.3) return "#e6c84c";
  if (intensity >= 0.12) return "#70a85b";
  return "#397c91";
}

function routeLabel(route) {
  return route ? route.etiqueta : "Sin tramos suficientes";
}

let fleetMap = null;
let heatLayer = null;
let routeLayer = null;
let stopLayer = null;
let truckLayer = null;
let pendingMapBounds = null;
let mapHasFitted = false;
const truckMarkers = new Map();

function vehicleStatus(vehicle) {
  const speed = Number(vehicle.velocidad || 0);
  if (speed > 3) return {label: "En movimiento", className: "moving"};
  if (vehicle.encendido !== null && vehicle.encendido !== undefined && Number(vehicle.encendido) === 0) return {label: "Apagado", className: "off"};
  return {label: "Detenido", className: "stopped"};
}

function initializeFleetMap() {
  const container = $("heatmap-map");
  if (fleetMap || !container) return;
  if (!window.L) {
    container.classList.add("map-unavailable");
    container.textContent = "No fue posible cargar la cartografía. Revisa la conexión a Internet y actualiza la página.";
    return;
  }
  container.replaceChildren();
  fleetMap = L.map(container, {preferCanvas: true, zoomControl: true});
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(fleetMap);
  heatLayer = L.layerGroup().addTo(fleetMap);
  routeLayer = L.layerGroup().addTo(fleetMap);
  stopLayer = L.layerGroup().addTo(fleetMap);
  truckLayer = L.layerGroup().addTo(fleetMap);
}

function fitPendingMap() {
  if (!fleetMap) return;
  fleetMap.invalidateSize();
  if (!mapHasFitted && pendingMapBounds && pendingMapBounds.isValid()) {
    fleetMap.fitBounds(pendingMapBounds, {padding: [28, 28], maxZoom: 12});
    mapHasFitted = true;
  }
}

function truckIcon(vehicle) {
  const status = vehicleStatus(vehicle);
  const heading = Number.isFinite(Number(vehicle.rumbo)) ? Number(vehicle.rumbo) : 0;
  const html = "<span class='truck-marker " + status.className + "'><span class='truck-heading' style='transform:rotate(" + heading + "deg)'>▲</span><span class='truck-glyph'>🚚</span></span>";
  return L.divIcon({className: "truck-div-icon", html, iconSize: [38, 38], iconAnchor: [19, 19], popupAnchor: [0, -17]});
}

function truckPopup(vehicle) {
  const status = vehicleStatus(vehicle);
  const wrapper = document.createElement("div");
  wrapper.className = "truck-popup";
  const title = document.createElement("strong");
  title.textContent = vehicle.asset_name || vehicle.asset_id || "Vehículo";
  const identifier = document.createElement("span");
  identifier.textContent = vehicle.asset_id || "Sin asset_id";
  const state = document.createElement("b");
  state.className = status.className;
  state.textContent = status.label;
  const details = document.createElement("p");
  const speed = vehicle.velocidad === null || vehicle.velocidad === undefined ? "Sin velocidad" : `${format(vehicle.velocidad, 1)} km/h`;
  details.textContent = `${speed} · ${vehicle.evento || "Sin evento"}`;
  const date = document.createElement("small");
  date.textContent = `Último reporte: ${formatUtc(vehicle.report_date)}`;
  wrapper.append(title, identifier, state, details, date);
  return wrapper;
}

function renderLiveFleet(vehicles) {
  const list = $("vehicle-live-list");
  if (!list) return;
  list.replaceChildren();
  $("vehicle-live-count").textContent = `${format(vehicles.length)} camiones visibles`;
  const ordered = [...vehicles].sort((a, b) => Number(b.velocidad || 0) - Number(a.velocidad || 0));
  for (const vehicle of ordered.slice(0, 14)) {
    const status = vehicleStatus(vehicle);
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = `vehicle-live-row ${status.className}`;
    const icon = document.createElement("span");
    icon.className = "vehicle-list-icon";
    icon.textContent = "🚚";
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = vehicle.asset_name || vehicle.asset_id;
    const detail = document.createElement("small");
    detail.textContent = `${status.label} · ${vehicle.velocidad === null || vehicle.velocidad === undefined ? "s/d" : `${format(vehicle.velocidad, 1)} km/h`}`;
    copy.append(name, detail);
    button.append(icon, copy);
    button.addEventListener("click", () => {
      const marker = truckMarkers.get(vehicle.asset_id);
      if (marker && fleetMap) {
        fleetMap.setView(marker.getLatLng(), Math.max(fleetMap.getZoom(), 13));
        marker.openPopup();
      }
    });
    item.append(button);
    list.append(item);
  }
  if (!vehicles.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "No hay posiciones de vehículos disponibles.";
    list.append(item);
  }
}

function renderFleetMap(data) {
  initializeFleetMap();
  if (!fleetMap) return;
  heatLayer.clearLayers();
  routeLayer.clearLayers();
  stopLayer.clearLayers();
  truckLayer.clearLayers();
  truckMarkers.clear();

  for (const cell of [...(data.celdas || [])].sort((a, b) => a.intensidad - b.intensidad)) {
    L.circle([cell.latitud, cell.longitud], {
      radius: 350 + Math.sqrt(cell.intensidad) * 2400,
      color: heatColor(cell.intensidad),
      weight: 1,
      opacity: 0.75,
      fillColor: heatColor(cell.intensidad),
      fillOpacity: 0.16 + cell.intensidad * 0.34
    }).bindTooltip(`${format(cell.reportes)} lecturas · ${format(cell.vehiculos)} vehículos`).addTo(heatLayer);
  }

  for (const route of [...(data.tramos || [])].sort((a, b) => a.intensidad - b.intensidad)) {
    L.polyline([
      [route.origen.latitud, route.origen.longitud],
      [route.destino.latitud, route.destino.longitud]
    ], {
      color: heatColor(route.intensidad),
      weight: 2 + route.intensidad * 7,
      opacity: 0.42 + route.intensidad * 0.5,
      lineCap: "round"
    }).bindTooltip(`${format(route.pasos)} cruces · ${format(route.vehiculos)} vehículos`).addTo(routeLayer);
  }

  for (const cell of (data.celdas || []).filter((value) => value.paradas > 0)) {
    L.circleMarker([cell.latitud, cell.longitud], {
      radius: 7 + Math.min(8, Math.sqrt(cell.paradas) * 2),
      color: "#6f42a6",
      weight: 3,
      fillColor: "#ffffff",
      fillOpacity: 0.85
    }).bindTooltip(`${format(cell.paradas)} paradas detectadas`).addTo(stopLayer);
  }

  const vehicles = data.vehiculos_actuales || [];
  for (const vehicle of vehicles) {
    const marker = L.marker([vehicle.latitud, vehicle.longitud], {
      icon: truckIcon(vehicle),
      zIndexOffset: 1000
    }).bindPopup(truckPopup(vehicle));
    marker.addTo(truckLayer);
    truckMarkers.set(vehicle.asset_id, marker);
  }
  renderLiveFleet(vehicles);
  $("map-last-update").textContent = data.ultima_posicion
    ? `Últimas posiciones: ${formatUtc(data.ultima_posicion)} · actualización cada 15 s`
    : "Sin posiciones recientes";

  if (data.limites) {
    pendingMapBounds = L.latLngBounds(
      [data.limites.latitud_min, data.limites.longitud_min],
      [data.limites.latitud_max, data.limites.longitud_max]
    );
  }
  if ($("heatmap-map").offsetParent !== null) requestAnimationFrame(fitPendingMap);
}

function renderHeatmap(data) {
  const summary = data.resumen || {};
  $("heatmap-reports").textContent = format(summary.reportes || 0);
  $("heatmap-vehicles").textContent = format(summary.vehiculos || 0);
  $("heatmap-stops").textContent = format(summary.paradas || 0);
  $("heatmap-routes").textContent = format(summary.tramos || 0);
  $("heatmap-zones").textContent = `${format(summary.zonas || 0)} zonas geográficas · muestra de hasta ${format(data.limite_reportes || 0)} lecturas`;

  const most = data.ranking?.mas_usado;
  const least = data.ranking?.menos_usado;
  $("most-route").textContent = routeLabel(most);
  $("most-route-usage").textContent = most ? `${format(most.pasos)} cruces · ${format(most.vehiculos)} vehículos` : "Sin información";
  $("least-route").textContent = routeLabel(least);
  $("least-route-usage").textContent = least ? `${format(least.pasos)} cruces · ${format(least.vehiculos)} vehículos` : "Sin información";
  $("heatmap-method").textContent = `${data.metodo?.descripcion_tramo || ""} Las paradas corresponden a: ${data.metodo?.descripcion_parada || "sin regla disponible"}`;

  const ranking = $("stop-ranking");
  ranking.replaceChildren();
  for (const vehicle of data.paradas_por_vehiculo || []) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = vehicle.asset_name || vehicle.asset_id;
    const count = document.createElement("strong");
    count.textContent = format(vehicle.paradas);
    item.append(name, count);
    ranking.append(item);
  }
  if (!ranking.children.length) {
    const item = document.createElement("li");
    item.textContent = "No se detectaron paradas en esta muestra.";
    ranking.append(item);
  }

  renderFleetMap(data);
}

function renderTrips() {
  const query = $("search").value.trim().toLocaleLowerCase("es");
  const filtered = trips.filter((row) => `${row.id} ${row.origen} ${row.destino}`.toLocaleLowerCase("es").includes(query));
  const visible = expanded ? filtered : filtered.slice(0, 10);
  const body = $("trip-rows");
  body.replaceChildren();
  for (const row of visible) {
    const tr = document.createElement("tr");
    const route = document.createElement("td");
    route.textContent = `${row.origen} → ${row.destino}`;
    const id = document.createElement("small");
    id.textContent = row.id;
    route.append(id);
    tr.append(route);
    for (const text of [`${format(row.distancia_km, 1)} km`, `${format(row.duracion_horas, 2)} h`]) {
      const td = document.createElement("td"); td.textContent = text; tr.append(td);
    }
    const prediction = document.createElement("td");
    const label = document.createElement("span"); label.className = "estimate";
    label.textContent = row.duracion_estimada === null ? "Pendiente" : `${format(row.duracion_estimada, 2)} h`;
    prediction.append(label); tr.append(prediction); body.append(tr);
  }
  if (!visible.length) {
    const row = document.createElement("tr"); const cell = document.createElement("td");
    cell.colSpan = 4; cell.className = "empty"; cell.textContent = "No se encontraron viajes.";
    row.append(cell); body.append(row);
  }
  $("table-count").textContent = `${visible.length} de ${filtered.length} viajes`;
  $("show-all").hidden = filtered.length <= 10;
  $("show-all").textContent = expanded ? "Ver menos" : "Ver todos";
}

function renderTelemetry(summary, dataset, state) {
  const reports = dataset.reportes || [];
  const telemetryState = state.telemetria || {};
  $("telemetry-count").textContent = format(summary.reportes);
  $("vehicle-count").textContent = format(summary.vehiculos);
  $("average-speed").textContent = `${format(summary.velocidad_promedio, 1)} km/h`;
  $("maximum-speed").textContent = `${format(summary.velocidad_maxima, 1)} km/h`;
  $("telemetry-range").textContent = summary.reportes
    ? `Desde ${formatUtc(summary.desde)} hasta ${formatUtc(summary.hasta)}`
    : "Todavía no hay reportes importados.";

  const read = Number(telemetryState.leidos || 0);
  const valid = Number(telemetryState.validos || 0);
  const rejected = Number(telemetryState.rechazados || 0);
  $("telemetry-quality").textContent = `${format(valid)} válidos · ${format(rejected)} rechazados de ${format(read)} leídos`;
  const labels = {
    ejecutando: "Extrayendo datos",
    completo: "Ventana completa",
    limite_alcanzado: "Carga controlada completa",
    esperando_nuevos_datos: "Sincronizado · próxima revisión en 15 min",
    deshabilitado: "ETL real deshabilitado",
    error: "Error en la última carga"
  };
  const status = telemetryState.estado || (summary.reportes ? "completo" : "deshabilitado");
  $("telemetry-status").textContent = labels[status] || "Datos disponibles";
  $("telemetry-status").className = `status ${status === "error" ? "warning" : ""}`;

  const body = $("telemetry-rows");
  body.replaceChildren();
  for (const report of reports) {
    const tr = document.createElement("tr");
    const vehicle = document.createElement("td");
    vehicle.textContent = report.asset_name || "Vehículo sin nombre";
    const assetId = document.createElement("small");
    assetId.textContent = report.asset_id || "Sin asset_id";
    vehicle.append(assetId);
    tr.append(vehicle);

    const date = document.createElement("td");
    date.textContent = formatUtc(report.report_date);
    tr.append(date);

    const event = document.createElement("td");
    event.textContent = report.event_name || "Sin evento";
    tr.append(event);

    const speed = document.createElement("td");
    const speedValue = report.gps_speed ?? report.speed;
    speed.textContent = speedValue === null || speedValue === undefined ? "Sin dato" : `${format(speedValue, 1)} km/h`;
    tr.append(speed);

    const coordinates = document.createElement("td");
    coordinates.textContent = report.latitude === null || report.longitude === null
      ? "Sin coordenadas"
      : `${Number(report.latitude).toFixed(5)}, ${Number(report.longitude).toFixed(5)}`;
    tr.append(coordinates);

    const ignition = document.createElement("td");
    ignition.textContent = report.ignition === null || report.ignition === undefined
      ? "Sin dato"
      : (Number(report.ignition) === 1 ? "Sí" : "No");
    tr.append(ignition);
    body.append(tr);
  }
  if (!reports.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "empty";
    cell.textContent = "No hay telemetría disponible para mostrar.";
    row.append(cell);
    body.append(row);
  }
}

function renderState(state) {
  const components = state.componentes || {};
  const count = Object.values(components).filter((status) => status === "ok").length;
  $("system-status").textContent = `${count} de 5 servicios disponibles`;
  $("system-status").className = `status ${count === 5 ? "" : "warning"}`;
  for (const node of document.querySelectorAll("[data-service]")) {
    const okay = components[node.dataset.service] === "ok";
    const label = node.querySelector(".node-status");
    label.textContent = okay ? "En funcionamiento" : "Sin respuesta reciente";
    label.className = `node-status ${okay ? "ok" : "bad"}`;
  }
  if (state.etl) {
    const etl = state.etl;
    $("last-sync").textContent = `Última carga: ${new Date(etl.ultima_carga).toLocaleString("es-CL")}`;
    $("source-count").textContent = `${format(etl.leidos)} filas leídas · ${etl.rechazados} descartadas`;
    $("etl-summary").textContent = `Origen: ${etl.origen}. Se normalizan ciudades y números, se validan rangos y se descartan IDs duplicados. ${etl.validos} filas válidas de ${etl.leidos}. Versión: ${etl.dataset_version.slice(0, 12)}.`;
    $("rejections").replaceChildren();
    for (const rejected of etl.rechazos) {
      const item = document.createElement("li"); item.textContent = `Fila ${rejected.fila}: ${rejected.motivo}`;
      $("rejections").append(item);
    }
  }
  if (state.modelo) {
    const metrics = state.modelo.metricas;
    $("model-error").textContent = `${format(metrics.mae_horas * 60, 1)} min`;
    $("model-name").textContent = "Regresión lineal · scikit-learn";
    $("model-training").textContent = `${metrics.filas_entrenamiento} viajes para entrenar · ${metrics.filas_evaluacion} para evaluar. Modelo final: ${metrics.filas_modelo_final} viajes.`;
  }
  if (state.modelo_telemetria) {
    const geo = state.modelo_telemetria;
    const metrics = geo.metricas || {};
    $("geo-model-zones").textContent = `${format(metrics.zonas_detectadas || 0)} zonas frecuentes`;
    $("geo-model-detail").textContent = `${geo.algoritmo} · ${format(metrics.reportes_entrenamiento || 0)} posiciones · ${format(metrics.cobertura_pct || 0, 1)} % agrupado. Entrenado ${formatUtc(geo.entrenado_en)}.`;
  }
}

async function refresh() {
  if (refreshing) return;
  refreshing = true; $("refresh").disabled = true;
  try {
    const [state, summary, dataset, telemetrySummary, telemetryDataset, heatmap] = await Promise.all([
      api("/api/estado"),
      api("/api/resumen"),
      api("/api/viajes?limite=500"),
      api("/api/telemetria/resumen"),
      api("/api/telemetria?limite=30"),
      api("/api/telemetria/mapa-calor")
    ]);
    renderState(state);
    renderTelemetry(telemetrySummary, telemetryDataset, state);
    renderHeatmap(heatmap);
    $("trip-count").textContent = format(summary.viajes);
    $("prediction-count").textContent = format(summary.predicciones);
    $("destination-count").textContent = format(summary.destinos);
    trips = dataset.viajes; renderTrips();
    $("notice").hidden = state.status === "ok";
    $("notice").textContent = "Una etapa no ha respondido recientemente o está preparando datos nuevos. Los datos mostrados corresponden a la última carga disponible.";
  } catch (error) {
    $("notice").hidden = false;
    $("notice").textContent = "No se pudo actualizar el sistema. Los datos que ya ves pueden estar desactualizados. " + error.message;
    $("system-status").textContent = "Sin conexión con el sistema";
    $("system-status").className = "status warning";
    for (const label of document.querySelectorAll(".node-status")) {
      label.textContent = "Estado sin verificar"; label.className = "node-status bad";
    }
  } finally { refreshing = false; $("refresh").disabled = false; }
}

function predictionMessage(title, text, value = null, error = false) {
  const result = $("prediction-result"); result.replaceChildren();
  result.className = `prediction-result${error ? " error" : ""}`;
  const label = document.createElement("span"); label.textContent = title; result.append(label);
  if (value !== null) {const number = document.createElement("strong"); number.textContent = `${format(value, 2)} horas`; result.append(number);}
  const paragraph = document.createElement("p"); paragraph.textContent = text; result.append(paragraph);
}

$("prediction-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("predict").disabled = true;
  predictionMessage("PROCESANDO", "Enviando la solicitud al modelo…");
  let pendingPath = null;
  try {
    const input = {distancia_km: Number($("distance").value), carga_kg: Number($("load").value), paradas: Number($("stops").value)};
    const request = await api("/api/predicciones", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(input)});
    pendingPath = request.consulta;
    const deadline = Date.now() + 25000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const result = await api(pendingPath);
      if (result.estado === "completada") {
        predictionMessage("DURACIÓN ESTIMADA", `Resultado guardado en MariaDB. Modelo ${result.dataset_version.slice(0, 8)}.`, result.duracion_estimada);
        return;
      }
    }
    throw new Error("La solicitud sigue pendiente. Puedes consultar su estado en el enlace inferior.");
  } catch (error) {
    predictionMessage("NO SE PUDO COMPLETAR", error.message, null, true);
    if (pendingPath) {const link = document.createElement("a"); link.href = pendingPath; link.textContent = "Consultar solicitud ↗"; link.target = "_blank"; link.rel = "noopener"; $("prediction-result").append(link);}
  } finally { $("predict").disabled = false; }
});
$("refresh").addEventListener("click", refresh);
$("search").addEventListener("input", renderTrips);
$("show-all").addEventListener("click", () => {expanded = !expanded; renderTrips();});
window.addEventListener("hashchange", syncDatasetView);
syncDatasetView();
refresh();
setInterval(refresh, 15000);
