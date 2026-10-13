"use strict";

const $ = (id) => document.getElementById(id);
const landing = document.querySelector(".landing");
const appShell = document.getElementById("app");
const loginForm = document.getElementById("login-form");
const format = (value, decimals = 0) => Number(value).toLocaleString("es-CL", {maximumFractionDigits: decimals});
const formatUtc = (value) => value ? new Intl.DateTimeFormat("es-CL", {
  dateStyle: "short", timeStyle: "medium", timeZone: "UTC"
}).format(new Date(value)) : "Sin fecha";
const HISTORY_ZONE = "America/Santiago";  // días de la empresa: 00:00 a 00:00 hora de Chile
const formatLocal = (value, zone = HISTORY_ZONE) => value ? new Intl.DateTimeFormat("es-CL", {
  dateStyle: "short", timeStyle: "medium", timeZone: zone
}).format(new Date(value)) : "Sin fecha";
const formatDataDay = (value) => value ? new Intl.DateTimeFormat("es-CL", {
  dateStyle: "long", timeZone: "UTC"
}).format(new Date(value + "T12:00:00Z")) : "Sin fecha";
let refreshing = false;

function showApp() {
  landing.hidden = true;
  appShell.hidden = false;
  window.scrollTo(0, 0);
}

const TOKEN_KEY = "artisa_token";
const EXPIRED_MESSAGE = "Tu sesión expiró. Ingresa nuevamente.";
let activeToken = null;
let sessionVersion = 0;
let expiryTimer;
const pendingRequests = new Set();

// Esta comprobación es solo para la interfaz; Nginx/Auth verifican la firma.
function tokenExpiration(value) {
  try {
    if (typeof value !== "string" || value.length > 4096) return 0;
    const parts = value.split(".");
    if (parts.length !== 3 || parts.some((part) => !/^[A-Za-z0-9_-]+$/.test(part))) return 0;
    const decode = (part) => {
      const base64 = part.replace(/-/g, "+").replace(/_/g, "/");
      return atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "="));
    };
    const header = JSON.parse(decode(parts[0]));
    const payload = JSON.parse(decode(parts[1]));
    if (header?.alg !== "HS256" || header?.typ !== "JWT" || decode(parts[2]).length !== 32) return 0;
    return Number.isSafeInteger(payload?.exp) && payload.exp > Date.now() / 1000
      ? payload.exp * 1000 : 0;
  } catch { return 0; }
}

function token() {
  if (activeToken && !tokenExpiration(activeToken)) logout(EXPIRED_MESSAGE);
  return activeToken;
}

function clearSessionWork() {
  clearTimeout(expiryTimer);
  sessionVersion++;
  for (const controller of pendingRequests) controller.abort();
  pendingRequests.clear();
  refreshing = false;
  $("refresh").disabled = false;
  journeyRequest++;
}

function clearPassword() {
  $("login-password").value = "";
  $("login-password").type = "password";
  $("toggle-password").textContent = "Mostrar";
  $("toggle-password").setAttribute("aria-pressed", "false");
}

function logout(message = "") {
  clearSessionWork();
  if (typeof historyState !== "undefined") Object.assign(historyState, {day: null, month: null, detail: null, loadedOnce: false});
  activeMapMode = "historical";
  userChoseMapMode = false;
  for (const wrapper of document.querySelectorAll(".explorer-frame-wrap")) {
    wrapper.hidden = true;
    const frame = wrapper.querySelector("iframe");
    if (frame) frame.removeAttribute("src");
  }
  activeToken = null;
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* Almacenamiento no disponible. */ }
  clearPassword();
  $("login-user").value = "";
  appShell.hidden = true;
  landing.hidden = false;
  history.replaceState(null, "", location.pathname + location.search);
  $("login-error").textContent = message;
  $("login-error").hidden = !message;
}

function scheduleExpiration() {
  clearTimeout(expiryTimer);
  const remaining = tokenExpiration(activeToken) - Date.now();
  if (remaining <= 0) return logout(EXPIRED_MESSAGE);
  expiryTimer = setTimeout(scheduleExpiration, Math.min(remaining, 2147483647));
}

function restoreSession() {
  let saved;
  try { saved = localStorage.getItem(TOKEN_KEY); } catch { saved = null; }
  if (!tokenExpiration(saved)) {
    logout(saved || activeToken ? EXPIRED_MESSAGE : $("login-error").textContent);
    return;
  }
  const changed = activeToken !== saved;
  if (changed) clearSessionWork();
  activeToken = saved;
  $("login-error").textContent = "";
  $("login-error").hidden = true;
  if (!location.hash) history.replaceState(null, "", "#datos-reales");
  syncDatasetView();
  if (appShell.hidden) showApp();
  scheduleExpiration();
  if (changed) refresh();
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = loginForm.querySelector(".landing-button");
  const error = $("login-error");
  const usernameInput = $("login-user");
  const passwordInput = $("login-password");
  button.disabled = true;
  error.hidden = true;
  try {
    const username = usernameInput.value.trim();
    const password = passwordInput.value;
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      cache: "no-store",
      body: JSON.stringify({username, password})
    });
    if (response.status === 401) throw new Error("Usuario o contraseña incorrectos");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "No se pudo iniciar sesión.");
    if (!tokenExpiration(payload.token)) throw new Error("El servidor devolvió una sesión no válida.");
    try { localStorage.setItem(TOKEN_KEY, payload.token); }
    catch { throw new Error("Permite el almacenamiento local para mantener tu sesión."); }
    clearPassword();
    history.replaceState(null, "", "#datos-reales");
    restoreSession();
  } catch (loginError) {
    clearPassword();
    error.textContent = loginError.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
});

$("toggle-password").addEventListener("click", () => {
  const passwordInput = $("login-password");
  const showing = passwordInput.type === "text";
  passwordInput.type = showing ? "password" : "text";
  $("toggle-password").textContent = showing ? "Mostrar" : "Ocultar";
  $("toggle-password").setAttribute("aria-pressed", String(!showing));
  passwordInput.focus();
});

$("logout").addEventListener("click", () => logout());

function openExplorer() {
  if (token()) setMapMode(activeMapMode);
}

