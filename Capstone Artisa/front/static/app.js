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
  activeMapMode = "historical";
  userChoseMapMode = false;
  for (const wrapper of document.querySelectorAll(".explorer-frame-wrap")) {
    wrapper.hidden = true;
    const frame = wrapper.querySelector("iframe");
    if (frame) frame.removeAttribute("src");
  }
  const dashboardPreview = $("dashboard-route-heatmap");
  if (dashboardPreview) dashboardPreview.hidden = false;
  for (const button of document.querySelectorAll("[data-explorer-open]")) button.hidden = false;
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

function openExplorer(which) {
  if (!token()) return;
  if (which === "routes") {
    setMapMode(activeMapMode);
    return;
  }
  const button = document.querySelector(`[data-explorer-open="${which}"]`);
  const wrapper = button && $(button.getAttribute("aria-controls"));
  const frame = wrapper?.querySelector("iframe[data-explorer-frame]");
  if (!frame) return;
  if (!frame.hasAttribute("src")) frame.src = frame.dataset.explorerFrame;
  wrapper.hidden = false;
  button.hidden = true;
  if (which === "dashboard") $("dashboard-route-heatmap").hidden = true;
  frame.contentWindow?.postMessage("artisa:show-map", location.origin);
}

document.querySelectorAll("[data-explorer-open]").forEach((button) => {
  button.addEventListener("click", () => openExplorer(button.dataset.explorerOpen));
});

