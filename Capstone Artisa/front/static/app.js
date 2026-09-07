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

  const svg = $("heatmap-map");
  svg.replaceChildren();
  if (!data.limites || !(data.celdas || []).length) {
    const empty = svgNode("text", {x: 480, y: 270, "text-anchor": "middle", class: "map-empty"});
    empty.textContent = "No hay coordenadas para construir el mapa.";
    svg.append(empty);
    return;
  }

  const width = 960, height = 540, padding = 48;
  const bounds = data.limites;
  const middleLatitude = (bounds.latitud_min + bounds.latitud_max) / 2;
  const longitudeFactor = Math.max(0.3, Math.cos(middleLatitude * Math.PI / 180));
  const geographicWidth = Math.max((bounds.longitud_max - bounds.longitud_min) * longitudeFactor, 0.01);
  const geographicHeight = Math.max(bounds.latitud_max - bounds.latitud_min, 0.01);
  const scale = Math.min((width - padding * 2) / geographicWidth, (height - padding * 2) / geographicHeight);
  const usedWidth = geographicWidth * scale;
  const usedHeight = geographicHeight * scale;
  const left = (width - usedWidth) / 2;
  const top = (height - usedHeight) / 2;
  const project = (latitude, longitude) => ({
    x: left + (longitude - bounds.longitud_min) * longitudeFactor * scale,
    y: top + (bounds.latitud_max - latitude) * scale
  });

  svg.append(svgNode("rect", {x: left, y: top, width: usedWidth, height: usedHeight, rx: 10, class: "map-background"}));
  for (let index = 0; index <= 4; index += 1) {
    const x = left + usedWidth * index / 4;
    const y = top + usedHeight * index / 4;
    svg.append(svgNode("line", {x1: x, y1: top, x2: x, y2: top + usedHeight, class: "map-grid"}));
    svg.append(svgNode("line", {x1: left, y1: y, x2: left + usedWidth, y2: y, class: "map-grid"}));
  }

  const routes = svgNode("g", {class: "map-routes"});
  for (const route of [...(data.tramos || [])].sort((a, b) => a.intensidad - b.intensidad)) {
    const origin = project(route.origen.latitud, route.origen.longitud);
    const destination = project(route.destino.latitud, route.destino.longitud);
    const line = svgNode("line", {
      x1: origin.x, y1: origin.y, x2: destination.x, y2: destination.y,
      stroke: heatColor(route.intensidad), "stroke-width": 1.5 + route.intensidad * 7,
      "stroke-opacity": 0.35 + route.intensidad * 0.55
    });
    const title = svgNode("title");
    title.textContent = `${route.etiqueta}: ${route.pasos} cruces, ${route.vehiculos} vehículos`;
    line.append(title);
    routes.append(line);
  }
  svg.append(routes);

  const points = svgNode("g", {class: "map-points"});
  for (const cell of [...(data.celdas || [])].sort((a, b) => a.intensidad - b.intensidad)) {
    const point = project(cell.latitud, cell.longitud);
    const radius = 4 + Math.sqrt(cell.intensidad) * 18;
    const circle = svgNode("circle", {
      cx: point.x, cy: point.y, r: radius, fill: heatColor(cell.intensidad),
      "fill-opacity": 0.35 + cell.intensidad * 0.5
    });
    const title = svgNode("title");
    title.textContent = `${cell.latitud.toFixed(3)}, ${cell.longitud.toFixed(3)}: ${cell.reportes} lecturas, ${cell.vehiculos} vehículos, ${cell.paradas} paradas`;
    circle.append(title);
    points.append(circle);
    if (cell.paradas > 0) points.append(svgNode("circle", {
      cx: point.x, cy: point.y, r: radius + 4, class: "stop-ring"
    }));
  }
  svg.append(points);

  const south = svgNode("text", {x: left, y: Math.min(height - 8, top + usedHeight + 24), class: "map-axis"});
  south.textContent = `${bounds.latitud_min.toFixed(2)}° lat`;
  const north = svgNode("text", {x: left + usedWidth, y: Math.min(height - 8, top + usedHeight + 24), "text-anchor": "end", class: "map-axis"});
  north.textContent = `${bounds.latitud_max.toFixed(2)}° lat`;
  svg.append(south, north);
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