async function api(path, options = {}) {
  const currentToken = token();
  if (!currentToken) throw new Error(EXPIRED_MESSAGE);
  const version = sessionVersion;
  const controller = new AbortController();
  pendingRequests.add(controller);
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs || 20000);
  try {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${currentToken}`);
    const {timeoutMs, ...fetchOptions} = options;
    let response;
    try {
      response = await fetch(path, {...fetchOptions, headers, signal: controller.signal});
    } catch (error) {
      if (error.name === "AbortError") throw new Error("El servidor tardó demasiado en responder.");
      throw error;
    }
    if (version !== sessionVersion) throw new Error("Petición de una sesión anterior cancelada.");
    // Nginx puede devolver HTML en un 401: comprobarlo antes de leer JSON.
    if (response.status === 401) {
      logout(EXPIRED_MESSAGE);
      throw new Error(EXPIRED_MESSAGE);
    }
    const payload = await response.json();
    if (version !== sessionVersion || !token()) throw new Error(EXPIRED_MESSAGE);
    if (!response.ok) throw new Error(payload.error || "El servicio está temporalmente ocupado.");
    return payload;
  } finally {
    clearTimeout(timeout);
    pendingRequests.delete(controller);
  }
}

function syncDatasetView() {
  let view = "real";
  if (window.location.hash === "#mapa-calor") view = "heatmap";
  else if (window.location.hash === "#dashboard") view = "dashboard";
  else if (window.location.hash === "#prediccion") view = "prediction";
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
  if (view === "heatmap") {
    window.setTimeout(openExplorer, 50);
  }
  if (view === "prediction") {
    window.setTimeout(() => { loadPrediction(); if (predictionState.map) predictionState.map.invalidateSize(); }, 50);
  }
  if (view === "heatmap" && activeMapMode === "classified" && fleetMap) {
    window.setTimeout(fitPendingMap, 0);
  }
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
const ROUTE_COLORS = Object.freeze({HIGH: "#14532d", MEDIUM: "#f97316", LOW: "#dc2626"});
const mapLayerVisibility = {routes: true, vehicles: true};
let activeRouteFilter = "ALL";
let activeMonitorTab = "routes";
let activeMapMode = "historical";
let userChoseMapMode = false;
let fleetMap = null;
const routeLayers = {LOW: null, MEDIUM: null, HIGH: null};
let truckLayer = null;
let journeyLayer = null;
let selectedRouteLayer = null;
let journeyRequest = 0;
let activeJourneyVehicleId = null;
let pendingMapBounds = null;
let mapHasFitted = false;
let currentMapData = null;
let selectedRouteId = null;
let selectedVehicleId = null;
const truckMarkers = new Map();
const routeMapLayers = new Map();
let openPopupVehicleId = null;   // ficha de camión abierta (se reabre tras actualizar el mapa)
let faenaLayer = null;
let densityLayer = null;
let densityDay = null;

function setMapMode(mode, remember = false, pending = !currentMapData || machinePending(currentMapData)) {
  activeMapMode = mode === "classified" && !pending ? "classified" : "historical";
  if (remember) userChoseMapMode = true;
  const historical = activeMapMode === "historical";
  const explorer = $("explorer-routes");
  explorer.closest(".map-card").classList.toggle("showing-history", historical);
  // El explorador histórico no usa las listas de rutas y vehículos: ocupa todo el ancho.
  explorer.closest(".heatmap-layout").classList.toggle("is-historical", historical);
  document.querySelector(".heatmap-method").hidden = historical;
  // Los filtros y capas solo aplican a las rutas clasificadas.
  for (const panel of document.querySelectorAll(".route-filter-panel, .map-layer-panel")) panel.hidden = historical;
  explorer.hidden = !historical;
  $("heatmap-map").hidden = historical;
  $("classified-map-legend").hidden = historical;
  for (const button of document.querySelectorAll("[data-map-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mapMode === activeMapMode));
    if (button.dataset.mapMode === "classified") button.disabled = pending;
  }
  $("map-view-description").textContent = historical
    ? "Reportes GPS guardados, día por día o en un rango de fechas. No cambia la clasificación de rutas."
    : "Rutas completas clasificadas por uso y última posición de cada camión.";
  for (const control of document.querySelectorAll("[data-route-filter], [data-map-layer], [data-monitor-tab], #monitor-search, #reset-map-view")) {
    control.disabled = pending || historical;
  }
  if (historical) {
    const frame = explorer.querySelector("iframe[data-explorer-frame]");
    if (frame && !frame.hasAttribute("src") && token()) frame.src = frame.dataset.explorerFrame;
    frame?.contentWindow?.postMessage("artisa:show-map", location.origin);
  } else if (fleetMap) {
    requestAnimationFrame(() => fitPendingMap(true));
  }
}

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

function drawSelectedRoute(record) {
  // La ruta seleccionada se dibuja encima de todas, con contorno, y con sus dos extremos.
  if (!selectedRouteLayer) return;
  selectedRouteLayer.clearLayers();
  if (!record) return;
  const properties = routeProperties(record.route);
  const coordinates = record.route.geometry.coordinates || [];
  const latlngs = coordinates.map((point) => [point[1], point[0]]);
  if (latlngs.length < 2) return;
  L.polyline(latlngs, {pane: "routeSelectedPane", color: "#0b1f1a", weight: 13, opacity: 0.9,
    lineCap: "round", lineJoin: "round", interactive: false}).addTo(selectedRouteLayer);
  L.polyline(latlngs, {pane: "routeSelectedPane", color: ROUTE_COLORS[properties.usage_level] || "#246c55",
    weight: 7, opacity: 1, lineCap: "round", lineJoin: "round", interactive: false}).addTo(selectedRouteLayer);
  const [nameA, nameB] = String(properties.label || "").split("↔").map((part) => part.trim());
  const ends = [[latlngs[0], nameA || "Extremo A"], [latlngs[latlngs.length - 1], nameB || nameA || "Extremo B"]];
  for (const [latlng, name] of ends) {
    L.circleMarker(latlng, {pane: "routeSelectedPane", radius: 8, color: "#0b1f1a", weight: 3,
      fillColor: "#ffffff", fillOpacity: 1, interactive: false})
      .bindTooltip(name, {permanent: true, direction: "top", offset: [0, -8], className: "route-endpoint"})
      .addTo(selectedRouteLayer);
  }
}

function refreshSelectionStyles() {
  const focused = Boolean(selectedRouteId);
  for (const [routeId, record] of routeMapLayers) {
    const selected = routeId === selectedRouteId;
    // Con una ruta seleccionada, el resto queda tenue para que se vea su recorrido completo.
    record.halo.setStyle({
      weight: record.haloWeight,
      opacity: focused ? 0 : record.haloOpacity
    });
    record.line.setStyle({
      weight: focused && !selected ? Math.max(2, record.baseWeight - 2) : record.baseWeight,
      opacity: focused ? (selected ? 0 : 0.12) : record.baseOpacity
    });
  }
  drawSelectedRoute(focused ? routeMapLayers.get(selectedRouteId) : null);
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
  for (const route of visible.slice(0, 150)) {
    const properties = routeProperties(route);
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "route-monitor-row";
    button.dataset.routeSelect = properties.route_id;
    const color = document.createElement("i");
    color.style.backgroundColor = ROUTE_COLORS[properties.usage_level] || "#718785";
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = properties.label || properties.route_id;
    const label = document.createElement("small");
    label.textContent = `${properties.route_id} · ${format(properties.usage_count)} recorridos · ${format(properties.unique_vehicle_count)} vehículos`;
    copy.append(name, label);
    const level = document.createElement("b");
    level.textContent = properties.usage_level;
    button.append(color, copy, level);
    button.addEventListener("click", () => selectRoute(route));
    item.append(button);
    list.append(item);
  }
  if (visible.length > 150) {
    const item = document.createElement("li");
    item.className = "monitor-empty";
    item.textContent = "Mostrando 150 de " + format(visible.length) +
      " rutas. Usa la búsqueda para encontrar otra; todas permanecen en el mapa.";
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

function vehicleDriver(vehicle) {
  const structured = [vehicle.driver_name, vehicle.driver_last_name]
    .map((part) => String(part || "").trim()).filter(Boolean).join(" ");
  if (structured) return structured;
  return "No informado";
}

function vehiclePlate(vehicle) {
  const explicit = String(vehicle.patente || vehicle.plate || vehicle.license_plate || "").trim();
  if (explicit) return explicit;
  return null;
}

function vehicleStatus(vehicle) {
  const timestamp = vehicle.timestamp ? new Date(vehicle.timestamp).getTime() : NaN;
  if (!Number.isFinite(timestamp)) return {label: "Estado no disponible", className: "off"};
  const stale = Number.isFinite(timestamp) && Date.now() - timestamp > 30 * 60 * 1000;
  if (stale) return {label: "Última posición histórica", className: "off"};
  const speed = Number(vehicle.speed_kmh || 0);
  if (speed > 3) return {label: "En movimiento", className: "moving"};
  if (vehicle.ignition !== null && vehicle.ignition !== undefined && Number(vehicle.ignition) === 0) {
    return {label: "Apagado", className: "off"};
  }
  return {label: "Con señal reciente", className: "moving"};
}

const TRUCK_MARKUP = '<img class="truck-glyph" src="/artisa-truck-3d.png?v=20261012" alt="" aria-hidden="true">';
const TRUCK_LIST_MARKUP = '<img class="truck-glyph" src="/artisa-truck-side-3d.png?v=20261012" alt="" aria-hidden="true">';

function renderLiveFleet(vehicles) {
  const list = $("vehicle-live-list");
  if (!list) return;
  $("vehicle-live-count").textContent = format(vehicles.length);
  const query = $("monitor-search").value.trim().toLocaleLowerCase("es");
  const ordered = [...vehicles]
    .filter((vehicle) => `${vehicle.vehicle_id} ${vehicle.vehicle_name} ${vehicleDriver(vehicle)}`.toLocaleLowerCase("es").includes(query))
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
    icon.innerHTML = TRUCK_LIST_MARKUP;
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = vehicle.vehicle_name || vehicle.vehicle_id;
    const detail = document.createElement("small");
    const speed = vehicle.speed_kmh === null || vehicle.speed_kmh === undefined
      ? "s/d"
      : format(vehicle.speed_kmh, 1) + " km/h";
    detail.textContent = status.label + " · " + speed + " · " + vehicleDriver(vehicle);
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
    const visible = mapLayerVisibility.routes && !activeJourneyVehicleId && !selectedRouteId &&
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
  // Con un vehículo seleccionado se ve solo él y su recorrido; con una ruta seleccionada,
  // solo esa ruta y los camiones cuyo último viaje fue por ella. "Vista general" muestra todo.
  const routeOf = new Map((currentMapData ? currentMapData.vehicles : []).map((v) => [v.vehicle_id, v.route_id]));
  for (const [vehicleId, marker] of truckMarkers) {
    const visible = activeJourneyVehicleId
      ? vehicleId === activeJourneyVehicleId
      : selectedRouteId ? routeOf.get(vehicleId) === selectedRouteId : true;
    if (visible && !truckLayer.hasLayer(marker)) truckLayer.addLayer(marker);
    if (!visible && truckLayer.hasLayer(marker)) truckLayer.removeLayer(marker);
  }
  setLayerVisibility(truckLayer, mapLayerVisibility.vehicles || !!activeJourneyVehicleId);
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
  fleetMap = L.map(container, {preferCanvas: true, zoomControl: true})
    .setView([-33.45, -70.66], 5);
  const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(fleetMap);
  const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {maxNativeZoom: 18, maxZoom: 19,
     attribution: "Tiles &copy; Esri &mdash; Esri, Maxar, Earthstar Geographics, GIS User Community"});
  fleetMap.createPane("satelliteLabelsPane").style.zIndex = "350";
  fleetMap.getPane("satelliteLabelsPane").style.pointerEvents = "none";
  const satelliteLabels = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    {pane: "satelliteLabelsPane", maxNativeZoom: 18, maxZoom: 19,
     attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors"});
  satellite.on("add", () => satelliteLabels.addTo(fleetMap));
  satellite.on("remove", () => satelliteLabels.remove());
  fleetMap.createPane("faenaPane").style.zIndex = "405";
  faenaLayer = L.featureGroup();
  densityLayer = L.layerGroup();
  faenaLayer.on("add", loadFaenas);
  densityLayer.on("add", loadDensity);
  L.control.layers(
    {"Calles y lugares (OpenStreetMap)": streets, "Satélite (Esri)": satellite},
    {"Faenas": faenaLayer, "Densidad GPS (día anterior)": densityLayer},
    {collapsed: false, position: "topright"}
  ).addTo(fleetMap);
  faenaLayer.addTo(fleetMap);
  const pendingNote = document.createElement("div");
  pendingNote.id = "map-pending-note";
  pendingNote.className = "map-pending-note";
  pendingNote.textContent = "Mapa base OpenStreetMap · rutas pendientes de procesamiento Machine";
  pendingNote.hidden = true;
  container.append(pendingNote);

  for (const [paneName, zIndex] of [
    ["routeLowPane", 410], ["routeMediumPane", 420],
    ["routeHighPane", 430], ["routeSelectedPane", 440], ["journeyPane", 625], ["truckPane", 650]
  ]) {
    fleetMap.createPane(paneName).style.zIndex = String(zIndex);
  }

  for (const level of ROUTE_DRAW_ORDER) routeLayers[level] = L.layerGroup();
  truckLayer = L.layerGroup();
  journeyLayer = L.featureGroup().addTo(fleetMap);
  selectedRouteLayer = L.featureGroup().addTo(fleetMap);
  $("journey-hide").addEventListener("click", resetMapSelection);
  applyMapLayerVisibility();
}

function mapOverlayNote(text) {
  let note = $("map-overlay-note");
  if (!note) {
    note = document.createElement("div");
    note.id = "map-overlay-note";
    note.className = "map-pending-note map-overlay-note";
    $("heatmap-map").append(note);
  }
  note.textContent = text || "";
  note.hidden = !text;
}

async function loadFaenas() {
  if (faenaLayer.getLayers().length) return;
  try {
    const payload = await api("/api/faenas", {timeoutMs: 30000});
    for (const faena of payload.faenas || []) {
      if (!faena.geocerca) continue;
      const geometry = typeof faena.geocerca === "string" ? JSON.parse(faena.geocerca) : faena.geocerca;
      const area = faena.area_m2 != null ? format(Math.round(faena.area_m2)) + " m²" : "sin dato";
      const polygon = L.geoJSON(geometry, {
        pane: "faenaPane", style: {color: "#2e7d32", weight: 2, fillOpacity: 0.18}
      }).bindTooltip(textTooltip(faena.nombre), {sticky: true}).addTo(faenaLayer);
      L.marker(polygon.getBounds().getCenter(), {
        icon: L.divIcon({className: "", html: '<div class="faena-pin"></div>', iconSize: [14, 14]})
      }).bindTooltip(textTooltip(faena.nombre + " · área " + area)).addTo(faenaLayer);
    }
  } catch (error) {
    mapOverlayNote("No se pudieron cargar las faenas: " + error.message);
  }
}

function previousChileDay() {
  const today = new Intl.DateTimeFormat("en-CA", {timeZone: HISTORY_ZONE}).format(new Date());
  return new Date(Date.parse(today) - 86400000).toISOString().slice(0, 10);
}

async function loadDensity() {
  const day = previousChileDay();
  if (densityDay === day && densityLayer.getLayers().length) return;
  if (!L.heatLayer) {
    mapOverlayNote("No se pudo cargar la librería del mapa de calor.");
    return;
  }
  densityLayer.clearLayers();
  densityDay = day;
  mapOverlayNote("Cargando densidad GPS del " + day.split("-").reverse().join("-") + "…");
  try {
    const gps = await api(`/api/gps?desde=${day}&hasta=${day}`, {timeoutMs: 120000});
    if (densityDay !== day) return;
    // Celdas de ~100 m (lat/lon x 1e5 -> 1e3), igual que el explorador de telemetría.
    const cells = new Map();
    for (let i = 0; i < gps.lat.length; i++) {
      const key = Math.round(gps.lat[i] / 100) + "," + Math.round(gps.lon[i] / 100);
      cells.set(key, (cells.get(key) || 0) + 1);
    }
    let max = 1;
    for (const value of cells.values()) max = Math.max(max, value);
    const points = [];
    for (const [key, value] of cells) {
      const [lat, lon] = key.split(",").map(Number);
      points.push([lat / 1000, lon / 1000, Math.log1p(value) / Math.log1p(max)]);
    }
    L.heatLayer(points, {radius: 8, blur: 10, minOpacity: 0.3, max: 1, maxZoom: 18}).addTo(densityLayer);
    mapOverlayNote(points.length ? "" : "Sin reportes GPS el " + day + ".");
  } catch (error) {
    densityDay = null;
    mapOverlayNote("No se pudo cargar la densidad GPS: " + error.message);
  }
}

function openTruckPopup(vehicleId) {
  const marker = truckMarkers.get(vehicleId);
  if (marker && fleetMap && truckLayer.hasLayer(marker)) marker.openPopup();
}

function fitPendingMap(force = false) {
  if (!fleetMap) return;
  fleetMap.invalidateSize();
  // Con un camión o una ruta seleccionados se respeta esa vista: volver a la pestaña o la
  // actualización automática no deben alejar el mapa a toda la flota.
  if (activeJourneyVehicleId || selectedRouteId || selectedVehicleId) return;
  if ((force || !mapHasFitted) && pendingMapBounds && pendingMapBounds.isValid()) {
    fleetMap.fitBounds(pendingMapBounds, {padding: [28, 28], maxZoom: 11});
    mapHasFitted = true;
  }
}

function truckIcon(vehicle) {
  const status = vehicleStatus(vehicle);
  const hasHeading = vehicle.heading !== null && vehicle.heading !== undefined &&
    vehicle.heading !== "" && Number.isFinite(Number(vehicle.heading));
  const arrow = hasHeading
    ? "<span class='truck-heading' style='transform:rotate(" + Number(vehicle.heading) + "deg)'>▲</span>"
    : "";
  const html = "<span class='truck-marker " + status.className +
    "'>" + arrow + TRUCK_MARKUP + "</span>";
  return L.divIcon({
    className: "truck-div-icon",
    html,
    iconSize: [54, 46],
    iconAnchor: [27, 23],
    popupAnchor: [0, -23]
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
  const driver = document.createElement("small");
  driver.textContent = "Conductor: " + vehicleDriver(vehicle);
  const route = document.createElement("small");
  const matched = currentMapData && currentMapData.routes.find(
    (item) => routeProperties(item).route_id === vehicle.route_id);
  route.textContent = "Ruta histórica: " + (matched
    ? routeProperties(matched).usage_label + " · " + vehicle.route_id
    : "sin clasificar");
  const date = document.createElement("small");
  date.textContent = "Última posición: " + formatUtc(vehicle.timestamp);
  const plate = document.createElement("small");
  plate.textContent = "Patente: " + (vehiclePlate(vehicle) || "No informada");
  wrapper.append(title, identifier, state, details, plate, driver, route, date);
  return wrapper;
}

function selectRoute(route, focus = true) {
  hideJourney();
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
  if (selectedVehicleId && selectedVehicleId !== vehicle.vehicle_id &&
      (activeJourneyVehicleId || !$("journey-detail").hidden)) hideJourney();
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
    // Al elegir un vehículo se muestra directamente su recorrido del día; el mapa se encuadra
    // una sola vez, cuando el recorrido llega, y deja espacio arriba para su ficha.
    showVehicleJourney(vehicle);
  }
}

function focusTruck(vehicleId) {
  const marker = truckMarkers.get(vehicleId);
  if (!marker || !fleetMap) return;
  fleetMap.setView(marker.getLatLng(), Math.max(fleetMap.getZoom(), 11));
  openTruckPopup(vehicleId);
}

function hideJourney() {
  journeyRequest += 1;
  if (journeyLayer) journeyLayer.clearLayers();
  activeJourneyVehicleId = null;
  applyMapLayerVisibility();
  const detail = $("journey-detail");
  if (detail) detail.hidden = true;
}

async function showVehicleJourney(vehicle) {
  if (!currentMapData || machinePending(currentMapData)) {
    focusTruck(vehicle.vehicle_id);
    $("journey-detail").hidden = false;
    $("journey-note").textContent = "Recorrido disponible después del procesamiento Machine.";
    return;
  }
  if (!journeyLayer) return;
  const request = ++journeyRequest;
  activeJourneyVehicleId = vehicle.vehicle_id;
  applyMapLayerVisibility();
  journeyLayer.clearLayers();
  const panel = $("journey-detail");
  panel.hidden = false;
  $("journey-note").textContent = "Cargando recorrido desde MariaDB…";
  try {
    const journey = await api("/api/vehiculos/" +
      encodeURIComponent(vehicle.vehicle_id) + "/recorrido");
    if (request !== journeyRequest) return;
    const paths = journey.geometry && journey.geometry.coordinates || [];
    for (const path of paths) {
      if (path.length < 2) continue;
      L.polyline(path.map((point) => [point[1], point[0]]), {
        pane: "journeyPane", color: "#1964b5", weight: 7, opacity: 1,
        lineCap: "round", lineJoin: "round"
      }).addTo(journeyLayer);
    }
    const route = currentMapData && currentMapData.routes.find(
      (item) => routeProperties(item).route_id === vehicle.route_id);
    const category = route
      ? routeProperties(route).usage_label + " · " + vehicle.route_id
      : "Sin clasificar";
    const driver = [journey.driver_name, journey.driver_last_name].filter(Boolean).join(" ");
    $("journey-plate").textContent = journey.patente || vehiclePlate(vehicle) || "No informada";
    $("journey-driver").textContent = driver || vehicleDriver(vehicle);
    $("journey-start").textContent = journey.inicio.replace("T", " ");
    $("journey-end").textContent = journey.termino_observado.replace("T", " ");
    $("journey-distance").textContent = format(journey.distancia_km, 1) + " km";
    $("journey-moving").textContent = format(journey.tiempo_movimiento_minutos || 0, 1) + " min estimados";
    $("journey-gaps").textContent = journey.cortes
      ? format(journey.cortes) + (journey.cortes === 1 ? " corte" : " cortes") +
        " · " + format(journey.minutos_sin_senal || 0, 0) + " min sin reportes"
      : "Sin cortes";
    $("journey-route").textContent = category;
    $("journey-note").textContent = journey.nota +
      (journey.completo ? "" : " Hay más registros ese día; se muestran los últimos 20.000.");
    setSelectionDetail(
      "RECORRIDO OBSERVADO · " + journey.fecha, journey.asset_name,
      format(journey.lecturas) + " posiciones · " +
        format(journey.distancia_km, 1) + " km",
      "Última posición: " + journey.termino_observado.replace("T", " ") +
        " · " + category
    );
    // Se deja espacio arriba para que la ficha del camión no quede tapada.
    if (journeyLayer.getLayers().length && journeyLayer.getBounds().isValid()) {
      fleetMap.once("moveend", () => openTruckPopup(vehicle.vehicle_id));
      fleetMap.fitBounds(journeyLayer.getBounds(), {
        paddingTopLeft: [45, 250], paddingBottomRight: [45, 45], maxZoom: 13
      });
      window.setTimeout(() => openTruckPopup(vehicle.vehicle_id), 700);
    } else {
      focusTruck(vehicle.vehicle_id);
    }
  } catch (error) {
    if (request === journeyRequest) {
      activeJourneyVehicleId = null;
      applyMapLayerVisibility();
      $("journey-note").textContent = "No se pudo consultar el recorrido: " + error.message;
      focusTruck(vehicle.vehicle_id);
    }
  }
}

function resetMapSelection() {
  hideJourney();
  if (fleetMap) fleetMap.closePopup();
  for (const record of routeMapLayers.values()) record.line.closeTooltip();
  selectedRouteId = null;
  selectedVehicleId = null;
  applyMapLayerVisibility();
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
  const reopenPopup = openPopupVehicleId;
  for (const level of ROUTE_DRAW_ORDER) routeLayers[level].clearLayers();
  truckLayer.clearLayers();
  truckMarkers.clear();
  routeMapLayers.clear();

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
    const baseWeight = level === "HIGH" ? 6 : (level === "MEDIUM" ? 4.8 : 3.3);
    const baseOpacity = level === "HIGH" ? 0.98 : (level === "MEDIUM" ? 0.92 : 0.82);
    const haloWeight = baseWeight + 1.5;
    const haloOpacity = 0.9;
    const halo = L.geoJSON(feature, {
      pane, interactive: false,
      style: {color: "#ffffff", weight: haloWeight, opacity: haloOpacity,
        lineCap: "round", lineJoin: "round"}
    }).addTo(layer);
    const line = L.geoJSON(feature, {
      pane,
      style: {color: ROUTE_COLORS[level], weight: baseWeight, opacity: baseOpacity,
        lineCap: "round", lineJoin: "round"}
    }).bindTooltip(textTooltip(
      (properties.usage_label || level) + " · " + (properties.route_id || "Ruta") +
      " · " + format(properties.usage_count || 0) + " recorridos · " +
      format(properties.unique_vehicle_count || 0) + " vehículos"
    )).addTo(layer);
    line.on("click", () => selectRoute(route));
    line.on("mouseover", () => line.setStyle({weight: baseWeight + 2, opacity: 1}));
    line.on("mouseout", refreshSelectionStyles);
    routeMapLayers.set(properties.route_id, {
      route, halo, line, bounds: line.getBounds(), baseWeight, baseOpacity,
      haloWeight, haloOpacity
    });
  }
  updateRouteFilterCounts(routes);

  for (const vehicle of data.vehicles) {
    const marker = L.marker([vehicle.latitude, vehicle.longitude], {
      pane: "truckPane", icon: truckIcon(vehicle), zIndexOffset: 1000
    }).bindPopup(truckPopup(vehicle));
    marker.on("click", () => selectVehicle(vehicle, true));
    marker.on("popupopen", () => { openPopupVehicleId = vehicle.vehicle_id; });
    marker.on("popupclose", () => {
      if (openPopupVehicleId === vehicle.vehicle_id) openPopupVehicleId = null;
    });
    marker.addTo(truckLayer);
    truckMarkers.set(vehicle.vehicle_id, marker);
  }
  renderMonitorLists();
  applyMapLayerVisibility();
  if (reopenPopup) openTruckPopup(reopenPopup);

  const minutes = Math.max(1, Math.round(Number(data.metadata.refresh_seconds || 300) / 60));
  $("map-last-update").textContent = "Clasificación al " + formatDataDay(data.snapshotDate) +
    " · se actualiza cada " + minutes + " min";
  const coverage = data.metadata.metricas?.cobertura;
  const processingHistory = Number(coverage?.dias_pendientes || 0) > 0;
  $("heatmap-source-status").textContent = processingHistory
    ? "Rutas disponibles · histórico en procesamiento" : "Rutas clasificadas";
  $("heatmap-intro").textContent =
    "Verde: rutas más usadas · naranja: uso medio · rojo: poco usadas. Cada camión aparece en su última posición recibida." +
    (processingHistory ? ` Clasificación parcial: ${coverage.dias_cerrados} de ${coverage.dias_ventana} días procesados en la ventana.` : "");

  const visibleBounds = L.latLngBounds([]);
  for (const route of data.routes) {
    if (!route.geometry || route.geometry.type !== "LineString") continue;
    for (const point of route.geometry.coordinates) {
      const longitude = Number(point[0]);
      const latitude = Number(point[1]);
      if (Number.isFinite(latitude) && Number.isFinite(longitude)) {
        visibleBounds.extend([latitude, longitude]);
      }
    }
  }
  {
    for (const vehicle of data.vehicles) {
      const latitude = Number(vehicle.latitude);
      const longitude = Number(vehicle.longitude);
      if (Number.isFinite(latitude) && Number.isFinite(longitude)) {
        visibleBounds.extend([latitude, longitude]);
      }
    }
  }
  pendingMapBounds = visibleBounds.isValid() ? visibleBounds : null;
  if (selectedRouteId) {
    const route = data.routes.find((value) => routeProperties(value).route_id === selectedRouteId);
    if (route) selectRoute(route, false); else resetMapSelection();
  } else if (selectedVehicleId) {
    const vehicle = data.vehicles.find((value) => value.vehicle_id === selectedVehicleId);
    if (vehicle) {
      selectVehicle(vehicle, false);
    } else resetMapSelection();
  } else {
    refreshSelectionStyles();
  }
  if ($("heatmap-map").offsetParent !== null) requestAnimationFrame(() => fitPendingMap(false));
}

function machinePending(data) {
  return data.status === "pending" || data.sourceMode === "PENDING_MACHINE" || data.metadata?.status === "pending";
}

function pendingText(ids, message = "Pendiente de procesamiento") {
  for (const id of ids) $(id).textContent = message;
}

function renderHeatmap(data) {
  const pending = machinePending(data);
  if (!userChoseMapMode) activeMapMode = pending ? "historical" : "classified";
  setMapMode(activeMapMode, false, pending);
  if (pending) {
    currentMapData = data;
    journeyRequest++;
    activeJourneyVehicleId = null;
    selectedVehicleId = null;
    selectedRouteId = null;
    if (fleetMap) {
      for (const level of ROUTE_DRAW_ORDER) routeLayers[level].clearLayers();
      truckLayer.clearLayers();
      journeyLayer.clearLayers();
    }
    truckMarkers.clear();
    routeMapLayers.clear();
    pendingMapBounds = null;
    mapHasFitted = false;
    initializeFleetMap();
    const pendingNote = $("map-pending-note");
    if (pendingNote) pendingNote.hidden = false;
    if (fleetMap && $("heatmap-map").offsetParent !== null) {
      requestAnimationFrame(() => fleetMap.invalidateSize());
    }
    pendingText(["heatmap-trips", "heatmap-vehicles", "heatmap-routes",
      "most-route", "least-route", "geo-model-zones", "heatmap-source-status"]);
    pendingText(["heatmap-intro", "heatmap-zones", "map-last-update", "most-route-usage",
      "least-route-usage", "geo-model-detail", "heatmap-method", "map-selection-detail"],
      "Disponible cuando Machine genere resultados");
    pendingText(["monitor-route-count", "vehicle-live-count"], "—");
    for (const counter of document.querySelectorAll("[data-route-count]")) counter.textContent = "—";
    for (const id of ["route-monitor-list", "vehicle-live-list"]) {
      const item = document.createElement("li");
      item.textContent = "Pendiente de procesamiento Machine.";
      $(id).replaceChildren(item);
    }
    $("journey-detail").hidden = true;
    $("journey-note").textContent = "Recorrido disponible después del procesamiento Machine.";
    return;
  }
  const pendingNote = $("map-pending-note");
  if (pendingNote) pendingNote.hidden = true;
  const summary = data.summary;
  $("heatmap-trips").textContent = format(summary.trip_count || 0);
  $("heatmap-vehicles").textContent = format(summary.vehicle_count || 0);
  $("heatmap-routes").textContent = format(summary.route_count || 0);
  $("heatmap-zones").textContent = format(summary.trip_count || 0) + " viajes en " +
    format(summary.route_count || 0) + " rutas · " + format(data.vehicles.length) + " camiones con posición";

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

  const window_ = metadata.window || {};
  $("geo-model-zones").textContent = window_.desde
    ? "Viajes del " + formatDataDay(window_.desde) + " al " + formatDataDay(window_.hasta)
    : "Clasificación vigente";
  $("geo-model-detail").textContent = metadata.road_matching ||
    "Rutas calculadas con la telemetría disponible.";
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
  const points = [];
  for (const route of data.routes) {
    const point = routeCenter(route);
    if (point) points.push(point);
  }
  for (const vehicle of data.vehicles) {
    const latitude = Number(vehicle.latitude);
    const longitude = Number(vehicle.longitude);
    if (Number.isFinite(latitude) && Number.isFinite(longitude)) points.push({latitude, longitude});
  }
  if (!points.length) {
    return {latitudeMin: -34, latitudeMax: -33, longitudeMin: -71, longitudeMax: -70};
  }
  const latitudes = points.map((point) => point.latitude);
  const longitudes = points.map((point) => point.longitude);
  return {
    latitudeMin: Math.min(...latitudes),
    latitudeMax: Math.max(...latitudes),
    longitudeMin: Math.min(...longitudes),
    longitudeMax: Math.max(...longitudes)
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

function renderDashboard(data, metrics = {}) {
  if (metrics.status === "pending" || machinePending(data)) {
    pendingText(["dashboard-reports", "dashboard-trips", "dashboard-routes",
      "dashboard-snapshot", "dashboard-most-route-zone"]);
    pendingText(["dashboard-most-route-zone-detail", "dashboard-method"],
      "Disponible cuando Machine genere resultados");
    for (const id of ["dashboard-route-zones", "dashboard-route-levels",
      "dashboard-fleet-status", "dashboard-top-routes"]) {
      const message = document.createElement("span");
      message.className = "chart-empty";
      message.textContent = "Pendiente de procesamiento Machine.";
      $(id).replaceChildren(message);
    }
    return;
  }
  const summary = data.summary || {};
  const operation = metrics.operacion || {};
  const quality = metrics.calidad || {};
  const routeMetrics = metrics.rutas || {};
  $("dashboard-reports").textContent = format(quality.validos || summary.available_report_count || 0);
  $("dashboard-trips").textContent = format(operation.viajes || data.trips.length);
  $("dashboard-routes").textContent = format(
    Number(routeMetrics.HIGH || 0) + Number(routeMetrics.MEDIUM || 0) + Number(routeMetrics.LOW || 0)
    || data.routes.length
  );
  const coverage = data.metadata.metricas?.cobertura;
  $("dashboard-snapshot").textContent = Number(coverage?.dias_pendientes || 0) > 0
    ? `Histórico en procesamiento · ${coverage.dias_cerrados}/${coverage.dias_ventana} días`
    : "Corte " + formatDataDay(data.snapshotDate);
  $("dashboard-snapshot").className = "status info";

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

  const routeLevels = {
    HIGH: Number(routeMetrics.HIGH || 0),
    MEDIUM: Number(routeMetrics.MEDIUM || 0),
    LOW: Number(routeMetrics.LOW || 0)
  };
  if (routeLevels.HIGH + routeLevels.MEDIUM + routeLevels.LOW === 0) {
    for (const route of data.routes) {
      const level = routeProperties(route).usage_level;
      if (Object.hasOwn(routeLevels, level)) routeLevels[level] += 1;
    }
  }
  renderDashboardDonut("dashboard-route-levels", [
    {label: "Más usadas", value: routeLevels.HIGH, color: "#14532d"},
    {label: "Uso medio", value: routeLevels.MEDIUM, color: "#f97316"},
    {label: "Poco usadas", value: routeLevels.LOW, color: "#dc2626"}
  ], "rutas");

  const fleet = {moving: 0, off: 0};
  for (const vehicle of data.vehicles) fleet[vehicleStatus(vehicle).className] += 1;
  renderDashboardDonut("dashboard-fleet-status", [
    {label: "Con señal", value: fleet.moving, color: "#58a566"},
    {label: "Sin señal o apagados", value: fleet.off, color: "#708083"}
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
  $("dashboard-method").textContent =
    "Cada ruta se cuenta en una zona (norte, centro, sur, oeste o este) según dónde queda su punto central " +
    "respecto del centro de todas las rutas. Se actualiza junto con el mapa.";
}
function formatAge(seconds) {
  const value = Math.max(0, Math.round(seconds));
  if (value < 90) return "hace menos de 2 min";
  if (value < 5400) return `hace ${format(Math.round(value / 60))} min`;
  if (value < 172800) return `hace ${format(Math.round(value / 3600))} h`;
  return `hace ${format(Math.round(value / 86400))} días`;
}

function connectionMessage(connection) {
  const since = connection.desconectado_desde ? ` desde ${formatLocal(connection.desconectado_desde)} (hora de Chile)` : "";
  if (connection.conexion === "reconectando") {
    return `Sin conexión con la API de la empresa${since}. El sistema reintenta solo; cuando vuelva la red recuperará los reportes del período desconectado. Se muestran los últimos datos recibidos.`;
  }
  if (connection.conexion === "error_configuracion") {
    return `La API de la empresa rechazó la consulta${since}. Revisa COMPANY_API_URL y las credenciales en .env. Se muestran los últimos datos recibidos.`;
  }
  if (connection.conexion === "servicio_detenido") {
    return "El servicio de telemetría no informa actividad reciente. Revisa el contenedor artisa-telemetria. Se muestran los últimos datos recibidos.";
  }
  if (connection.conexion === "sincronizando") {
    return "Conectado. Se están recuperando los reportes acumulados desde la última sincronización; los días afectados se cierran cuando termine.";
  }
  if (connection.advertencia) return connection.advertencia;
  return "";
}

function setTelemetryBanner(message, tone = "warning") {
  const banner = $("telemetry-connection");
  banner.hidden = !message;
  banner.textContent = message || "";
  banner.className = "telemetry-connection " + tone;
}

// Bucle propio de Reportes: más frecuente que el refresco general, nunca se
// superpone, conserva los últimos datos si falla y reintenta al volver la red.
const TELEMETRY_REFRESH_MS = 60000;
let telemetryRefreshing = false;
let telemetryLastOk = null;

async function refreshTelemetry() {
  if (!token() || document.hidden || telemetryRefreshing) return;
  const version = sessionVersion;
  telemetryRefreshing = true;
  try {
    const [telemetrySummary, telemetryDataset] = await Promise.all([
      api("/api/telemetria/resumen"),
      api("/api/telemetria?limite=30")
    ]);
    if (version !== sessionVersion || !token()) return;
    renderTelemetry(telemetrySummary, telemetryDataset, {});
    telemetryLastOk = new Date();
    refreshHistory();
  } catch (error) {
    if (version !== sessionVersion || !token()) return;
    if (!$("telemetry-content").hidden) {
      const last = telemetryLastOk
        ? ` Última actualización correcta: ${telemetryLastOk.toLocaleTimeString("es-CL")}.`
        : "";
      setTelemetryBanner(
        (navigator.onLine === false
          ? "Este equipo está sin conexión a la red."
          : "No se pudo contactar al servidor de Artisa.") +
        " Se reintentará automáticamente y se muestran los últimos datos disponibles." + last
      );
    }
  } finally {
    if (version === sessionVersion) telemetryRefreshing = false;
  }
}

function renderTelemetry(summary, dataset, state) {
  const pending = dataset.status === "pending" && dataset.source === "company_api";
  $("telemetry-pending").hidden = !pending;
  $("telemetry-content").hidden = pending;
  const tabNote = document.querySelector('[data-dataset-tab="real"] small');
  if (pending) {
    if (tabNote) tabNote.textContent = "Pendiente de API empresa";
    setTelemetryBanner("");
    $("telemetry-status").textContent = "Pendiente de API empresa";
    $("telemetry-status").className = "status neutral";
    $("telemetry-rows").replaceChildren();
    $("telemetry-count").textContent = "—";
    $("vehicle-count").textContent = "—";
    $("telemetry-range").textContent = "";
    $("telemetry-quality").textContent = "";
    return;
  }
  const reports = dataset.reportes || [];
  const connection = dataset.conexion || summary.conexion || {};
  const recentSeconds = Number(connection.reporte_reciente_segundos || 900);
  $("telemetry-count").textContent = format(summary.reportes || 0);
  $("vehicle-count").textContent = summary.vehiculos === null || summary.vehiculos === undefined
    ? "Sin dato" : format(summary.vehiculos);
  $("vehicle-count-note").textContent = `Camiones con reportes en las últimas ${format(summary.vehiculos_ventana_horas || 24)} h`;
  $("telemetry-range").textContent = connection.ultimo_dato
    ? `Último reporte recibido: ${formatLocal(connection.ultimo_dato)} · último contacto con la API: ${connection.ultimo_exito ? formatLocal(connection.ultimo_exito) : "sin contacto aún"} (hora de Chile)`
    : "La API está configurada, pero todavía no entrega reportes.";
  $("telemetry-quality").textContent =
    `${format(connection.guardados || 0)} guardados · ${format(connection.rechazados || 0)} rechazados de ${format(connection.leidos || 0)} leídos`;

  const states = {
    en_vivo: ["En vivo", "status"],
    sin_reportes_recientes: ["Conectado · sin reportes recientes", "status neutral"],
    sincronizando: ["Recuperando reportes atrasados…", "status info"],
    reconectando: ["Reconectando con la API…", "status warning"],
    error_configuracion: ["Revisar configuración de la API", "status warning"],
    servicio_detenido: ["Servicio de telemetría detenido", "status warning"]
  };
  const [label, className] = states[connection.conexion] || ["Estado desconocido", "status neutral"];
  $("telemetry-status").textContent = label;
  $("telemetry-status").className = className;
  if (tabNote) tabNote.textContent = "API empresa · " + label;
  const infoTone = connection.conexion === "sincronizando" || (connection.advertencia && connection.conexion === "en_vivo");
  setTelemetryBanner(connectionMessage(connection), infoTone ? "info" : "warning");

  const body = $("telemetry-rows");
  body.replaceChildren();
  for (const report of reports) {
    const tr = document.createElement("tr");
    const vehicle = document.createElement("td");
    vehicle.textContent = report.asset_name || "Vehículo sin nombre";
    const assetId = document.createElement("small");
    const driver = [report.driver_name, report.driver_last_name].filter(hasValue).join(" ");
    assetId.textContent = `Patente ${report.asset_id || "—"} · ${driver ? "Conductor: " + driver : "Conductor no informado"}`;
    vehicle.append(assetId);
    tr.append(vehicle);

    const date = document.createElement("td");
    date.textContent = formatLocal(report.report_date);
    const age = document.createElement("small");
    const ageSeconds = Number(report.edad_segundos);
    if (Number.isFinite(ageSeconds)) {
      age.textContent = ageSeconds <= recentSeconds
        ? "Posición reciente · " + formatAge(ageSeconds)
        : "Último dato conocido · " + formatAge(ageSeconds);
      age.className = ageSeconds <= recentSeconds ? "age-recent" : "age-old";
      date.append(age);
    }
    tr.append(date);

    const event = document.createElement("td");
    event.textContent = report.event_name || "Sin evento";
    tr.append(event);

    const coordinates = document.createElement("td");
    coordinates.textContent = report.latitude === null || report.longitude === null
      ? "Sin coordenadas"
      : `${Number(report.latitude).toFixed(5)}, ${Number(report.longitude).toFixed(5)}`;
    tr.append(coordinates);

    const ignition = document.createElement("td");
    ignition.className = "col-ignition";
    ignition.textContent = hasValue(report.ignition) ? (Number(report.ignition) === 1 ? "Sí" : "No") : "";
    tr.append(ignition);
    body.append(tr);
  }
  // Si la fuente no informa encendido, la columna se oculta en vez de mostrar "Sin dato".
  body.closest("table").classList.toggle("no-ignition", !reports.some((report) => hasValue(report.ignition)));
  if (!reports.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "empty";
    cell.textContent = "No hay telemetría disponible para mostrar.";
    row.append(cell);
    body.append(row);
  }
}

function updateOverviewTexts(state) {
  const components = state.componentes || {};
  const company = components.telemetria_api === "ok";
  const machine = components.machine === "ok";
  $("overview-summary").textContent = [
    company ? "Telemetría en vivo desde la API de la empresa" : "Telemetría pendiente de la API de la empresa",
    machine ? "rutas clasificadas por Machine" : "Machine preparando la primera clasificación de rutas",
    "cierre diario a las 00:00 (hora de Chile)"
  ].join(" · ") + ".";
  $("origin-source").textContent = state.etl_historico === false ? "API de la empresa" : "API de la empresa + histórico MongoDB";
}

function renderState(state) {
  const components = state.componentes || {};
  // Las 5 etapas del diagrama. Una etapa "pending" (p. ej. Machine sin su primera
  // clasificación) no está operativa, pero tampoco es una falla.
  const services = ["etl", "mariadb", "machine", "endpoints", "front"];
  const nodes = [...document.querySelectorAll("[data-service]")];
  const count = services.filter((name) => components[name] === "ok").length;
  const failing = services.some((name) => !["ok", "pending"].includes(components[name]));
  $("system-status").textContent = format(count) + " de " + format(services.length) + " servicios operativos";
  $("system-status").className = "status " + (failing ? "warning" : count === services.length ? "" : "neutral");
  updateOverviewTexts(state);
  for (const node of nodes) {
    const okay = components[node.dataset.service] === "ok";
    const label = node.querySelector(".node-status");
    const pending = components[node.dataset.service] === "pending";
    const pendingText = node.dataset.service === "machine" ? "Esperando la primera clasificación" : "Pendiente de configuración";
    label.textContent = okay ? "En funcionamiento" : pending ? pendingText : "Sin respuesta reciente";
    label.className = "node-status " + (okay ? "ok" : pending ? "neutral" : "bad");
  }
  const etl = state.etl;
  const company = state.telemetria_api;
  if (company && company.ultimo_exito) {
    $("last-sync").textContent = "Último dato de la API: " + formatLocal(company.ultimo_exito) + " (hora de Chile)";
  } else if (etl) {
    $("last-sync").textContent = etl.ultima_carga
      ? "Última carga: " + formatUtc(etl.ultima_carga)
      : "ETL aún sin primera carga";
  }
}

async function refresh() {
  if (!token()) return;
  if (refreshing) return;
  const version = sessionVersion;
  refreshing = true; $("refresh").disabled = true;
  // Cada sección se actualiza por separado: si una consulta falla o tarda, las demás
  // siguen funcionando y esa sección conserva lo último que mostró.
  const [stateResult, summaryResult, datasetResult, mapResult, dashboardResult] = await Promise.allSettled([
    api("/api/estado"),
    api("/api/telemetria/resumen"),
    api("/api/telemetria?limite=30"),
    api("/api/mapa", {timeoutMs: 90000}),
    api("/api/dashboard", {timeoutMs: 60000})
  ]);
  try {
    if (version !== sessionVersion || !token()) return;
    const problems = [];
    const state = stateResult.status === "fulfilled" ? stateResult.value : null;
    if (state) renderState(state); else problems.push("estado del sistema");
    if (summaryResult.status === "fulfilled" && datasetResult.status === "fulfilled") {
      renderTelemetry(summaryResult.value, datasetResult.value, state || {});
      refreshHistory();
    } else problems.push("reportes");
    if (mapResult.status === "fulfilled" && window.ArtisaMapData) {
      const mapData = window.ArtisaMapData.adaptMapPayload(mapResult.value);
      renderHeatmap(mapData);
      if (dashboardResult.status === "fulfilled") renderDashboard(mapData, dashboardResult.value);
      else problems.push("dashboard");
    } else problems.push("mapa y dashboard");

    if (!state) {
      $("system-status").textContent = "Sin conexión con el sistema";
      $("system-status").className = "status warning";
      for (const label of document.querySelectorAll(".node-status")) {
        label.textContent = "Estado sin verificar"; label.className = "node-status bad";
      }
    }
    if (problems.length) {
      const reason = [stateResult, summaryResult, datasetResult, mapResult, dashboardResult]
        .find((result) => result.status === "rejected")?.reason?.message || "";
      $("notice").hidden = false;
      $("notice").textContent = `No se pudo actualizar: ${problems.join(", ")}. Se muestran los últimos datos disponibles y se reintentará automáticamente. ${reason}`;
    } else {
      $("notice").hidden = state.status === "ok";
      $("notice").textContent = "Una etapa no ha respondido recientemente o está preparando datos nuevos. Los datos mostrados corresponden a la última carga disponible.";
    }
  } finally {
    if (version === sessionVersion) { refreshing = false; $("refresh").disabled = false; }
  }
}

// --- Historial diario (00:00 a 00:00 hora de Chile) ---
var historyState = {day: null, month: null, detail: null, loadedOnce: false};
const isoDayIn = (date, zone = HISTORY_ZONE) =>
  new Intl.DateTimeFormat("en-CA", {timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit"}).format(date);
const localToday = () => isoDayIn(new Date());
const shiftDay = (iso, days) => {
  const value = new Date(iso + "T12:00:00Z");
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
};
const formatLocalTime = (value, zone = HISTORY_ZONE) => value ? new Intl.DateTimeFormat("es-CL", {
  timeStyle: "medium", timeZone: zone
}).format(new Date(value)) : "—";
const HISTORY_STATES = {
  en_curso: ["Hoy · en curso hasta las 00:00", "status info"],
  esperando_datos: ["Cerrando · esperando datos de la API", "status warning"],
  cerrado: ["Día cerrado", "status"]
};

const hasValue = (value) => value !== null && value !== undefined && value !== "";

function formatMinutes(value) {
  const minutes = Math.round(Number(value || 0));
  if (!minutes) return "—";
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours} h ${String(minutes % 60).padStart(2, "0")} min` : `${minutes} min`;
}