async function api(path, options = {}) {
  const currentToken = token();
  if (!currentToken) throw new Error(EXPIRED_MESSAGE);
  const version = sessionVersion;
  const controller = new AbortController();
  pendingRequests.add(controller);
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${currentToken}`);
    const response = await fetch(path, {...options, headers, signal: controller.signal});
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
  if (["#datos-simulados", "#viajes", "#simulador"].includes(window.location.hash)) {
    history.replaceState(null, "", "#datos-reales");
  }
  let view = "real";
  if (window.location.hash === "#mapa-calor") view = "heatmap";
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
  if (view === "dashboard" || view === "heatmap") {
    window.setTimeout(() => openExplorer(view === "dashboard" ? "dashboard" : "routes"), 50);
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
let journeyRequest = 0;
let activeJourneyVehicleId = null;
let pendingMapBounds = null;
let mapHasFitted = false;
let currentMapData = null;
let selectedRouteId = null;
let selectedVehicleId = null;
const truckMarkers = new Map();
const routeMapLayers = new Map();

function setMapMode(mode, remember = false, pending = !currentMapData || machinePending(currentMapData)) {
  activeMapMode = mode === "classified" && !pending ? "classified" : "historical";
  if (remember) userChoseMapMode = true;
  const historical = activeMapMode === "historical";
  const explorer = $("explorer-routes");
  explorer.closest(".map-card").classList.toggle("showing-history", historical);
  explorer.hidden = !historical;
  $("heatmap-map").hidden = historical;
  $("classified-map-legend").hidden = historical;
  for (const button of document.querySelectorAll("[data-map-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mapMode === activeMapMode));
    if (button.dataset.mapMode === "classified") button.disabled = pending;
  }
  $("map-view-description").textContent = historical
    ? "GPS histórico de MariaDB; los filtros de rutas clasificadas estarán disponibles cuando Machine entregue resultados."
    : "Rutas clasificadas por Machine; los filtros y vehículos corresponden a este mapa.";
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

function refreshSelectionStyles() {
  for (const [routeId, record] of routeMapLayers) {
    const selected = routeId === selectedRouteId;
    record.halo.setStyle({
      weight: selected ? record.haloWeight + 3 : record.haloWeight,
      opacity: selected ? 1 : record.haloOpacity
    });
    record.line.setStyle({
      weight: selected ? record.baseWeight + 3 : record.baseWeight,
      opacity: selected ? 1 : record.baseOpacity
    });
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

const TRUCK_MARKUP = '<svg class="truck-glyph" viewBox="0 0 32 32" aria-hidden="true" focusable="false">' +
  '<rect x="7" y="3" width="18" height="17" rx="2" fill="#f9fbf7" stroke="#173f35" stroke-width="1.5"/>' +
  '<rect x="6" y="20" width="20" height="8" rx="2" fill="#f9fbf7" stroke="#173f35" stroke-width="1.5"/>' +
  '<rect x="9" y="22" width="14" height="3" rx="1" fill="#8fc1d1"/>' +
  '<rect x="4" y="6" width="3" height="6" rx="1" fill="#173f35"/>' +
  '<rect x="25" y="6" width="3" height="6" rx="1" fill="#173f35"/>' +
  '<rect x="8" y="27" width="4" height="3" rx="1" fill="#173f35"/>' +
  '<rect x="20" y="27" width="4" height="3" rx="1" fill="#173f35"/>' +
  '</svg>';

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
    icon.innerHTML = TRUCK_MARKUP;
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
    const visible = mapLayerVisibility.routes && !activeJourneyVehicleId &&
      (activeRouteFilter === "ALL" || activeRouteFilter === level);
    setLayerVisibility(routeLayers[level], visible);
  }
  for (const button of document.querySelectorAll("[data-route-filter]")) {
    const selected = button.dataset.routeFilter === activeRouteFilter;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    button.disabled = !mapLayerVisibility.routes || !!activeJourneyVehicleId;
  }
  if (currentMapData) renderRouteMonitor(currentMapData.routes);
}

function applyMapLayerVisibility() {
  if (!fleetMap) return;
  for (const [vehicleId, marker] of truckMarkers) {
    const visible = !activeJourneyVehicleId || vehicleId === activeJourneyVehicleId;
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
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(fleetMap);
  const pendingNote = document.createElement("div");
  pendingNote.id = "map-pending-note";
  pendingNote.className = "map-pending-note";
  pendingNote.textContent = "Mapa base OpenStreetMap · rutas pendientes de procesamiento Machine";
  pendingNote.hidden = true;
  container.append(pendingNote);

  for (const [paneName, zIndex] of [
    ["routeLowPane", 410], ["routeMediumPane", 420],
    ["routeHighPane", 430], ["journeyPane", 625], ["truckPane", 650]
  ]) {
    fleetMap.createPane(paneName).style.zIndex = String(zIndex);
  }

  for (const level of ROUTE_DRAW_ORDER) routeLayers[level] = L.layerGroup();
  truckLayer = L.layerGroup();
  journeyLayer = L.featureGroup().addTo(fleetMap);
  $("journey-hide").addEventListener("click", resetMapSelection);
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
  const viewJourney = document.createElement("button");
  viewJourney.type = "button";
  viewJourney.className = "popup-journey-button";
  viewJourney.textContent = "Ver recorrido completo";
  viewJourney.addEventListener("click", () => showVehicleJourney(vehicle));
  wrapper.append(title, identifier, state, details, plate, driver, route, date, viewJourney);
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
    fleetMap.setView(marker.getLatLng(), Math.max(fleetMap.getZoom(), 15));
    marker.openPopup();
  }
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
    $("journey-chains").textContent = "No determinados";
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
    fleetMap.closePopup();
    if (journeyLayer.getLayers().length && journeyLayer.getBounds().isValid()) {
      fleetMap.fitBounds(journeyLayer.getBounds(), {padding: [45, 45], maxZoom: 13});
    }
  } catch (error) {
    if (request === journeyRequest) {
      activeJourneyVehicleId = null;
      applyMapLayerVisibility();
      $("journey-note").textContent = "No se pudo consultar el recorrido: " + error.message;
    }
  }
}

function resetMapSelection() {
  hideJourney();
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
    marker.on("click", () => selectVehicle(vehicle, false));
    marker.addTo(truckLayer);
    truckMarkers.set(vehicle.vehicle_id, marker);
  }
  renderMonitorLists();
  applyMapLayerVisibility();

  const sourceLabel = data.sourceMode === "HISTORICAL_GPS_CATALOG"
    ? "Rutas históricas completas"
    : data.sourceMode === "HISTORICAL_GPS_PILOT"
      ? "Ensayo histórico parcial"
      : data.sourceMode === "SIMULATION_PROCESSED"
        ? "Simulación aislada"
        : "Muestra GPS reciente";
  const minutes = Math.max(1, Math.round(Number(data.metadata.refresh_seconds || 300) / 60));
  const isHistorical = data.sourceMode === "HISTORICAL_GPS_CATALOG" ||
    data.sourceMode === "HISTORICAL_GPS_PILOT";
  $("map-last-update").textContent = sourceLabel +
    (isHistorical ? " · catálogo " : " · muestra ") +
    formatDataDay(data.snapshotDate) +
    " · posiciones cada " + minutes + " min";
  $("heatmap-source-status").textContent = sourceLabel;
  $("heatmap-intro").textContent =
    (isHistorical
      ? " Verde, naranja y rojo representan recorridos históricos agrupados. El camión muestra su última lectura disponible."
      : " Los colores pertenecen a las rutas de esta vista; el camión muestra su última lectura disponible.");

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
  if (data.sourceMode !== "HISTORICAL_GPS_PILOT" || !visibleBounds.isValid()) {
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
  $("heatmap-zones").textContent = data.metadata.processed_report_count
    ? format(data.metadata.processed_report_count) + " de " +
      format(data.metadata.source_report_count) + " reportes históricos procesados · " +
      format(summary.vehicle_count || 0) + " vehículos con posición en la ventana"
    : format(summary.window_report_count || 0) + " registros recientes · " +
      format(summary.reading_count || 0) + " posiciones únicas · " +
      format(summary.available_report_count || 0) + " reportes conservados";

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

  $("geo-model-zones").textContent = "Contrato procesado v" + (data.schemaVersion || "1");
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
  $("dashboard-snapshot").textContent = "Corte " + formatDataDay(data.snapshotDate);
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
    "Las coordenadas se dividen en cinco zonas relativas al centro del área observada: norte, centro, sur, oeste y este. " +
    "Cada ruta conserva una sola clasificación y se cuenta en la zona de su centro geométrico. Actualización automática con el mismo bloque del mapa.";
}
function renderTelemetry(summary, dataset, state) {
  const pending = dataset.status === "pending" && dataset.source === "company_api";
  $("telemetry-pending").hidden = !pending;
  $("telemetry-content").hidden = pending;
  if (pending) {
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
  const telemetryState = state.telemetria || state.etl || {};
  $("telemetry-count").textContent = summary.status === "pending" ? "Pendiente de procesamiento" : format(summary.reportes);
  $("vehicle-count").textContent = summary.status === "pending" ? "Pendiente de procesamiento" : format(summary.vehiculos);
  $("telemetry-range").textContent = summary.status === "pending"
    ? "Resumen pendiente de procesamiento; la tabla muestra reportes GPS reales."
    : summary.reportes
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
    esperando: "Sincronizado · esperando nuevos datos",
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
    cell.colSpan = 5;
    cell.className = "empty";
    cell.textContent = "No hay telemetría disponible para mostrar.";
    row.append(cell);
    body.append(row);
  }
}

function renderState(state) {
  const components = state.componentes || {};
  const required = ["etl", "mariadb", "endpoints", "front"];
  const nodes = [...document.querySelectorAll("[data-service]")];
  const count = required.filter((name) => components[name] === "ok").length;
  $("system-status").textContent = format(count) + " de " + format(required.length) + " servicios operativos";
  $("system-status").className = "status " + (required.every((name) => components[name] === "ok") ? "" : "warning");
  for (const node of nodes) {
    const okay = components[node.dataset.service] === "ok";
    const label = node.querySelector(".node-status");
    const pending = node.dataset.service === "machine" && !okay;
    label.textContent = okay ? "En funcionamiento" : pending ? "Pendiente de integración" : "Sin respuesta reciente";
    label.className = "node-status " + (okay ? "ok" : pending ? "neutral" : "bad");
  }
  const etl = state.etl;
  if (etl) {
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
  try {
    const [state, telemetrySummary, telemetryDataset, mapPayload, dashboard] = await Promise.all([
      api("/api/estado"),
      api("/api/telemetria/resumen"),
      api("/api/telemetria?limite=30"),
      api("/api/mapa"),
      api("/api/dashboard")
    ]);
    if (version !== sessionVersion || !token()) return;
    if (!window.ArtisaMapData) throw new Error("No se cargó el adaptador del mapa.");
    const mapData = window.ArtisaMapData.adaptMapPayload(mapPayload);
    renderState(state);
    renderTelemetry(telemetrySummary, telemetryDataset, state);
    renderHeatmap(mapData);
    renderDashboard(mapData, dashboard);
    $("notice").hidden = state.status === "ok";
    $("notice").textContent = "Una etapa no ha respondido recientemente o está preparando datos nuevos. Los datos mostrados corresponden a la última carga disponible.";
  } catch (error) {
    if (version !== sessionVersion || !token()) return;
    $("notice").hidden = false;
    $("notice").textContent = "No se pudo actualizar el sistema. Los datos que ya ves pueden estar desactualizados. " + error.message;
    $("system-status").textContent = "Sin conexión con el sistema";
    $("system-status").className = "status warning";
    for (const label of document.querySelectorAll(".node-status")) {
      label.textContent = "Estado sin verificar"; label.className = "node-status bad";
    }
  } finally {
    if (version === sessionVersion) { refreshing = false; $("refresh").disabled = false; }
  }
}

$("refresh").addEventListener("click", refresh);
for (const button of document.querySelectorAll("[data-map-mode]")) {
  button.addEventListener("click", () => setMapMode(button.dataset.mapMode, true));
}
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
restoreSession();
setInterval(refresh, 300000);
