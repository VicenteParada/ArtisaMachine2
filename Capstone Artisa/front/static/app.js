"use strict";

const $ = (id) => document.getElementById(id);
const landing = document.querySelector(".landing");
const appShell = document.getElementById("app");
const loginForm = document.getElementById("login-form");
const format = (value, decimals = 0) => Number(value).toLocaleString("es-CL", {maximumFractionDigits: decimals});
const formatUtc = (value) => value ? new Intl.DateTimeFormat("es-CL", {
  dateStyle: "short", timeStyle: "medium", timeZone: "UTC"
}).format(new Date(value)) : "Sin fecha";
const formatDataDay = (value) => value ? new Intl.DateTimeFormat("es-CL", {
  dateStyle: "long", timeZone: "UTC"
}).format(new Date(value + "T12:00:00Z")) : "Sin fecha";
let trips = [];
let expanded = false;
let refreshing = false;

function showApp() {
  landing.hidden = true;
  appShell.hidden = false;
  window.scrollTo(0, 0);
}

function token() {
  return sessionStorage.getItem("artisa_token");
}

function logout() {
  sessionStorage.removeItem("artisa_token");
  landing.hidden = false;
  appShell.hidden = true;
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = loginForm.querySelector("button");
  const error = $("login-error");
  button.disabled = true;
  error.hidden = true;
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        username: $("login-user").value,
        password: $("login-password").value
      })
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "No se pudo iniciar sesión.");
    sessionStorage.setItem("artisa_token", payload.token);
    showApp();
    refresh();
  } catch (loginError) {
    error.textContent = loginError.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
});