function emptyRow(body, columns, message) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = columns; cell.className = "empty"; cell.textContent = message;
  row.append(cell); body.append(row);
}

function cell(text, small) {
  const td = document.createElement("td");
  td.textContent = text;
  if (small) { const note = document.createElement("small"); note.textContent = small; td.append(note); }
  return td;
}

async function loadHistoryDay(day, quiet = false) {
  if (!day) return;
  const today = localToday();
  if (day > today) day = today;
  historyState.day = day;
  $("history-date").value = day;
  $("history-date").max = today;
  $("history-next").disabled = day >= today;
  if (!quiet) { $("history-status").textContent = "Consultando…"; $("history-status").className = "status neutral"; }
  try {
    const data = await api(`/api/reportes/dia?fecha=${day}`);
    if (historyState.day !== day) return;
    renderHistoryDay(data);
    const month = day.slice(0, 7);
    if (historyState.month !== month || quiet) loadHistoryMonth(month);
  } catch (error) {
    if (historyState.day !== day) return;
    $("history-status").textContent = "No se pudo consultar";
    $("history-status").className = "status warning";
    $("history-note").textContent = error.message + " Se mantienen los últimos datos mostrados.";
  }
}

function renderHistoryDay(data) {
  const zone = data.zona || HISTORY_ZONE;
  const state = data.estado;
  const [label, className] = state ? (HISTORY_STATES[state.estado] || [state.estado, "status neutral"])
    : ["Sin datos para este día", "status neutral"];
  $("history-status").textContent = label;
  $("history-status").className = className;
  $("history-trucks").textContent = state ? format(state.camiones) : "—";
  $("history-reports").textContent = state ? format(state.reportes) : "—";
  $("history-discarded").textContent = state && state.descartados
    ? `${format(state.descartados)} descartados por coordenadas inválidas` : "Reportes GPS válidos";
  $("history-km").textContent = state ? format(state.km_observados, 1) : "—";
  $("history-calculated").textContent = state ? formatLocalTime(state.calculado_en, zone) : "—";
  $("history-calculated-note").textContent = state && state.recalculos
    ? `Recalculado ${format(state.recalculos)} ${state.recalculos === 1 ? "vez" : "veces"} por reportes atrasados`
    : "Hora de Chile";
  $("history-note").textContent = state && state.estado === "esperando_datos"
    ? "Las cifras son provisorias: el día se cierra cuando la API confirme los reportes hasta las 00:00."
    : state && state.estado === "en_curso" ? "Cifras parciales del día de hoy; se actualizan cada pocos minutos." : "";

  const body = $("history-rows");
  body.replaceChildren();
  for (const truck of data.camiones || []) {
    const tr = document.createElement("tr");
    tr.className = "history-row";
    tr.tabIndex = 0;
    tr.title = "Ver los reportes de este camión en el día";
    tr.append(
      cell(truck.asset_name || "Camión sin nombre", truck.asset_id),
      cell(format(truck.reportes)),
      cell(formatLocalTime(truck.primer_reporte, zone)),
      cell(formatLocalTime(truck.ultimo_reporte, zone)),
      cell(format(truck.km_observados, 1)),
      cell(format(truck.viajes), "recorridos entre paradas"),
      cell(formatMinutes(truck.minutos_detenido), `${format(truck.paradas || 0)} ${Number(truck.paradas) === 1 ? "parada" : "paradas"} de 10 min o más`)
    );
    const open = () => {
      for (const row of body.querySelectorAll(".is-selected")) row.classList.remove("is-selected");
      tr.classList.add("is-selected");
      loadTruckDetail(truck.asset_id, truck.asset_name, data.fecha);
    };
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
    body.append(tr);
  }
  if (!(data.camiones || []).length) {
    emptyRow(body, 7, state ? "Ningún camión reportó este día." : "Este día todavía no tiene resumen calculado.");
  }
  if (historyState.detail && historyState.detail.day !== data.fecha) closeTruckDetail();
}

async function loadTruckDetail(assetId, assetName, day, after = null) {
  const detail = after ? historyState.detail : {assetId, assetName, day, next: null, hasIgnition: false};
  historyState.detail = detail;
  $("history-detail").hidden = false;
  $("history-detail-title").textContent = `Reportes de ${assetName || assetId} · ${day} (hora de Chile)`;
  const body = $("history-detail-rows");
  if (!after) { body.replaceChildren(); emptyRow(body, 4, "Cargando reportes…"); }
  $("history-detail-more").hidden = true;
  try {
    const query = `/api/reportes/camion?asset_id=${encodeURIComponent(assetId)}&fecha=${day}&limite=500` +
      (after ? `&despues=${encodeURIComponent(after)}` : "");
    const data = await api(query);
    if (historyState.detail !== detail) return;
    if (!after) body.replaceChildren();
    for (const report of data.reportes || []) {
      const tr = document.createElement("tr");
      tr.append(
        cell(formatLocalTime(report.report_date, data.zona)),
        cell(report.event_name || "Sin evento"),
        cell(`${Number(report.latitude).toFixed(5)}, ${Number(report.longitude).toFixed(5)}`)
      );
      const ignition = cell(hasValue(report.ignition) ? (Number(report.ignition) === 1 ? "Sí" : "No") : "");
      ignition.className = "col-ignition";
      tr.append(ignition);
      if (hasValue(report.ignition)) detail.hasIgnition = true;
      body.append(tr);
    }
    body.closest("table").classList.toggle("no-ignition", !detail.hasIgnition);
    if (!body.children.length) emptyRow(body, 4, "Sin reportes para este camión en el día.");
    detail.next = data.siguiente;
    $("history-detail-more").hidden = !data.siguiente;
  } catch (error) {
    if (historyState.detail !== detail) return;
    if (!after) body.replaceChildren();
    emptyRow(body, 4, "No se pudieron cargar los reportes: " + error.message);
  }
}