if (token() && window.location.hash) showApp();

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token()}`);
  const response = await fetch(path, {...options, headers, signal: AbortSignal.timeout(20000)});
  const payload = await response.json();
  if (response.status === 401) {
    logout();
    throw new Error("La sesión expiró. Ingresa nuevamente.");
  }
  if (!response.ok) throw new Error(payload.error || "El servicio está temporalmente ocupado.");
  return payload;
}

function syncDatasetView() {
  const simulatedHashes = new Set(["#datos-simulados", "#viajes", "#simulador"]);
  let view = "real";
  if (simulatedHashes.has(window.location.hash)) view = "simulated";
  else if (window.location.hash === "#mapa-calor") view = "heatmap";
  else if (window.location.hash === "#dashboard") view = "dashboard";
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

function routeProperties(route) {
  return route && route.properties ? route.properties : {};
}

function routeLabel(route) {
  const properties = routeProperties(route);
  return properties.label || properties.route_id || "Sin rutas suficientes";
}

function routeUsageSummary(route) {
  const properties = routeProperties(route);
  return route
    ? format(properties.usage_count || 0) + " recorridos · " +
      format(properties.unique_vehicle_count || 0) + " vehículos · " +
      (properties.usage_level || "Sin nivel")
    : "Sin información";
}

const ROUTE_DRAW_ORDER = ["LOW", "MEDIUM", "HIGH"];
const mapLayerVisibility = {routes: true, vehicles: true, stops: true, readings: false};
let activeRouteFilter = "ALL";
let activeMonitorTab = "routes";
let fleetMap = null;
let readingLayer = null;
const routeLayers = {LOW: null, MEDIUM: null, HIGH: null};
let stopLayer = null;
let truckLayer = null;
let pendingMapBounds = null;
let mapHasFitted = false;
let currentMapData = null;
let selectedRouteId = null;
let selectedVehicleId = null;
const truckMarkers = new Map();
const routeMapLayers = new Map();

function setLayerVisibility(layer, visible) {
  if (!fleetMap || !layer) return;
  if (visible && !fleetMap.hasLayer(layer)) fleetMap.addLayer(layer);
  if (!visible && fleetMap.hasLayer(layer)) fleetMap.removeLayer(layer);
}

function updateRouteFilterCounts(routes) {
  const counts = {ALL: routes.length, LOW: 0, MEDIUM: 0, HIGH: 0};
  for (const route of routes) {
    const level = routeProperties(route).usage_level;
    if (Object.hasOwn(counts, level)) counts[level] += 1;
  }
  for (const counter of document.querySelectorAll("[data-route-count]")) {
    counter.textContent = format(counts[counter.dataset.routeCount] || 0);
  }
}

function setSelectionDetail(kind, title, description, metadata) {
  const detail = $("map-selection-detail");
  if (!detail) return;
  const type = document.createElement("span");
  type.textContent = kind;
  const heading = document.createElement("strong");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = description;
  detail.replaceChildren(type, heading, copy);
  if (metadata) {
    const extra = document.createElement("small");
    extra.textContent = metadata;
    detail.append(extra);
  }
}

function refreshSelectionStyles() {
  for (const [routeId, record] of routeMapLayers) {
    const selected = routeId === selectedRouteId;
    record.halo.setStyle({weight: selected ? 13 : 9, opacity: selected ? 1 : 0.9});
    record.line.setStyle({weight: selected ? 8 : 6, opacity: selected ? 1 : 0.92});
  }
  for (const [vehicleId, marker] of truckMarkers) {
    const element = marker.getElement();
    if (element) element.classList.toggle("is-selected", vehicleId === selectedVehicleId);
  }
  for (const button of document.querySelectorAll("[data-route-select]")) {
    button.classList.toggle("is-active", button.dataset.routeSelect === selectedRouteId);
  }
  for (const button of document.querySelectorAll("[data-vehicle-select]")) {
    button.classList.toggle("is-active", button.dataset.vehicleSelect === selectedVehicleId);
  }
}

function routeMatchesFilter(route) {
  return activeRouteFilter === "ALL" || routeProperties(route).usage_level === activeRouteFilter;
}

function renderRouteMonitor(routes) {
  const list = $("route-monitor-list");
  if (!list) return;
  $("monitor-route-count").textContent = format(routes.length);
  const query = $("monitor-search").value.trim().toLocaleLowerCase("es");
  const visible = routes.filter((route) => {
    const properties = routeProperties(route);
    return routeMatchesFilter(route) &&
      `${properties.route_id} ${properties.label}`.toLocaleLowerCase("es").includes(query);
  });
  list.replaceChildren();
  for (const route of visible) {
    const properties = routeProperties(route);
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "route-monitor-row";
    button.dataset.routeSelect = properties.route_id;
    const color = document.createElement("i");
    color.style.backgroundColor = properties.color;
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = properties.route_id;
    const label = document.createElement("small");
    label.textContent = `${format(properties.usage_count)} recorridos · ${format(properties.unique_vehicle_count)} vehículos`;
    copy.append(name, label);
    const level = document.createElement("b");
    level.textContent = properties.usage_level;
    button.append(color, copy, level);
    button.addEventListener("click", () => selectRoute(route));
    item.append(button);
    list.append(item);
  }
  if (!visible.length) {
    const item = document.createElement("li");
    item.className = "monitor-empty";
    item.textContent = "No hay rutas para este filtro.";
    list.append(item);
  }
  refreshSelectionStyles();
}

function vehicleStatus(vehicle) {
  const speed = Number(vehicle.speed_kmh || 0);
  if (speed > 3) return {label: "En movimiento", className: "moving"};
  if (vehicle.ignition !== null && vehicle.ignition !== undefined && Number(vehicle.ignition) === 0) {
    return {label: "Apagado", className: "off"};
  }
  return {label: "Detenido", className: "stopped"};
}

function renderLiveFleet(vehicles) {
  const list = $("vehicle-live-list");
  if (!list) return;
  $("vehicle-live-count").textContent = format(vehicles.length);
  const query = $("monitor-search").value.trim().toLocaleLowerCase("es");
  const ordered = [...vehicles]
    .filter((vehicle) => `${vehicle.vehicle_id} ${vehicle.vehicle_name}`.toLocaleLowerCase("es").includes(query))
    .sort((first, second) => Number(second.speed_kmh || 0) - Number(first.speed_kmh || 0));
  list.replaceChildren();
  for (const vehicle of ordered) {
    const status = vehicleStatus(vehicle);
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "vehicle-live-row " + status.className;
    button.dataset.vehicleSelect = vehicle.vehicle_id;
    const icon = document.createElement("span");
    icon.className = "vehicle-list-icon";
    icon.textContent = "🚚";
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = vehicle.vehicle_name || vehicle.vehicle_id;
    const detail = document.createElement("small");
    const speed = vehicle.speed_kmh === null || vehicle.speed_kmh === undefined
      ? "s/d"
      : format(vehicle.speed_kmh, 1) + " km/h";
    detail.textContent = status.label + " · " + speed + " · " + (vehicle.route_id || "sin ruta agrupada");
    copy.append(name, detail);
    button.append(icon, copy);
    button.addEventListener("click", () => selectVehicle(vehicle));
    item.append(button);
    list.append(item);
  }
  if (!ordered.length) {
    const item = document.createElement("li");
    item.className = "monitor-empty";
    item.textContent = "No hay vehículos para esta búsqueda.";
    list.append(item);
  }
  refreshSelectionStyles();
}

function renderMonitorLists() {
  if (!currentMapData) return;
  renderRouteMonitor(currentMapData.routes);
  renderLiveFleet(currentMapData.vehicles);
}

function setMonitorTab(tab) {
  activeMonitorTab = tab === "vehicles" ? "vehicles" : "routes";
  for (const button of document.querySelectorAll("[data-monitor-tab]")) {
    const selected = button.dataset.monitorTab === activeMonitorTab;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-selected", selected ? "true" : "false");
  }
  for (const list of document.querySelectorAll("[data-monitor-list]")) {
    list.hidden = list.dataset.monitorList !== activeMonitorTab;
  }
  $("monitor-search").value = "";
  $("monitor-search").placeholder = activeMonitorTab === "routes"
    ? "Buscar ruta…"
    : "Buscar vehículo…";
  renderMonitorLists();
}

function applyRouteFilter() {
  if (!fleetMap) return;
  for (const level of ROUTE_DRAW_ORDER) {
    const visible = mapLayerVisibility.routes &&
      (activeRouteFilter === "ALL" || activeRouteFilter === level);
    setLayerVisibility(routeLayers[level], visible);
  }
  for (const button of document.querySelectorAll("[data-route-filter]")) {
    const selected = button.dataset.routeFilter === activeRouteFilter;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    button.disabled = !mapLayerVisibility.routes;
  }
  if (currentMapData) renderRouteMonitor(currentMapData.routes);
}

function applyMapLayerVisibility() {
  if (!fleetMap) return;
  setLayerVisibility(readingLayer, mapLayerVisibility.readings);
  setLayerVisibility(stopLayer, mapLayerVisibility.stops);
  setLayerVisibility(truckLayer, mapLayerVisibility.vehicles);
  applyRouteFilter();
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

  for (const [paneName, zIndex] of [
    ["readingPane", 350], ["routeLowPane", 410], ["routeMediumPane", 420],
    ["routeHighPane", 430], ["stopPane", 600], ["truckPane", 650]
  ]) {
    fleetMap.createPane(paneName).style.zIndex = String(zIndex);
  }

  readingLayer = L.layerGroup();
  for (const level of ROUTE_DRAW_ORDER) routeLayers[level] = L.layerGroup();
  stopLayer = L.layerGroup();
  truckLayer = L.layerGroup();
  applyMapLayerVisibility();
}

function fitPendingMap(force = false) {
  if (!fleetMap) return;
  fleetMap.invalidateSize();
  if ((force || !mapHasFitted) && pendingMapBounds && pendingMapBounds.isValid()) {
    fleetMap.fitBounds(pendingMapBounds, {padding: [28, 28], maxZoom: 11});
    mapHasFitted = true;
  }
}

function truckIcon(vehicle) {
  const status = vehicleStatus(vehicle);
  const heading = Number.isFinite(Number(vehicle.heading)) ? Number(vehicle.heading) : 0;
  const html = "<span class='truck-marker " + status.className +
    "'><span class='truck-heading' style='transform:rotate(" + heading +
    "deg)'>▲</span><span class='truck-glyph'>🚚</span></span>";
  return L.divIcon({
    className: "truck-div-icon",
    html,
    iconSize: [38, 38],
    iconAnchor: [19, 19],
    popupAnchor: [0, -17]
  });
}

function truckPopup(vehicle) {
  const status = vehicleStatus(vehicle);
  const wrapper = document.createElement("div");
  wrapper.className = "truck-popup";
  const title = document.createElement("strong");
  title.textContent = vehicle.vehicle_name || vehicle.vehicle_id || "Vehículo";
  const identifier = document.createElement("span");
  identifier.textContent = vehicle.vehicle_id || "Sin vehicle_id";
  const state = document.createElement("b");
  state.className = status.className;
  state.textContent = status.label;
  const details = document.createElement("p");
  const speed = vehicle.speed_kmh === null || vehicle.speed_kmh === undefined
    ? "Sin velocidad"
    : format(vehicle.speed_kmh, 1) + " km/h";
  details.textContent = speed + " · " + (vehicle.event || "Sin evento");
  const route = document.createElement("small");
  route.textContent = "Ruta agrupada: " + (vehicle.route_id || "sin asignar");
  const date = document.createElement("small");
  date.textContent = "Última posición: " + formatUtc(vehicle.timestamp);
  wrapper.append(title, identifier, state, details, route, date);
  return wrapper;
}

function selectRoute(route, focus = true) {
  const properties = routeProperties(route);
  const record = routeMapLayers.get(properties.route_id);
  if (!record || !fleetMap) return;
  selectedRouteId = properties.route_id;
  selectedVehicleId = null;
  fleetMap.closePopup();
  if (!mapLayerVisibility.routes) {
    mapLayerVisibility.routes = true;
    const toggle = document.querySelector('[data-map-layer="routes"]');
    if (toggle) toggle.checked = true;
  }
  applyMapLayerVisibility();
  refreshSelectionStyles();
  setSelectionDetail(
    "RUTA SELECCIONADA · " + properties.usage_level,
    routeLabel(route),
    `${format(properties.usage_count)} recorridos · ${format(properties.unique_vehicle_count)} vehículos`,
    `${format(properties.observed_points)} posiciones observadas · ${properties.geometry_source}`
  );
  if (focus) {
    fleetMap.fitBounds(record.bounds, {padding: [55, 55], maxZoom: 14});
    record.line.openTooltip();
  }
}

function selectVehicle(vehicle, focus = true) {
  const marker = truckMarkers.get(vehicle.vehicle_id);
  if (!marker || !fleetMap) return;
  selectedVehicleId = vehicle.vehicle_id;
  selectedRouteId = null;
  for (const record of routeMapLayers.values()) record.line.closeTooltip();
  if (!mapLayerVisibility.vehicles) {
    mapLayerVisibility.vehicles = true;
    const toggle = document.querySelector('[data-map-layer="vehicles"]');
    if (toggle) toggle.checked = true;
  }
  applyMapLayerVisibility();
  refreshSelectionStyles();
  const status = vehicleStatus(vehicle);
  const speed = vehicle.speed_kmh === null || vehicle.speed_kmh === undefined
    ? "Velocidad sin dato"
    : format(vehicle.speed_kmh, 1) + " km/h";
  setSelectionDetail(
    "VEHÍCULO SELECCIONADO · " + status.label.toUpperCase(),
    vehicle.vehicle_name || vehicle.vehicle_id,
    `${speed} · ${vehicle.event || "Sin evento"}`,
    `${Number(vehicle.latitude).toFixed(6)}, ${Number(vehicle.longitude).toFixed(6)} · ${formatUtc(vehicle.timestamp)}`
  );
  if (focus) {
    fleetMap.setView(marker.getLatLng(), Math.max(fleetMap.getZoom(), 15));
    marker.openPopup();
  }
}

function resetMapSelection() {
  if (fleetMap) fleetMap.closePopup();
  for (const record of routeMapLayers.values()) record.line.closeTooltip();
  selectedRouteId = null;
  selectedVehicleId = null;
  refreshSelectionStyles();
  setSelectionDetail(
    "SELECCIÓN DEL MAPA",
    "Selecciona una ruta o un vehículo",
    "El mapa centrará el elemento y mostrará sus datos sin modificar la clasificación."
  );
  fitPendingMap(true);
}

function textTooltip(text) {
  const label = document.createElement("span");
  label.textContent = text;
  return label;
}

function renderFleetMap(data) {
  initializeFleetMap();
  if (!fleetMap) return;
  currentMapData = data;
  readingLayer.clearLayers();
  for (const level of ROUTE_DRAW_ORDER) routeLayers[level].clearLayers();
  stopLayer.clearLayers();
  truckLayer.clearLayers();
  truckMarkers.clear();
  routeMapLayers.clear();

  for (const reading of data.readings) {
    const radius = 4 + Math.min(4, Math.sqrt(Number(reading.reading_count || 0)) / 4);
    L.circleMarker([reading.latitude, reading.longitude], {
      pane: "readingPane", radius, color: "#1d4ed8", weight: 1, opacity: 0.8,
      fillColor: "#60a5fa", fillOpacity: 0.32
    }).bindTooltip(textTooltip(
      reading.reading_id + " · " + format(reading.reading_count) +
      " lecturas técnicas · " + format(reading.vehicle_count) + " vehículos"
    )).addTo(readingLayer);
  }

  const routes = [...data.routes].sort((first, second) => {
    const firstLevel = ROUTE_DRAW_ORDER.indexOf(routeProperties(first).usage_level);
    const secondLevel = ROUTE_DRAW_ORDER.indexOf(routeProperties(second).usage_level);
    if (firstLevel !== secondLevel) return firstLevel - secondLevel;
    return Number(routeProperties(first).usage_count || 0) - Number(routeProperties(second).usage_count || 0);
  });

  for (const route of routes) {
    const properties = routeProperties(route);
    const level = properties.usage_level;
    const layer = routeLayers[level];
    if (!layer || !route.geometry || route.geometry.type !== "LineString") continue;
    const pane = level === "HIGH" ? "routeHighPane" : (level === "MEDIUM" ? "routeMediumPane" : "routeLowPane");
    const feature = {type: "Feature", properties, geometry: route.geometry};
    const halo = L.geoJSON(feature, {
      style: {pane, color: "#ffffff", weight: 9, opacity: 0.9, lineCap: "round", lineJoin: "round", interactive: false}
    }).addTo(layer);
    const line = L.geoJSON(feature, {
      style: {pane, color: properties.color, weight: 6, opacity: 0.92, lineCap: "round", lineJoin: "round"}
    }).bindTooltip(textTooltip(
      (properties.usage_label || level) + " · " + (properties.route_id || "Ruta") +
      " · " + format(properties.usage_count || 0) + " recorridos · " +
      format(properties.unique_vehicle_count || 0) + " vehículos"
    )).addTo(layer);
    line.on("click", () => selectRoute(route));
    line.on("mouseover", () => line.setStyle({weight: properties.route_id === selectedRouteId ? 8 : 7, opacity: 1}));
    line.on("mouseout", refreshSelectionStyles);
    routeMapLayers.set(properties.route_id, {route, halo, line, bounds: line.getBounds()});
  }
  updateRouteFilterCounts(routes);

  for (const stop of data.stops) {
    const radius = 7 + Math.min(7, Math.sqrt(Number(stop.vehicle_count || 0)) * 2);
    const duration = stop.duration_minutes === null || stop.duration_minutes === undefined
      ? "duración pendiente de consolidar"
      : format(stop.duration_minutes, 0) + " min";
    L.circleMarker([stop.latitude, stop.longitude], {
      pane: "stopPane", radius, color: "#6f42a6", weight: 3,
      fillColor: "#ffffff", fillOpacity: 0.9
    }).bindTooltip(textTooltip(
      stop.label + " · " + format(stop.stop_event_count || 0) + " detenciones · " +
      format(stop.vehicle_count) + " vehículos · " + duration
    )).addTo(stopLayer);
  }

  for (const vehicle of data.vehicles) {
    const marker = L.marker([vehicle.latitude, vehicle.longitude], {
      pane: "truckPane", icon: truckIcon(vehicle), zIndexOffset: 1000
    }).bindPopup(truckPopup(vehicle));
    marker.on("click", () => selectVehicle(vehicle, false));
    marker.addTo(truckLayer);
    truckMarkers.set(vehicle.vehicle_id, marker);
  }
  renderMonitorLists();
  applyMapLayerVisibility();

  const sourceLabel = data.sourceMode === "TELEMETRY_PROCESSED_SIMULATION"
    ? "Telemetría procesada"
    : (data.sourceMode === "SIMULATION_PROCESSED" ? "Simulación aislada" : "Datos procesados");
  const minutes = Math.max(1, Math.round(Number(data.metadata.refresh_seconds || 300) / 60));
  $("map-last-update").textContent = sourceLabel + " · corte " +
    formatDataDay(data.snapshotDate) + " · actualización cada " + minutes + " min";

  if (data.bounds) {
    pendingMapBounds = L.latLngBounds(
      [data.bounds.latitude_min, data.bounds.longitude_min],
      [data.bounds.latitude_max, data.bounds.longitude_max]
    );
  }
  if (selectedRouteId) {
    const route = data.routes.find((value) => routeProperties(value).route_id === selectedRouteId);
    if (route) selectRoute(route, false); else resetMapSelection();
  } else if (selectedVehicleId) {
    const vehicle = data.vehicles.find((value) => value.vehicle_id === selectedVehicleId);
    if (vehicle) selectVehicle(vehicle, false); else resetMapSelection();
  } else {
    refreshSelectionStyles();
  }
  if ($("heatmap-map").offsetParent !== null) requestAnimationFrame(() => fitPendingMap(false));
}

function renderHeatmap(data) {
  const summary = data.summary;
  $("heatmap-trips").textContent = format(summary.trip_count || 0);
  $("heatmap-vehicles").textContent = format(summary.vehicle_count || 0);
  $("heatmap-stops").textContent = format(summary.stop_count || 0);
  $("heatmap-routes").textContent = format(summary.route_count || 0);
  $("heatmap-zones").textContent =
    `${format(summary.window_report_count || 0)} registros recientes · ` +
    `${format(summary.reading_count || 0)} posiciones únicas · ` +
    `${format(summary.available_report_count || 0)} reportes conservados`;

  const most = data.ranking.mostUsed;
  const least = data.ranking.leastUsed;
  $("most-route").textContent = routeLabel(most);
  $("most-route-usage").textContent = routeUsageSummary(most);
  $("least-route").textContent = routeLabel(least);
  $("least-route-usage").textContent = routeUsageSummary(least);

  const metadata = data.metadata;
  $("heatmap-method").textContent = [
    metadata.classification, metadata.geometry, metadata.road_matching,
    metadata.data_retention
  ].filter(Boolean).join(" ");

  const ranking = $("stop-ranking");
  ranking.replaceChildren();
  for (const stop of [...data.stops]
    .sort((first, second) => Number(second.stop_event_count || 0) - Number(first.stop_event_count || 0))
    .slice(0, 8)) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = stop.label || stop.stop_id;
    const count = document.createElement("strong");
    count.textContent = format(stop.stop_event_count || 0) + " det.";
    item.append(name, count);
    ranking.append(item);
  }
  if (!ranking.children.length) {
    const item = document.createElement("li");
    item.textContent = "No hay paradas en la ventana procesada.";
    ranking.append(item);
  }

  $("geo-model-zones").textContent = "Contrato procesado v" + (data.schemaVersion || "1");
  $("geo-model-detail").textContent = data.description + " " + (metadata.road_matching || "");
  renderFleetMap(data);
}
const DASHBOARD_ZONES = ["Zona Norte", "Zona Centro", "Zona Sur", "Zona Oeste", "Zona Este"];

function routeCenter(route) {
  const coordinates = route && route.geometry && route.geometry.type === "LineString" ? route.geometry.coordinates : [];
  let latitude = 0;
  let longitude = 0;
  let count = 0;
  for (const coordinate of coordinates) {
    const lon = Number(coordinate && coordinate[0]);
    const lat = Number(coordinate && coordinate[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    latitude += lat;
    longitude += lon;
    count += 1;
  }
  return count ? {latitude: latitude / count, longitude: longitude / count} : null;
}

function dashboardBounds(data) {
  const supplied = data.bounds || {};
  const bounds = {
    latitudeMin: Number(supplied.latitude_min),
    latitudeMax: Number(supplied.latitude_max),
    longitudeMin: Number(supplied.longitude_min),
    longitudeMax: Number(supplied.longitude_max)
  };
  if (Object.values(bounds).every(Number.isFinite)) return bounds;
  const points = [];
  for (const route of data.routes) {
    const point = routeCenter(route);
    if (point) points.push(point);
  }
  for (const stop of data.stops) {
    const latitude = Number(stop.latitude);
    const longitude = Number(stop.longitude);
    if (Number.isFinite(latitude) && Number.isFinite(longitude)) points.push({latitude, longitude});
  }
  const latitudes = points.map((point) => point.latitude);
  const longitudes = points.map((point) => point.longitude);
  return {
    latitudeMin: Math.min(...latitudes, -34),
    latitudeMax: Math.max(...latitudes, -33),
    longitudeMin: Math.min(...longitudes, -71),
    longitudeMax: Math.max(...longitudes, -70)
  };
}

function dashboardZone(latitude, longitude, bounds) {
  const latitudeCenter = (bounds.latitudeMin + bounds.latitudeMax) / 2;
  const longitudeCenter = (bounds.longitudeMin + bounds.longitudeMax) / 2;
  const latitudeRadius = Math.max((bounds.latitudeMax - bounds.latitudeMin) / 2, 0.0001);
  const longitudeRadius = Math.max((bounds.longitudeMax - bounds.longitudeMin) / 2, 0.0001);
  const northing = (Number(latitude) - latitudeCenter) / latitudeRadius;
  const easting = (Number(longitude) - longitudeCenter) / longitudeRadius;
  if (Math.abs(northing) <= 0.26 && Math.abs(easting) <= 0.26) return "Zona Centro";
  if (Math.abs(northing) >= Math.abs(easting)) return northing >= 0 ? "Zona Norte" : "Zona Sur";
  return easting < 0 ? "Zona Oeste" : "Zona Este";
}

function zoneAccumulator() {
  return new Map(DASHBOARD_ZONES.map((zone) => [zone, {label: zone, value: 0, count: 0, vehicles: 0}]));
}

function renderDashboardBars(containerId, items, defaultColor) {
  const container = $(containerId);
  if (!container) return;
  container.replaceChildren();
  const maximum = Math.max(1, ...items.map((item) => Number(item.value || 0)));
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "dashboard-bar-row";
    const heading = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = item.label;
    const value = document.createElement("strong");
    value.textContent = format(item.value || 0);
    heading.append(label, value);
    const track = document.createElement("div");
    track.className = "dashboard-bar-track";
    const fill = document.createElement("i");
    const percentage = Number(item.value || 0) > 0 ? Math.max(3, Number(item.value || 0) / maximum * 100) : 0;
    fill.style.width = percentage + "%";
    fill.style.backgroundColor = item.color || defaultColor;
    track.append(fill);
    const detail = document.createElement("small");
    detail.textContent = item.detail || "Sin actividad en este bloque";
    row.append(heading, track, detail);
    container.append(row);
  }
  if (!items.length) {
    const empty = document.createElement("span");
    empty.className = "chart-empty";
    empty.textContent = "No hay datos suficientes para construir este gráfico.";
    container.append(empty);
  }
}

function renderDashboardDonut(containerId, items, centerLabel) {
  const container = $(containerId);
  if (!container) return;
  container.replaceChildren();
  const total = items.reduce((sum, item) => sum + Number(item.value || 0), 0);
  let angle = 0;
  const segments = [];
  for (const item of items) {
    const next = total ? angle + Number(item.value || 0) / total * 360 : angle;
    segments.push(`${item.color} ${angle}deg ${next}deg`);
    angle = next;
  }
  const chart = document.createElement("div");
  chart.className = "dashboard-donut";
  chart.style.background = total ? `conic-gradient(${segments.join(",")})` : "#e6ece8";
  const center = document.createElement("div");
  const number = document.createElement("strong");
  number.textContent = format(total);
  const label = document.createElement("span");
  label.textContent = centerLabel;
  center.append(number, label);
  chart.append(center);
  const legend = document.createElement("ul");
  legend.className = "dashboard-donut-legend";
  for (const item of items) {
    const row = document.createElement("li");
    const swatch = document.createElement("i");
    swatch.style.backgroundColor = item.color;
    const name = document.createElement("span");
    name.textContent = item.label;
    const value = document.createElement("strong");
    const percentage = total ? Math.round(Number(item.value || 0) / total * 100) : 0;
    value.textContent = `${format(item.value || 0)} · ${percentage}%`;
    row.append(swatch, name, value);
    legend.append(row);
  }
  container.append(chart, legend);
}

function renderDashboard(data) {
  const summary = data.summary || {};
  $("dashboard-reports").textContent = format(summary.available_report_count || 0);
  $("dashboard-trips").textContent = format(summary.trip_count || data.trips.length);
  $("dashboard-routes").textContent = format(summary.route_count || data.routes.length);
  $("dashboard-stop-events").textContent = format(summary.stop_event_count || 0);
  $("dashboard-snapshot").textContent = "Corte " + formatDataDay(data.snapshotDate);
  $("dashboard-snapshot").className = "status simulation";

  const bounds = dashboardBounds(data);
  const routeZones = zoneAccumulator();
  for (const route of data.routes) {
    const point = routeCenter(route);
    if (!point) continue;
    const zone = routeZones.get(dashboardZone(point.latitude, point.longitude, bounds));
    const properties = routeProperties(route);
    zone.value += Number(properties.usage_count || 0);
    zone.count += 1;
    zone.vehicles += Number(properties.unique_vehicle_count || 0);
  }
  const routeZoneItems = [...routeZones.values()]
    .map((zone) => ({...zone, detail: `${format(zone.count)} rutas · ${format(zone.vehicles)} participaciones de vehículos`}))
    .sort((first, second) => second.value - first.value);
  renderDashboardBars("dashboard-route-zones", routeZoneItems, "#246c55");

  const stopZones = zoneAccumulator();
  for (const stop of data.stops) {
    const latitude = Number(stop.latitude);
    const longitude = Number(stop.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    const zone = stopZones.get(dashboardZone(latitude, longitude, bounds));
    zone.value += Number(stop.stop_event_count || 0);
    zone.count += 1;
    zone.vehicles += Number(stop.vehicle_count || 0);
  }
  const stopZoneItems = [...stopZones.values()]
    .map((zone) => ({...zone, detail: `${format(zone.count)} zonas de parada · ${format(zone.vehicles)} participaciones de vehículos`}))
    .sort((first, second) => second.value - first.value);
  renderDashboardBars("dashboard-stop-zones", stopZoneItems, "#6f42a6");

  const routeLevels = {HIGH: 0, MEDIUM: 0, LOW: 0};
  for (const route of data.routes) {
    const level = routeProperties(route).usage_level;
    if (Object.hasOwn(routeLevels, level)) routeLevels[level] += 1;
  }
  renderDashboardDonut("dashboard-route-levels", [
    {label: "Más usadas", value: routeLevels.HIGH, color: "#14532d"},
    {label: "Uso medio", value: routeLevels.MEDIUM, color: "#f97316"},
    {label: "Poco usadas", value: routeLevels.LOW, color: "#dc2626"}
  ], "rutas");

  const fleet = {moving: 0, stopped: 0, off: 0};
  for (const vehicle of data.vehicles) fleet[vehicleStatus(vehicle).className] += 1;
  renderDashboardDonut("dashboard-fleet-status", [
    {label: "En movimiento", value: fleet.moving, color: "#58a566"},
    {label: "Detenidos", value: fleet.stopped, color: "#e88836"},
    {label: "Apagados", value: fleet.off, color: "#708083"}
  ], "vehículos");

  const topRoutes = [...data.routes]
    .sort((first, second) => Number(routeProperties(second).usage_count || 0) - Number(routeProperties(first).usage_count || 0))
    .slice(0, 8)
    .map((route) => {
      const properties = routeProperties(route);
      return {
        label: properties.label || properties.route_id,
        value: Number(properties.usage_count || 0),
        color: properties.color,
        detail: `${properties.route_id} · ${format(properties.unique_vehicle_count || 0)} vehículos · ${properties.usage_label || properties.usage_level}`
      };
    });
  renderDashboardBars("dashboard-top-routes", topRoutes, "#246c55");

  const mostRouteZone = routeZoneItems[0];
  $("dashboard-most-route-zone").textContent = mostRouteZone ? mostRouteZone.label : "Sin datos";
  $("dashboard-most-route-zone-detail").textContent = mostRouteZone
    ? `${format(mostRouteZone.value)} recorridos en ${format(mostRouteZone.count)} rutas`
    : "No hay rutas suficientes en el bloque.";
  const mostStopZone = stopZoneItems[0];
  $("dashboard-most-stop-zone").textContent = mostStopZone ? mostStopZone.label : "Sin datos";
  $("dashboard-most-stop-zone-detail").textContent = mostStopZone
    ? `${format(mostStopZone.value)} detenciones en ${format(mostStopZone.count)} zonas detectadas`
    : "No hay paradas suficientes en el bloque.";
  $("dashboard-method").textContent =
    "Las coordenadas se dividen en cinco zonas relativas al centro del área observada: norte, centro, sur, oeste y este. " +
    "Cada ruta conserva una sola clasificación y se cuenta en la zona de su centro geométrico. Actualización automática con el mismo bloque del mapa.";
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

}

async function refresh() {
  if (!token()) return;
  if (refreshing) return;
  refreshing = true; $("refresh").disabled = true;
  try {
    const [state, summary, dataset, telemetrySummary, telemetryDataset, mapPayload] = await Promise.all([
      api("/api/estado"),
      api("/api/resumen"),
      api("/api/viajes?limite=500"),
      api("/api/telemetria/resumen"),
      api("/api/telemetria?limite=30"),
      api("/api/mapa")
    ]);
    if (!window.ArtisaMapData) throw new Error("No se cargó el adaptador del mapa.");
    const mapData = window.ArtisaMapData.adaptMapPayload(mapPayload);
    renderState(state);
    renderTelemetry(telemetrySummary, telemetryDataset, state);
    renderHeatmap(mapData);
    renderDashboard(mapData);
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
for (const button of document.querySelectorAll("[data-route-filter]")) {
  button.addEventListener("click", () => {
    activeRouteFilter = button.dataset.routeFilter || "ALL";
    applyRouteFilter();
  });
}
for (const input of document.querySelectorAll("[data-map-layer]")) {
  input.addEventListener("change", () => {
    const layerName = input.dataset.mapLayer;
    if (Object.hasOwn(mapLayerVisibility, layerName)) {
      mapLayerVisibility[layerName] = input.checked;
      applyMapLayerVisibility();
    }
  });
}
for (const button of document.querySelectorAll("[data-monitor-tab]")) {
  button.addEventListener("click", () => setMonitorTab(button.dataset.monitorTab));
}
$("monitor-search").addEventListener("input", () => {
  if (activeMonitorTab === "routes") renderRouteMonitor(currentMapData ? currentMapData.routes : []);
  else renderLiveFleet(currentMapData ? currentMapData.vehicles : []);
});
$("reset-map-view").addEventListener("click", resetMapSelection);
window.addEventListener("hashchange", syncDatasetView);
syncDatasetView();
if (token()) refresh();
setInterval(refresh, 15000);