function closeTruckDetail() {
  historyState.detail = null;
  $("history-detail").hidden = true;
  for (const row of document.querySelectorAll("#history-rows .is-selected")) row.classList.remove("is-selected");
}

async function loadHistoryMonth(month) {
  historyState.month = month;
  $("history-month").value = month;
  $("history-month").max = localToday().slice(0, 7);
  const body = $("history-month-rows");
  try {
    const data = await api(`/api/reportes/mes?mes=${month}`);
    if (historyState.month !== month) return;
    body.replaceChildren();
    for (const day of data.dias || []) {
      const tr = document.createElement("tr");
      const link = document.createElement("button");
      link.type = "button"; link.className = "history-day-link";
      link.textContent = day.fecha;
      link.addEventListener("click", () => loadHistoryDay(day.fecha));
      const first = document.createElement("td"); first.append(link);
      const [label] = HISTORY_STATES[day.estado] || [day.estado];
      tr.append(first, cell(label), cell(format(day.camiones)), cell(format(day.reportes)), cell(format(day.km_observados, 1)));
      body.append(tr);
    }
    if (!(data.dias || []).length) emptyRow(body, 5, "No hay días procesados en este mes.");
  } catch (error) {
    if (historyState.month !== month) return;
    body.replaceChildren();
    emptyRow(body, 5, "No se pudo consultar el mes: " + error.message);
  }
}

function csvValue(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function csvLocalDateTime(value, zone) {
  if (!value) return "";
  return new Intl.DateTimeFormat("es-CL", {
    dateStyle: "short", timeStyle: "medium", timeZone: zone || HISTORY_ZONE
  }).format(new Date(value));
}

async function downloadHistoryMonth() {
  const month = $("history-month").value || historyState.month;
  if (!month) return;
  const button = $("history-month-download");
  const status = $("history-month-download-status");
  button.disabled = true;
  button.textContent = "Preparando CSV…";
  status.className = "history-download-status";
  status.textContent = "Reuniendo el detalle completo del mes…";
  try {
    const data = await api(`/api/reportes/mes-completo?mes=${encodeURIComponent(month)}`);
    const rows = data.filas || [];
    if (!rows.length) throw new Error("Este mes todavía no tiene informes procesados.");
    const headers = [
      "Fecha", "Estado del día", "Patente", "Camión", "Reportes", "Primer reporte (Chile)",
      "Último reporte (Chile)", "Km observados", "Viajes", "Tramos unidos", "Paradas",
      "Tiempo detenido (minutos)"
    ];
    const lines = [headers.map(csvValue).join(";")];
    for (const row of rows) {
      const [stateLabel] = HISTORY_STATES[row.estado] || [row.estado || ""];
      lines.push([
        row.fecha, stateLabel, row.asset_id, row.asset_name, row.reportes,
        csvLocalDateTime(row.primer_reporte, data.zona),
        csvLocalDateTime(row.ultimo_reporte, data.zona),
        row.km_observados, row.viajes, row.tramos_unidos, row.paradas, row.minutos_detenido
      ].map(csvValue).join(";"));
    }
    const blob = new Blob(["\uFEFF", lines.join("\r\n")], {type: "text/csv;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `artisa-informe-${month}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = `CSV generado con ${format(rows.length)} registros del mes.`;
  } catch (error) {
    status.className = "history-download-status is-error";
    status.textContent = "No se pudo generar el CSV: " + error.message;
  } finally {
    button.disabled = false;
    button.textContent = "↓ Descargar CSV";
  }
}

function refreshHistory() {
  if (!token()) return;
  if (!historyState.loadedOnce) {
    historyState.loadedOnce = true;
    loadHistoryDay(shiftDay(localToday(), -1));
  } else if (historyState.day === localToday()) {
    loadHistoryDay(historyState.day, true);  // hoy en curso: se mantiene al día
  }
}

$("history-date").addEventListener("change", () => loadHistoryDay($("history-date").value));
$("history-prev").addEventListener("click", () => loadHistoryDay(shiftDay(historyState.day || localToday(), -1)));
$("history-next").addEventListener("click", () => loadHistoryDay(shiftDay(historyState.day || localToday(), 1)));
$("history-yesterday").addEventListener("click", () => loadHistoryDay(shiftDay(localToday(), -1)));
$("history-today").addEventListener("click", () => loadHistoryDay(localToday()));
$("history-month").addEventListener("change", () => { if ($("history-month").value) loadHistoryMonth($("history-month").value); });
$("history-month-download").addEventListener("click", downloadHistoryMonth);
$("history-detail-close").addEventListener("click", closeTruckDetail);
$("history-detail-more").addEventListener("click", () => {
  const detail = historyState.detail;
  if (detail && detail.next) loadTruckDetail(detail.assetId, detail.assetName, detail.day, detail.next);
});

$("refresh").addEventListener("click", refresh);
for (const button of document.querySelectorAll("[data-map-mode]")) {
  button.addEventListener("click", () => setMapMode(button.dataset.mapMode, true));
}
for (const button of document.querySelectorAll("[data-route-filter]")) {
  button.addEventListener("click", () => {
    activeRouteFilter = button.dataset.routeFilter || "ALL";
    if (selectedRouteId || selectedVehicleId || activeJourneyVehicleId) resetMapSelection();
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
// Retirar el almacenamiento de la implementación anterior sin persistir el usuario.
try {
  sessionStorage.removeItem("artisa_token");
  sessionStorage.removeItem("artisa_user");
} catch { /* Puede estar deshabilitado. */ }
window.addEventListener("hashchange", restoreSession);
window.addEventListener("popstate", restoreSession);
window.addEventListener("pageshow", restoreSession);
window.addEventListener("pagehide", () => { appShell.hidden = true; landing.hidden = true; });
window.addEventListener("storage", (event) => {
  if (event.key === TOKEN_KEY || event.key === null) restoreSession();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) restoreSession();
});
// --- Predicción de actividad (/api/prediccion) ----------------------------------------------
// Ruleta de días: un día, un rango o todos; recorrido animado; confianza por día según el
// error real que tuvo el método al predecir 1, 2, … días hacia adelante en la prueba.
const PREDICTION_REFRESH_MS = 15 * 60 * 1000;
const PREDICTION_PLAY_MS = 1600;
const PREDICTION_GRADIENT = {0.2: "#3b4cc0", 0.45: "#2ec4b6", 0.65: "#9be15d", 0.82: "#f9c74f", 1: "#e63946"};
const PREDICTION_METHODS = {
  lightgbm: "Modelo LightGBM",
  promedio_dia_semana: "Promedio por día de la semana",
  promedio_historico: "Promedio histórico"
};
const CONFIDENCE_LABELS = {alta: "Confianza alta", media: "Confianza media", baja: "Confianza baja", na: "Sin evaluar"};
// Dos modelos en pestañas: mapa de calor (zonas) y rutas (tramos).
const PREDICTION_TYPES = {
  calor: {
    endpoint: "/api/prediccion", unit: "reportes", totalLabel: "Reportes esperados",
    chartUnit: "Reportes", errorUnit: "reportes por zona y día",
    value: (item) => item.total, cardText: (item) => format(item.total),
    intro: "Zonas donde se espera más movimiento de camiones",
    method: "Cada zona es un cuadro de ~1 km. Para cada día se predice cuántos reportes de camiones en movimiento " +
      "(sobre 2 km/h) habrá en cada zona"
  },
  rutas: {
    endpoint: "/api/prediccion-rutas", unit: "tramos", totalLabel: "Tramos con camiones esperados",
    chartUnit: "Tramos", errorUnit: "camiones por tramo y día",
    value: (item) => item.elementos, cardText: (item) => format(item.elementos) + " tramos",
    intro: "Tramos de ruta por los que se espera que pasen camiones",
    method: "Cada tramo une dos cuadros vecinos de ~1 km que los camiones cruzan entre dos reportes seguidos " +
      "(5 min o menos y bajo 150 km/h). Para cada día se predice cuántos camiones distintos pasarán por cada tramo"
  }
};
const predictionState = {
  tipo: "calor", cache: {}, data: null, loadedAt: 0, loading: false, map: null, layer: null, fitted: false,
  mode: "day", index: 0, from: 0, to: 0, timer: null, scrollTimer: null, wheelBound: false, renderer: null
};

function predictionConfig() { return PREDICTION_TYPES[predictionState.tipo]; }

// Colores de rutas: rojo = pocos camiones, naranja = medio, verde = muchos (igual que las rutas clasificadas).
function routeHeatColor(ratio) {
  const stops = [[0, [220, 38, 38]], [0.5, [249, 115, 22]], [1, [20, 83, 45]]];
  const x = Math.max(0, Math.min(1, ratio));
  const [i0, i1] = x <= 0.5 ? [0, 1] : [1, 2];
  const t = (x - stops[i0][0]) / (stops[i1][0] - stops[i0][0]);
  const c = stops[i0][1].map((v, k) => Math.round(v + (stops[i1][1][k] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function predictionDate(iso) { return new Date(iso + "T12:00:00Z"); }

function predictionDayLabel(iso, style = "short") {
  const options = style === "short" ? {weekday: "short", day: "numeric"}
    : style === "weekday" ? {weekday: "short"}
      : style === "month" ? {month: "short"}
        : {weekday: "long", day: "numeric", month: "long"};
  return new Intl.DateTimeFormat("es-CL", {...options, timeZone: "UTC"}).format(predictionDate(iso));
}

function predictionConfidence(data, iso) {
  const errors = (data.metricas || {}).error_por_dia || [];
  const ahead = Math.round((predictionDate(iso) - predictionDate(data.base_hasta)) / 86400000) - 1;
  const error = errors[ahead];
  if (error === null || error === undefined || !errors[0]) return {level: "na", error: null};
  const ratio = error / errors[0];
  return {level: ratio <= 1.1 ? "alta" : ratio <= 1.3 ? "media" : "baja", error};
}

function predictionSelected() {
  const days = predictionState.data ? predictionState.data.dias : [];
  if (predictionState.mode === "all") return days;
  if (predictionState.mode === "range") {
    const [a, b] = [predictionState.from, predictionState.to].sort((x, y) => x - y);
    return days.slice(a, b + 1);
  }
  return days.slice(predictionState.index, predictionState.index + 1);
}

function predictionCells(days) {
  // Calor: suma de los días. Rutas: promedio de camiones por día en cada tramo.
  if (days.length === 1) return days[0].celdas;
  const routes = predictionState.tipo === "rutas";
  const sum = new Map();
  for (const item of days) {
    for (const cell of item.celdas) {
      const key = cell.slice(0, -1).join(",");
      const previous = sum.get(key);
      sum.set(key, [...cell.slice(0, -1), (previous ? previous[previous.length - 1] : 0) + cell[cell.length - 1]]);
    }
  }
  const result = [...sum.values()];
  if (routes) for (const cell of result) cell[cell.length - 1] /= days.length;
  return result;
}

function ensurePredictionMap() {
  if (predictionState.map || !window.L) return predictionState.map;
  predictionState.map = L.map($("prediction-map"), {zoomControl: true}).setView([-24.5, -70], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors"
  }).addTo(predictionState.map);
  return predictionState.map;
}

function drawPrediction() {
  const data = predictionState.data;
  if (!data || !data.dias.length) return;
  const days = predictionSelected();
  const config = predictionConfig();
  const routes = predictionState.tipo === "rutas";
  const cells = predictionCells(days);
  const total = routes ? cells.length : days.reduce((sum, item) => sum + item.total, 0);
  // Resumen de la selección
  $("prediction-total-label").textContent = config.totalLabel;
  $("prediction-total").textContent = format(total);
  $("prediction-total-detail").textContent = days.length === 1
    ? predictionDayLabel(days[0].fecha, "long") : `En ${days.length} días · camiones en movimiento`;
  const text = $("prediction-selection");
  if (days.length === 1) {
    const confidence = predictionConfidence(data, days[0].fecha);
    text.innerHTML = "";
    const strong = document.createElement("strong");
    const label = predictionDayLabel(days[0].fecha, "long");
    strong.textContent = label.charAt(0).toUpperCase() + label.slice(1);
    const badge = document.createElement("span");
    badge.className = "confidence-badge " + confidence.level;
    badge.textContent = CONFIDENCE_LABELS[confidence.level];
    const amount = routes ? `${format(days[0].elementos)} tramos con camiones esperados `
      : `${format(days[0].total)} reportes esperados `;
    text.append(strong, " · " + amount, badge,
      confidence.error === null ? "" : ` · se equivoca ~${format(confidence.error, 2)} ${routes ? "camiones por tramo" : "por zona"}`);
  } else {
    text.textContent = `${predictionDayLabel(days[0].fecha, "long")} → ${predictionDayLabel(days[days.length - 1].fecha, "long")}` +
      (routes ? ` · ${format(total)} tramos con camiones (promedio de camiones por día en cada tramo)`
        : ` · ${format(total)} reportes esperados en ${days.length} días`);
  }
  // Mapa de calor
  const map = ensurePredictionMap();
  if (map) {
    map.invalidateSize();
    if (predictionState.layer) map.removeLayer(predictionState.layer);
    const max = cells.reduce((top, cell) => Math.max(top, cell[cell.length - 1]), 0);
    if (routes) {
      // Tramos como líneas: de menos a más usados, para que los verdes queden encima.
      predictionState.renderer = predictionState.renderer || L.canvas({padding: 0.5});
      const layer = L.layerGroup();
      const sorted = [...cells].sort((a, b) => a[4] - b[4]);
      for (const [lat1, lon1, lat2, lon2, value] of sorted) {
        const ratio = max ? Math.sqrt(value / max) : 0;
        L.polyline([[lat1, lon1], [lat2, lon2]], {
          renderer: predictionState.renderer, color: routeHeatColor(ratio), weight: 2 + ratio * 4,
          opacity: 0.55 + ratio * 0.4, lineCap: "round"
        }).bindTooltip(`${format(value, 1)} camiones ${days.length > 1 ? "por día (promedio)" : "esperados"}`, {sticky: true})
          .addTo(layer);
      }
      predictionState.layer = layer;
    } else {
      // Escala logarítmica, igual que el mapa de calor del cuaderno (np.log1p).
      const points = cells.map(([lat, lon, value]) => [lat, lon, max ? Math.log1p(value) / Math.log1p(max) : 0]);
      predictionState.layer = L.heatLayer
        ? L.heatLayer(points, {radius: 9, blur: 11, minOpacity: 0.3, max: 1, maxZoom: 18, gradient: PREDICTION_GRADIENT})
        : L.layerGroup();
    }
    predictionState.layer.addTo(map);
    if (!predictionState.fitted && cells.length) {
      map.fitBounds(L.latLngBounds(cells.map((cell) => [cell[0], cell[1]])), {padding: [30, 30], maxZoom: 9});
      predictionState.fitted = true;
    }
  }
  // Marcar la selección en la ruleta y en las columnas
  const selected = new Set(days.map((item) => item.fecha));
  for (const node of document.querySelectorAll("[data-prediction-fecha]")) {
    node.classList.toggle("is-selected", selected.has(node.dataset.predictionFecha));
  }
  for (const button of document.querySelectorAll("[data-prediction-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.predictionMode === predictionState.mode));
  }
  $("prediction-range").hidden = predictionState.mode !== "range";
  updatePredictionRange();
}

// --- Ruleta ---
function buildPredictionWheel(data, today) {
  const wheel = $("prediction-wheel");
  wheel.replaceChildren(...data.dias.map((item, index) => {
    const confidence = predictionConfidence(data, item.fecha);
    const card = document.createElement("button");
    card.type = "button";
    card.className = "wheel-day" + (item.fecha === today ? " is-today" : "");
    card.dataset.predictionFecha = item.fecha;
    card.dataset.index = String(index);
    card.setAttribute("role", "option");
    const weekday = document.createElement("small");
    weekday.textContent = item.fecha === today ? "Hoy" : predictionDayLabel(item.fecha, "weekday");
    const day = document.createElement("strong");
    day.textContent = String(predictionDate(item.fecha).getUTCDate());
    const month = document.createElement("small");
    month.textContent = predictionDayLabel(item.fecha, "month");
    const total = document.createElement("span");
    total.textContent = predictionConfig().cardText(item);
    const dot = document.createElement("i");
    dot.className = "confidence-dot " + confidence.level;
    dot.title = CONFIDENCE_LABELS[confidence.level];
    card.append(weekday, day, month, total, dot);
    card.addEventListener("click", () => selectPredictionIndex(index, true));
    return card;
  }));
  if (!predictionState.wheelBound) {
    predictionState.wheelBound = true;
    wheel.addEventListener("scroll", () => {
      paintPredictionWheel();
      clearTimeout(predictionState.scrollTimer);
      predictionState.scrollTimer = setTimeout(() => {
        if (predictionState.mode !== "day" || predictionState.timer) return;
        const index = centeredPredictionIndex();
        if (index !== predictionState.index) { predictionState.index = index; drawPrediction(); }
      }, 140);
    }, {passive: true});
    // Rueda del mouse: avanza un día por movimiento, como una ruleta. Solo cuando la ruleta
    // está activa (después de tocarla), para no robarle el desplazamiento a la página.
    wheel.addEventListener("pointerdown", () => wheel.focus({preventScroll: true}));
    wheel.addEventListener("wheel", (event) => {
      if (!wheel.contains(document.activeElement) || Math.abs(event.deltaY) < Math.abs(event.deltaX)) return;
      event.preventDefault();
      stopPredictionPlay();
      selectPredictionIndex(predictionState.index + (event.deltaY > 0 ? 1 : -1), true);
    }, {passive: false});
    wheel.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        selectPredictionIndex(predictionState.index + (event.key === "ArrowRight" ? 1 : -1), true);
      }
    });
    window.addEventListener("resize", () => centerPredictionWheel(false));
  }
  requestAnimationFrame(() => centerPredictionWheel(false));
}

function centeredPredictionIndex() {
  const wheel = $("prediction-wheel");
  if (!wheel.clientWidth) return predictionState.index;  // oculta: no cambiar la selección
  const middle = wheel.scrollLeft + wheel.clientWidth / 2;
  let best = 0, distance = Infinity;
  for (const card of wheel.children) {
    const gap = Math.abs(card.offsetLeft + card.offsetWidth / 2 - middle);
    if (gap < distance) { distance = gap; best = Number(card.dataset.index); }
  }
  return best;
}

function paintPredictionWheel() {
  // Efecto de ruleta: las tarjetas giran y se achican al alejarse del centro.
  const wheel = $("prediction-wheel");
  const middle = wheel.scrollLeft + wheel.clientWidth / 2;
  for (const card of wheel.children) {
    const offset = (card.offsetLeft + card.offsetWidth / 2 - middle) / (card.offsetWidth + 10);
    const reach = Math.min(Math.abs(offset), 4);
    card.style.transform = `perspective(700px) rotateY(${Math.max(-55, Math.min(55, -offset * 16))}deg) scale(${1 - reach * 0.07})`;
    card.style.opacity = String(1 - reach * 0.16);
    card.classList.toggle("is-center", reach < 0.5);
  }
}

function centerPredictionWheel(smooth = true) {
  const wheel = $("prediction-wheel");
  const index = predictionState.mode === "range" ? predictionState.to : predictionState.index;
  const card = wheel.children[index];
  if (!card) return;
  wheel.scrollTo({left: card.offsetLeft + card.offsetWidth / 2 - wheel.clientWidth / 2, behavior: smooth ? "smooth" : "auto"});
  paintPredictionWheel();
}

function selectPredictionIndex(index, smooth) {
  const days = predictionState.data ? predictionState.data.dias : [];
  if (!days.length) return;
  index = Math.max(0, Math.min(days.length - 1, index));
  if (predictionState.mode === "range") {
    // En modo rango, tocar un día mueve el extremo más cercano.
    if (Math.abs(index - predictionState.from) <= Math.abs(index - predictionState.to)) predictionState.from = index;
    else predictionState.to = index;
  } else {
    predictionState.mode = "day";
    predictionState.index = index;
  }
  drawPrediction();
  centerPredictionWheelTo(index, smooth);
}

function centerPredictionWheelTo(index, smooth) {
  const wheel = $("prediction-wheel");
  const card = wheel.children[index];
  if (card) wheel.scrollTo({left: card.offsetLeft + card.offsetWidth / 2 - wheel.clientWidth / 2, behavior: smooth ? "smooth" : "auto"});
}

// --- Rango ---
function updatePredictionRange() {
  const days = predictionState.data ? predictionState.data.dias.length : 0;
  const from = $("prediction-range-from"), to = $("prediction-range-to");
  for (const input of [from, to]) input.max = String(Math.max(0, days - 1));
  from.value = String(predictionState.from);
  to.value = String(predictionState.to);
  const [a, b] = [predictionState.from, predictionState.to].sort((x, y) => x - y);
  const span = Math.max(1, days - 1);
  const fill = $("prediction-range-fill");
  fill.style.left = (a / span * 100) + "%";
  fill.style.width = ((b - a) / span * 100) + "%";
}

function setPredictionMode(mode) {
  stopPredictionPlay();
  const days = predictionState.data ? predictionState.data.dias.length : 0;
  if (mode === "range" && predictionState.mode !== "range") {
    predictionState.from = predictionState.index;
    predictionState.to = Math.min(days - 1, predictionState.index + 6);
  }
  predictionState.mode = mode;
  drawPrediction();
}

// --- Recorrido animado ---
function stopPredictionPlay() {
  clearInterval(predictionState.timer);
  predictionState.timer = null;
  const button = $("prediction-play");
  button.setAttribute("aria-pressed", "false");
  button.textContent = "▶ Recorrer días";
}

function togglePredictionPlay() {
  if (predictionState.timer) { stopPredictionPlay(); return; }
  const days = predictionState.data ? predictionState.data.dias.length : 0;
  if (!days) return;
  predictionState.mode = "day";
  const button = $("prediction-play");
  button.setAttribute("aria-pressed", "true");
  button.textContent = "❚❚ Pausar";
  const step = () => {
    predictionState.index = (predictionState.index + 1) % days;
    drawPrediction();
    centerPredictionWheelTo(predictionState.index, true);
  };
  if (predictionState.index >= days - 1) predictionState.index = -1;
  step();
  predictionState.timer = setInterval(step, PREDICTION_PLAY_MS);
}

function renderPrediction(data) {
  predictionState.data = data;
  const status = $("prediction-status");
  if (!data || data.status !== "ok" || !data.dias.length) {
    status.textContent = "Pendiente";
    status.className = "status neutral";
    $("prediction-intro").textContent = (data && data.message) ||
      "La predicción se calcula sola cuando hay al menos 21 días cerrados.";
    $("prediction-wheel").replaceChildren();
    return;
  }
  const m = data.metricas || {};
  const usesModel = m.metodo === "lightgbm";
  const errors = m.errores || {};
  status.textContent = PREDICTION_METHODS[m.metodo] || "Predicción";
  status.className = "status " + (usesModel ? "" : "neutral");
  const today = localToday();
  const first = data.dias[0].fecha, last = data.dias[data.dias.length - 1].fecha;
  const config = predictionConfig();
  for (const tab of document.querySelectorAll("[data-prediction-tab]")) {
    const selected = tab.dataset.predictionTab === predictionState.tipo;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-selected", String(selected));
  }
  $("prediction-legend-heat").hidden = predictionState.tipo !== "calor";
  $("prediction-legend-routes").hidden = predictionState.tipo !== "rutas";
  $("prediction-chart-unit").textContent = config.chartUnit;
  $("prediction-intro").textContent =
    `${config.intro} entre el ${predictionDayLabel(first, "long")} y el ` +
    `${predictionDayLabel(last, "long")} (${data.dias.length} días). Gira la ruleta para elegir un día, ` +
    "elige un rango o recorre los días con ▶. Se recalcula sola cada noche." +
    ((data.metricas || {}).dias_descartados
      ? ` Se muestran solo los días en que el modelo se equivoca poco; ${data.metricas.dias_descartados} días ` +
        "más allá se descartaron por tener demasiado error." : "");
  const peak = data.dias.reduce((top, item) => config.value(item) > config.value(top) ? item : top, data.dias[0]);
  $("prediction-peak").textContent = predictionDayLabel(peak.fecha);
  $("prediction-peak-detail").textContent = format(config.value(peak)) + " " + config.unit + " esperados";
  const error = m.mae_publicado ?? errors[m.metodo] ?? m.mae_referencia;
  $("prediction-error").textContent = error === undefined ? "—" : format(error, 2);
  const others = Object.entries(errors).filter(([method]) => method !== m.metodo)
    .map(([method, value]) => `${PREDICTION_METHODS[method] || method}: ${format(value, 2)}`);
  $("prediction-error-detail").textContent = others.length
    ? others.join(" · ") + " · menor es mejor" : `En ${config.errorUnit} · menor es mejor`;
  $("prediction-base").textContent = new Intl.DateTimeFormat("es-CL", {
    day: "numeric", month: "short", timeZone: "UTC"}).format(predictionDate(data.base_hasta));
  $("prediction-base-detail").textContent = m.dias_historia
    ? `${format(m.dias_historia)} días de historia · ${format(m.camiones || 0)} camiones` : "—";
  $("prediction-method").textContent =
    config.method + ", usando los días anteriores, el mismo día de las semanas pasadas, la tendencia y los " +
    "promedios recientes. Los días siguientes se apoyan en la predicción del día anterior, por eso cada día " +
    "muestra su confianza: es el error real que tuvo el método al predecir esa misma distancia hacia " +
    "adelante en los días de prueba (alta: hasta 10 % más que el primer día; media: hasta 30 %; baja: más). " +
    `Compiten tres métodos en ${format(m.dias_prueba || 0)} días de prueba que ninguno vio: el modelo LightGBM, ` +
    "el promedio del mismo día de la semana y el promedio de todo el período. " +
    `Se publica el de menor error: ${PREDICTION_METHODS[m.metodo] || m.metodo}` +
    (error === undefined ? "." : ` (${format(error, 2)} ${config.errorUnit} de error).`) +
    (!usesModel && m.motivo ? ` ${m.motivo}.` : "");
  // Mantener la selección si sigue existiendo; si no, hoy (o el primer día).
  const count = data.dias.length;
  const todayIndex = Math.max(0, data.dias.findIndex((item) => item.fecha === today));
  if (predictionState.index >= count || !predictionState.loadedAt) predictionState.index = todayIndex;
  predictionState.from = Math.min(predictionState.from, count - 1);
  predictionState.to = Math.min(predictionState.to || count - 1, count - 1);
  buildPredictionWheel(data, today);
  renderPredictionCharts(data, today);
  drawPrediction();
}

function renderPredictionCharts(data, today) {
  // Columnas: total esperado por día (clic = ver ese día). La transparencia indica la confianza.
  const chart = $("prediction-daily-chart");
  const config = predictionConfig();
  const peak = Math.max(1, ...data.dias.map((item) => config.value(item)));
  chart.replaceChildren(...data.dias.map((item, index) => {
    const weekday = predictionDate(item.fecha).getUTCDay();
    const confidence = predictionConfidence(data, item.fecha);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "prediction-column confidence-" + confidence.level +
      (weekday === 0 || weekday === 6 ? " weekend" : "") + (item.fecha === today ? " is-today" : "");
    button.dataset.predictionFecha = item.fecha;
    button.title = `${predictionDayLabel(item.fecha, "long")}: ${format(config.value(item))} ${config.unit} esperados · ` +
      CONFIDENCE_LABELS[confidence.level];
    const value = document.createElement("strong");
    value.textContent = format(config.value(item));
    const bar = document.createElement("i");
    bar.style.height = Math.max(2, config.value(item) / peak * 100) + "%";
    const label = document.createElement("span");
    label.textContent = item.fecha === today ? "Hoy" : predictionDayLabel(item.fecha);
    button.append(value, bar, label);
    button.addEventListener("click", () => { stopPredictionPlay(); selectPredictionIndex(index, true); });
    return button;
  }));
  // Barras: error de cada método; el publicado en verde.
  const m = data.metricas || {};
  const errors = m.errores || (m.mae_referencia !== undefined ? {promedio_historico: m.mae_referencia} : {});
  const order = ["lightgbm", "promedio_dia_semana", "promedio_historico"].filter((key) => key in errors);
  renderDashboardBars("prediction-methods-chart", order.map((key) => ({
    label: (PREDICTION_METHODS[key] || key) + (key === m.metodo ? " · publicado" : ""),
    value: errors[key],
    color: key === m.metodo ? "#246c55" : "#b8c6bf",
    detail: key === m.metodo ? "El de menor error: es el que se ve en el mapa"
      : `${format((errors[key] / errors[m.metodo] - 1) * 100, 1)} % más error que el publicado`
  })), "#246c55");
  for (const [index, row] of [...$("prediction-methods-chart").children].entries()) {
    row.classList.toggle("winner", order[index] === m.metodo);
    const strong = row.querySelector("strong");
    if (strong) strong.textContent = format(errors[order[index]], 3);
  }
}

async function loadPrediction(force = false) {
  if (!token() || predictionState.loading) return;
  const tipo = predictionState.tipo;
  const cached = predictionState.cache[tipo];
  if (!force && cached && Date.now() - cached.loadedAt < PREDICTION_REFRESH_MS) {
    if (predictionState.data !== cached.data) renderPrediction(cached.data);
    else { drawPrediction(); centerPredictionWheel(false); }
    return;
  }
  predictionState.loading = true;
  if (!cached) {
    $("prediction-status").textContent = "Cargando…";
    $("prediction-intro").textContent = "Cargando la predicción…";
  }
  try {
    const data = await api(PREDICTION_TYPES[tipo].endpoint, {timeoutMs: 120000});
    predictionState.cache[tipo] = {data, loadedAt: Date.now()};
    predictionState.loadedAt = Date.now();
    predictionState.retries = 0;
    if (predictionState.tipo === tipo) renderPrediction(data);
  } catch (error) {
    if (!cached) {
      $("prediction-status").textContent = "Reintentando…";
      $("prediction-status").className = "status warning";
      $("prediction-intro").textContent = "No se pudo consultar la predicción (" + error.message + "). Se reintentará sola.";
      // Reintento automático mientras se esté mirando la sección (máx. 5 veces).
      predictionState.retries = (predictionState.retries || 0) + 1;
      if (predictionState.retries <= 5) {
        setTimeout(() => { if (window.location.hash === "#prediccion") loadPrediction(); }, 8000);
      }
    }
  } finally {
    predictionState.loading = false;
  }
}

function setPredictionType(tipo) {
  if (tipo === predictionState.tipo) return;
  stopPredictionPlay();
  predictionState.tipo = tipo;
  loadPrediction();
}

for (const tab of document.querySelectorAll("[data-prediction-tab]")) {
  tab.addEventListener("click", () => setPredictionType(tab.dataset.predictionTab));
}

for (const button of document.querySelectorAll("[data-prediction-mode]")) {
  button.addEventListener("click", () => setPredictionMode(button.dataset.predictionMode));
}
$("prediction-play").addEventListener("click", togglePredictionPlay);
$("prediction-prev").addEventListener("click", () => { stopPredictionPlay(); selectPredictionIndex((predictionState.mode === "range" ? predictionState.to : predictionState.index) - 1, true); });
$("prediction-next").addEventListener("click", () => { stopPredictionPlay(); selectPredictionIndex((predictionState.mode === "range" ? predictionState.to : predictionState.index) + 1, true); });
for (const id of ["prediction-range-from", "prediction-range-to"]) {
  $(id).addEventListener("input", () => {
    predictionState.from = Number($("prediction-range-from").value);
    predictionState.to = Number($("prediction-range-to").value);
    drawPrediction();
  });
}
document.addEventListener("visibilitychange", () => { if (document.hidden) stopPredictionPlay(); });
window.addEventListener("hashchange", () => { if (window.location.hash !== "#prediccion") stopPredictionPlay(); });
setInterval(() => { if (window.location.hash === "#prediccion") loadPrediction(); }, PREDICTION_REFRESH_MS);

restoreSession();
setInterval(refresh, 300000);
setInterval(refreshTelemetry, TELEMETRY_REFRESH_MS);
// Al recuperar la red o volver a la pestaña, actualizar de inmediato sin esperar el minuto.
window.addEventListener("online", () => { refreshTelemetry(); refresh(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshTelemetry(); });
