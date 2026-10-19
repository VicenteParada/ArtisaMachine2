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
let liveVehiclesRefreshing = false;

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
  liveVehiclesRefreshing = false;
  if (typeof vg !== "undefined") vg.loading = false;
  $("refresh").disabled = false;
  window.clearTimeout(fleetRetry);
}

function clearPassword() {
  $("login-password").value = "";
  $("login-password").type = "password";
  $("toggle-password").textContent = "Mostrar";
  $("toggle-password").setAttribute("aria-pressed", "false");
}

function logout(message = "") {
  clearSessionWork();
  if (typeof historyState !== "undefined") Object.assign(historyState, {month: null, detail: null});
  activeMapMode = "live";
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
  if (!location.hash) history.replaceState(null, "", "#camiones");
  syncDatasetView();
  if (appShell.hidden) showApp();
  scheduleExpiration();
  // Al abrir la página se cargan todas las secciones, no solo la visible: así, al cambiar
  // de pestaña los datos ya están (o vienen en camino).
  if (changed) { refresh(); refreshTelemetry(); loadFleet(); loadVigilance(false); }
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
    history.replaceState(null, "", "#camiones");
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
  let view = "trucks";
  if (window.location.hash === "#datos-reales") { history.replaceState(null, "", "#camiones"); }
  else if (window.location.hash === "#mapa-calor") view = "heatmap";
  else if (window.location.hash === "#dashboard") view = "dashboard";
  else if (window.location.hash === "#prediccion") view = "prediction";
  // Arquitectura es una vista aparte; el resto de las vistas parte directo en su contenido.
  const overview = window.location.hash === "#flujo";
  document.querySelector(".heading").hidden = !overview;
  $("flujo").hidden = !overview;
  document.querySelector(".dataset-tabs").hidden = overview;
  for (const space of document.querySelectorAll("[data-dataset-view]")) {
    space.hidden = overview || space.dataset.datasetView !== view;
  }
  for (const tab of document.querySelectorAll("[data-dataset-tab]")) {
    const selected = tab.dataset.datasetTab === view;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-current", selected ? "page" : "false");
  }
  $("overview-link").classList.toggle("active", overview);
  document.querySelector(".topbar strong").textContent = overview ? "Arquitectura"
    : {trucks: "Camiones en vivo", heatmap: "Mapa de rutas", prediction: "Predicción", dashboard: "Dashboard"}[view];
  for (const link of document.querySelectorAll(".sidebar [data-dataset-link]")) {
    link.classList.toggle("active", !overview && link.dataset.datasetLink === view);
  }
  if (overview) return;
  if (view === "trucks") syncTruckView();
  if (view === "heatmap") {
    window.setTimeout(openExplorer, 50);
  }
  if (view === "prediction") {
    window.setTimeout(() => { loadPrediction(); if (predictionState.map) predictionState.map.invalidateSize(); }, 50);
  }
  if (view === "heatmap" && activeMapMode === "live" && fleetMap) {
    window.setTimeout(() => { fleetMap.invalidateSize(); vgFit(false); }, 0);
  }
}

function routeProperties(route) {
  return route && route.properties ? route.properties : {};
}

// --- Mapa de rutas: últimos 7 días en vivo y alertas (/api/vigilancia) -------------------------
// Sin rutas predefinidas: cada celda de ~500 m se pinta según el % de la flota que pasó por
// ella en los últimos 7 días (hasta este minuto). Las alertas marcan camiones que anduvieron
// por zonas que casi nadie de la flota usa. Si dejan de llegar datos, la ventana sigue
// terminando en la hora actual y las alertas abiertas pasan a "sin señal".
const ROUTE_LABELS = Object.freeze({HIGH: "Más usada", MEDIUM: "Uso medio", LOW: "Poco usada"});
// Colores de la clasificación de rutas: los usan Predicción y la ficha de cada camión.
const ROUTE_COLORS = Object.freeze({HIGH: "#14532d", MEDIUM: "#f97316", LOW: "#dc2626"});
// Tramos de la escala: hasta (sin incluir) el % indicado; el primero es "poco habitual".
// Las rutas habituales se dibujan más gruesas: de un vistazo se ve por dónde anda la flota.
const VG_BINS = [
  {hasta: null, color: "#d7301f", weight: 2},  // hasta el umbral: poco habitual (se completa al cargar)
  {hasta: 15, color: "#f28e2b", weight: 3},
  {hasta: 30, color: "#e3b43a", weight: 4},
  {hasta: 50, color: "#5e9f58", weight: 5.5},
  {hasta: Infinity, color: "#1f6b50", weight: 7}
];
const VG_STATES = {
  activa: {label: "Activa", className: "active"},
  sin_senal: {label: "Sin señal", className: "nosignal"},
  terminada: {label: "Terminada", className: "done"}
};
const VG_REFRESH_MS = 60000;
let activeMapMode = "live";
let fleetMap = null;
let usageLayer = null;
let usageRenderer = null;
let trailLayer = null;
let truckLayer = null;
let selectionLayer = null;
const truckMarkers = new Map();
var vg = {data: null, cells: new Map(), alerts: new Map(), vehicles: [],
  tab: "alerts", alertFilter: "all", zone: "all", layers: {usage: true, vehicles: true, trails: true},
  selected: null, fitted: false, loading: false, openPopup: null};

const vgTime = (iso) => iso ? new Intl.DateTimeFormat("es-CL", {
  weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: HISTORY_ZONE}).format(new Date(iso)) : "—";
const vgLocalDay = (iso) => new Intl.DateTimeFormat("en-CA", {timeZone: HISTORY_ZONE}).format(new Date(iso));
const vgDuration = (minutes) => minutes < 60 ? `${format(minutes)} min`
  : `${format(Math.floor(minutes / 60))} h ${String(Math.round(minutes % 60)).padStart(2, "0")} min`;

function setMapMode(mode) {
  activeMapMode = mode === "historical" ? "historical" : "live";
  const historical = activeMapMode === "historical";
  const explorer = $("explorer-routes");
  explorer.closest(".map-card").classList.toggle("showing-history", historical);
  explorer.closest(".heatmap-layout").classList.toggle("is-historical", historical);
  document.querySelector(".heatmap-method").hidden = historical;
  document.querySelector(".atlas-side").hidden = historical;
  for (const panel of document.querySelectorAll(".route-filter-panel, .map-layer-panel")) panel.hidden = historical;
  explorer.hidden = !historical;
  $("heatmap-map").hidden = historical;
  $("classified-map-legend").hidden = historical;
  $("atlas-stats").hidden = historical;
  for (const button of document.querySelectorAll("[data-map-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mapMode === activeMapMode));
  }
  if (historical) {
    const frame = explorer.querySelector("iframe[data-explorer-frame]");
    if (frame && !frame.hasAttribute("src") && token()) frame.src = frame.dataset.explorerFrame;
    frame?.contentWindow?.postMessage("artisa:show-map", location.origin);
  } else if (fleetMap) {
    requestAnimationFrame(() => { fleetMap.invalidateSize(); vgFit(false); });
  }
}

function machinePending(data) {
  return data.status === "pending" || data.sourceMode === "PENDING_MACHINE" || data.metadata?.status === "pending";
}

function setSelectionDetail(kind, title, description, metadata) {
  const detail = $("map-selection-detail");
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

// --- Camiones ---
function vehicleDriver(vehicle) {
  const structured = [vehicle.driver_name, vehicle.driver_last_name]
    .map((part) => String(part || "").trim()).filter(Boolean).join(" ");
  return structured || "No informado";
}

function vehicleStatus(vehicle) {
  const timestamp = vehicle.timestamp ? new Date(vehicle.timestamp).getTime() : NaN;
  if (!Number.isFinite(timestamp)) return {label: "Estado no disponible", className: "off"};
  if (Date.now() - timestamp > 30 * 60 * 1000) return {label: "Última posición conocida", className: "off"};
  if (Number(vehicle.speed_kmh || 0) > 3) return {label: "En movimiento", className: "moving"};
  if (vehicle.ignition !== null && vehicle.ignition !== undefined && Number(vehicle.ignition) === 0) {
    return {label: "Apagado", className: "off"};
  }
  return {label: "Con señal reciente", className: "moving"};
}

const TRUCK_MARKUP = '<img class="truck-glyph" src="/artisa-truck-box-white-green.png?v=20261014" alt="" aria-hidden="true">';

function truckIcon(vehicle) {
  const status = vehicleStatus(vehicle);
  const hasHeading = vehicle.heading !== null && vehicle.heading !== undefined &&
    vehicle.heading !== "" && Number.isFinite(Number(vehicle.heading));
  const heading = hasHeading ? ((Number(vehicle.heading) % 360) + 360) % 360 : null;
  // La imagen base mira hacia la izquierda: se invierte cuando el rumbo tiene componente este.
  const facing = hasHeading && heading <= 180 ? -1 : 1;
  const direction = hasHeading ? "<span class='truck-direction' aria-hidden='true'></span>" : "";
  const orientation = hasHeading ? " style='--truck-heading:" + heading + "deg;--truck-facing:" + facing + "'" : "";
  const alert = vehicle.en_alerta ? " is-alert" : "";
  return L.divIcon({
    className: "truck-div-icon",
    html: "<span class='truck-marker " + status.className + alert + "'" + orientation + ">" + direction + TRUCK_MARKUP + "</span>",
    iconSize: [56, 38], iconAnchor: [28, 19], popupAnchor: [0, -21]
  });
}

function truckAgeText(vehicle) {
  const seconds = (Date.now() - new Date(vehicle.timestamp).getTime()) / 1000;
  return Number.isFinite(seconds) ? formatAge(seconds) : "sin dato";
}

function truckPopup(vehicle) {
  const status = vehicleStatus(vehicle);
  const wrapper = el("div", "truck-popup");
  const state = el("b", status.className, vehicle.en_alerta ? "En alerta · " + status.label : status.label);
  if (vehicle.en_alerta) state.classList.add("alert");
  const speed = vehicle.speed_kmh === null || vehicle.speed_kmh === undefined ? "Sin velocidad" : format(vehicle.speed_kmh, 0) + " km/h";
  const link = el("a", "truck-popup-link", "Ver su recorrido de hoy");
  link.href = "#camiones/" + encodeURIComponent(vehicle.vehicle_id);
  wrapper.append(
    el("strong", "", vehicle.vehicle_name || vehicle.vehicle_id), state,
    el("p", "", speed + " · " + (vehicle.event || "Sin evento")),
    el("small", "", "Patente: " + vehicle.vehicle_id),
    el("small", "", "Conductor: " + vehicleDriver(vehicle)),
    el("small", "", "Último dato: " + formatLocal(vehicle.timestamp) + " (" + truckAgeText(vehicle) + ")"),
    link);
  return wrapper;
}

// --- Capa de uso: las rutas de la semana como líneas (canvas) ---
function vgPct(count) {
  return count / Math.max(1, vg.data.camiones) * 100;
}

function vgBin(pct) {
  return VG_BINS.findIndex((bin) => pct < bin.hasta);
}

// Cada arista une dos puntos por donde pasó la flota; toma el mayor uso de sus extremos para
// que una carretera no se corte por el ruido de los reportes. Se dibuja de menor a mayor uso.
function vgDrawUsage() {
  usageLayer.clearLayers();
  if (!vg.data) return;
  const nodes = vg.data.nodos, edges = vg.data.aristas;
  const groups = VG_BINS.map(() => []);
  for (let k = 0; k < edges.length; k += 2) {
    const a = nodes[edges[k]], b = nodes[edges[k + 1]];
    groups[vgBin(vgPct(Math.max(a[2], b[2])))].push([[a[0], a[1]], [b[0], b[1]]]);
  }
  const onlyRare = vg.zone === "rare";
  groups.forEach((segments, k) => {
    if (!segments.length) return;
    const rare = k === 0;
    const muted = onlyRare && !rare;
    L.polyline(segments, {renderer: usageRenderer, interactive: false, lineCap: "round", lineJoin: "round",
      color: muted ? "#9aa8a5" : VG_BINS[k].color, weight: muted ? 2 : (onlyRare && rare ? 4 : VG_BINS[k].weight),
      opacity: muted ? 0.45 : (rare ? 0.8 : 0.92)}).addTo(usageLayer);
  });
}

function vgIndexCells(data) {
  vg.cells = new Map();
  for (const [lat, lon, count] of data.nodos) {
    vg.cells.set(Math.floor(lat * data.escala) + "|" + Math.floor(lon * data.escala), count);
  }
}

// Al tocar: la celda y sus vecinas (~1,5 km), igual que el servidor.
function vgPctAt(lat, lng) {
  const scale = vg.data.escala;
  const i = Math.floor(lat * scale), j = Math.floor(lng * scale);
  let count = 0;
  for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) count = Math.max(count, vg.cells.get((i + di) + "|" + (j + dj)) || 0);
  return count;
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
  fleetMap = ArtisaMapaBase.crear(container, {pedir: mapPedir, centro: [-24.5, -70], zoom: 6}).mapa;
  for (const [pane, zIndex] of [["usagePane", 380], ["trailPane", 600], ["selectionPane", 620], ["truckPane", 650]]) {
    fleetMap.createPane(pane).style.zIndex = String(zIndex);
  }
  fleetMap.getPane("usagePane").style.pointerEvents = "none";
  usageRenderer = L.canvas({pane: "usagePane", padding: 0.3});
  usageLayer = L.layerGroup();
  trailLayer = L.layerGroup();
  truckLayer = L.layerGroup();
  selectionLayer = L.featureGroup().addTo(fleetMap);
  fleetMap.on("click", (event) => vgInspect(event.latlng));
  // De lejos los camiones se ven como puntos para no tapar el mapa.
  const compact = () => container.classList.toggle("is-far", fleetMap.getZoom() < 9);
  fleetMap.on("zoomend", compact);
  compact();
  vgApplyLayers();
}

function vgApplyLayers() {
  if (!fleetMap) return;
  const show = (layer, visible) => {
    if (visible && !fleetMap.hasLayer(layer)) fleetMap.addLayer(layer);
    if (!visible && fleetMap.hasLayer(layer)) fleetMap.removeLayer(layer);
  };
  show(usageLayer, vg.layers.usage && Boolean(vg.data));
  show(trailLayer, vg.layers.trails);
  show(truckLayer, vg.layers.vehicles);
  for (const input of document.querySelectorAll("[data-map-layer]")) input.checked = vg.layers[input.dataset.mapLayer];
  for (const button of document.querySelectorAll("[data-zone-filter]")) {
    const active = button.dataset.zoneFilter === vg.zone;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
    button.disabled = !vg.layers.usage;
  }
}

// Al tocar el mapa: qué % de la flota pasó por ese lugar.
function vgInspect(latlng) {
  if (!vg.data || !vg.layers.usage) return;
  const count = vgPctAt(latlng.lat, latlng.lng);
  const pct = vgPct(count);
  const text = count
    ? `${format(pct, pct < 10 ? 1 : 0)} % de la flota pasó por aquí en 7 días (${format(count)} de ${format(vg.data.camiones)} camiones)` +
      (pct < vg.data.umbral_pct ? " · zona poco habitual" : "")
    : "Ningún camión pasó por aquí en los últimos 7 días";
  L.popup({className: "vg-inspect"}).setLatLng(latlng).setContent(el("span", "", text)).openOn(fleetMap);
}

// --- Carga ---
async function loadVigilance(quiet = true) {
  if (!token() || vg.loading) return;
  vg.loading = true;
  const version = sessionVersion;
  try {
    const data = await api("/api/vigilancia", {timeoutMs: 90000});
    if (version !== sessionVersion) return;
    renderVigilance(data);
  } catch (error) {
    if (version !== sessionVersion) return;
    $("heatmap-source-status").textContent = "Sin conexión";
    $("heatmap-source-status").className = "status warning";
    $("map-last-update").textContent = "No se pudo actualizar (" + error.message + "). Se muestran los últimos datos y se reintenta cada minuto.";
    if (!vg.data && !quiet) $("alert-list").replaceChildren(el("li", "monitor-empty", "No se pudo cargar: " + error.message));
  } finally {
    if (version === sessionVersion) vg.loading = false;
  }
}

function renderVigilance(data) {
  initializeFleetMap();
  if (data.status !== "ok") {
    vg.data = null;
    vg.alerts = new Map();
    vg.vehicles = [];
    $("heatmap-source-status").textContent = "Sin datos en 7 días";
    $("heatmap-source-status").className = "status neutral";
    for (const id of ["vg-window", "vg-trucks", "vg-moving", "vg-last", "vg-alerts"]) $(id).textContent = "—";
    $("map-last-update").textContent = data.message || "Sin reportes de camiones en los últimos 7 días.";
    $("alert-list").replaceChildren(el("li", "monitor-empty", data.message || "Sin reportes de camiones en los últimos 7 días."));
    $("vehicle-live-list").replaceChildren();
    if (fleetMap) { trailLayer.clearLayers(); truckLayer.clearLayers(); truckMarkers.clear(); vgApplyLayers(); }
    return;
  }
  vg.data = data;
  VG_BINS[0].hasta = data.umbral_pct;
  vgIndexCells(data);
  vg.alerts = new Map(data.alertas.map((alert) => [alert.patente + "|" + alert.inicio, alert]));
  vg.vehicles = data.vehiculos;
  vgRenderScale(data);
  vgRenderStats(data);
  if (fleetMap) {
    vgDrawUsage();
    vgRenderTrails();
    vgRenderTrucks();
    vgApplyLayers();
  }
  vgRenderLists();
  vgRestoreSelection();
  $("heatmap-method").textContent =
    `Las líneas son los caminos por donde anduvo la flota en los últimos 7 días, hasta este minuto (entre dos reportes seguidos ` +
    `de 5 min o menos; los saltos de GPS se descartan). El color y el grosor dicen qué % de los ${format(data.camiones)} camiones con ` +
    `reportes pasó por ese lugar, contando cuadros de ~500 m y sus vecinos: verde y grueso es la ruta habitual, rojo y fino es poco habitual. ` +
    `Hay alerta cuando un camión en movimiento, fuera de las faenas, suma ${format(data.reportes_alerta)} reportes o más por lugares donde pasó ` +
    `menos del ${format(data.umbral_pct)} % del resto de la flota (se mira el cuadro y sus vecinos, ~1,5 km). Termina cuando vuelve a una zona ` +
    `habitual o entra a una faena; si deja de reportar por ${format(30)} min queda "sin señal". Es un comportamiento inusual para revisar, ` +
    "no una falta comprobada. Si no llegan datos nuevos, la ventana igual termina en la hora actual: lo antiguo va saliendo y lo último recibido se mantiene.";
  if (!vg.fitted && $("heatmap-map").offsetParent !== null) requestAnimationFrame(() => vgFit(false));
}

function vgRenderScale(data) {
  const edges = [0, data.umbral_pct, 15, 30, 50];
  $("vg-scale").replaceChildren(...VG_BINS.map((bin, k) => {
    const item = el("span", "vg-bin" + (k === 0 ? " is-rare" : ""));
    const swatch = el("i");
    swatch.style.background = bin.color;
    swatch.style.height = bin.weight + "px";
    item.append(swatch, el("small", "", k === 0 ? `< ${format(data.umbral_pct)} % · poco habitual`
      : k === VG_BINS.length - 1 ? `${format(edges[k])} % o más` : `${format(edges[k])}–${format(VG_BINS[k].hasta)} %`));
    return item;
  }));
}

function vgRenderStats(data) {
  const desde = new Date(data.desde), hasta = new Date(data.hasta);
  const short = (date) => new Intl.DateTimeFormat("es-CL", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    timeZone: HISTORY_ZONE}).format(date);
  $("vg-window").textContent = `${short(desde)} → ahora`;
  $("vg-window-d").textContent = `Hasta ${short(hasta)} · se actualiza cada minuto`;
  $("vg-trucks").textContent = `${format(data.camiones)} de ${format(data.catalogo)}`;
  $("vg-trucks-d").textContent = `${format(data.reportes)} reportes en 7 días`;
  vgRenderLive();
  const summary = data.resumen;
  $("vg-alerts").textContent = format(summary.activas);
  $("vg-alerts-card").classList.toggle("has-alerts", summary.activas > 0);
  $("vg-alerts-d").textContent = `${format(summary.sin_senal)} sin señal · ${format(summary.terminadas)} terminadas en 7 días`;
  $("heatmap-source-status").textContent = summary.activas
    ? `${format(summary.activas)} ${summary.activas === 1 ? "alerta activa" : "alertas activas"}` : "En vivo · sin alertas activas";
  $("heatmap-source-status").className = "status " + (summary.activas ? "warning" : "info");
}

// Lo que cambia cada minuto con las posiciones: en movimiento, último dato, pie del mapa.
function vgRenderLive() {
  if (!vg.data) return;
  const recent = vg.vehicles.filter((v) => Date.now() - new Date(v.timestamp).getTime() <= 15 * 60 * 1000);
  $("vg-moving").textContent = format(recent.filter((v) => Number(v.speed_kmh || 0) > 3).length);
  $("vg-moving-d").textContent = `${format(recent.length)} con dato de los últimos 15 min`;
  const last = vg.vehicles.reduce((top, v) => (!top || v.timestamp > top ? v.timestamp : top), null);
  $("vg-last").textContent = last ? formatAge((Date.now() - new Date(last).getTime()) / 1000) : "—";
  $("vg-last-d").textContent = last ? formatLocal(last) + " (hora de Chile)" : "Sin reportes";
  const stale = last && Date.now() - new Date(last).getTime() > 15 * 60 * 1000;
  $("vg-last").closest(".atlas-stat").classList.toggle("is-stale", Boolean(stale));
  $("map-last-update").textContent = (stale
    ? "No están llegando datos nuevos: se muestra lo último recibido y la ventana sigue avanzando con la hora actual"
    : "Uso calculado con los últimos 7 días") + " · actualizado " + new Intl.DateTimeFormat("es-CL", {
    hour: "2-digit", minute: "2-digit", timeZone: HISTORY_ZONE}).format(new Date());
}

function vgRenderTrails() {
  trailLayer.clearLayers();
  for (const [id, alert] of vg.alerts) {
    if (alert.estado === "terminada") continue;
    const line = L.polyline(alert.estela, {pane: "trailPane", color: alert.estado === "activa" ? "#d7301f" : "#8a6a64",
      weight: 3, opacity: 0.85, dashArray: "6 6", lineCap: "round"});
    line.bindTooltip(el("span", "", `${alert.patente} · ${VG_STATES[alert.estado].label} · desde ${vgTime(alert.inicio)}`), {sticky: true});
    line.on("click", (event) => { L.DomEvent.stopPropagation(event); vgSelect({kind: "alert", id}); });
    line.addTo(trailLayer);
  }
}

function vgRenderTrucks() {
  truckLayer.clearLayers();
  truckMarkers.clear();
  for (const vehicle of vg.vehicles) {
    if (!Number.isFinite(Number(vehicle.latitude)) || !Number.isFinite(Number(vehicle.longitude))) continue;
    const marker = L.marker([vehicle.latitude, vehicle.longitude], {pane: "truckPane", icon: truckIcon(vehicle),
      zIndexOffset: vehicle.en_alerta ? 2000 : 1000}).bindPopup(truckPopup(vehicle));
    marker.on("click", () => vgSelect({kind: "vehicle", id: vehicle.vehicle_id}, false));
    marker.on("popupopen", () => { vg.openPopup = vehicle.vehicle_id; });
    marker.on("popupclose", () => { if (vg.openPopup === vehicle.vehicle_id) vg.openPopup = null; });
    marker.addTo(truckLayer);
    truckMarkers.set(vehicle.vehicle_id, marker);
  }
  if (vg.openPopup && truckMarkers.has(vg.openPopup)) truckMarkers.get(vg.openPopup).openPopup();
}

// --- Listas ---
function vgRenderLists() {
  vgRenderAlertList();
  vgRenderVehicleList();
}

function vgMatches(text) {
  const query = $("monitor-search").value.trim().toLocaleLowerCase("es");
  return !query || text.toLocaleLowerCase("es").includes(query);
}

function vgRenderAlertList() {
  const list = $("alert-list");
  const alerts = [...vg.alerts.entries()];
  $("alert-count").textContent = format(alerts.filter(([, a]) => a.estado === "activa").length);
  for (const button of document.querySelectorAll("[data-alert-filter]")) {
    const state = button.dataset.alertFilter;
    const count = state === "all" ? alerts.length : alerts.filter(([, a]) => a.estado === state).length;
    button.textContent = `${{all: "Todas", activa: "Activas", sin_senal: "Sin señal", terminada: "Terminadas"}[state]} ${format(count)}`;
    button.classList.toggle("is-active", vg.alertFilter === state);
  }
  const visible = alerts.filter(([, a]) => (vg.alertFilter === "all" || a.estado === vg.alertFilter) &&
    vgMatches(`${a.patente} ${a.nombre || ""} ${a.conductor || ""} ${a.ubicacion || ""}`));
  list.replaceChildren(...visible.map(([id, alert]) => {
    const state = VG_STATES[alert.estado];
    const item = el("li");
    const button = el("button", "vg-alert " + state.className);
    button.type = "button";
    button.dataset.alertSelect = id;
    const head = el("span", "vg-alert-head");
    head.append(el("b", "vg-state " + state.className, state.label),
      el("small", "", `desde ${vgTime(alert.inicio)} · ${vgDuration(alert.minutos)}`));
    const who = el("span", "vg-alert-who");
    who.append(el("span", "plate-chip", alert.patente), el("strong", "", alert.nombre || "Sin nombre"));
    const driver = el("span", "vg-alert-driver", "Conductor: " + (alert.conductor || "no informado"));
    const facts = el("span", "vg-alert-facts", (alert.km_al_corredor === null ? "a más de 130 km" : `a ${format(alert.km_al_corredor, 1)} km`) +
      ` del corredor habitual · zona usada por ${format(alert.pct_zona, 1)} % de la flota`);
    button.append(head, who, driver, facts);
    if (alert.ubicacion) button.append(el("span", "vg-alert-place", "Ubicación: " + alert.ubicacion));
    button.addEventListener("click", () => vgSelect({kind: "alert", id}));
    item.append(button);
    return item;
  }));
  if (!visible.length) {
    list.append(el("li", "monitor-empty", alerts.length ? "No hay alertas con este filtro o búsqueda."
      : "Sin alertas en los últimos 7 días: todos los camiones anduvieron por zonas habituales de la flota."));
  }
  vgMarkSelection();
}

function vgRenderVehicleList() {
  const list = $("vehicle-live-list");
  $("vehicle-live-count").textContent = format(vg.vehicles.length);
  const ordered = vg.vehicles
    .filter((v) => vgMatches(`${v.vehicle_id} ${v.vehicle_name || ""} ${vehicleDriver(v)}`))
    .sort((a, b) => (b.en_alerta - a.en_alerta) || Number(b.speed_kmh || 0) - Number(a.speed_kmh || 0));
  list.replaceChildren(...ordered.map((vehicle) => {
    const status = vehicleStatus(vehicle);
    const item = el("li");
    const button = el("button", "vehicle-live-row " + status.className + (vehicle.en_alerta ? " is-alert" : ""));
    button.type = "button";
    button.dataset.vehicleSelect = vehicle.vehicle_id;
    const icon = el("span", "vehicle-list-icon");
    icon.innerHTML = TRUCK_MARKUP;
    const copy = el("span");
    const identity = el("small", "fleet-identity");
    const driver = vehicleDriver(vehicle);
    identity.append(...truckIdentity(vehicle.vehicle_id, driver === "No informado" ? null : driver, null));
    copy.append(el("strong", "", vehicle.vehicle_name || vehicle.vehicle_id), identity);
    const state = el("em", "vehicle-live-state", vehicle.en_alerta ? "En alerta"
      : status.className === "moving" && Number(vehicle.speed_kmh) > 3 ? format(vehicle.speed_kmh, 0) + " km/h" : truckAgeText(vehicle));
    button.append(icon, copy, state);
    button.addEventListener("click", () => vgSelect({kind: "vehicle", id: vehicle.vehicle_id}));
    item.append(button);
    return item;
  }));
  if (!ordered.length) list.append(el("li", "monitor-empty", "No hay camiones para esta búsqueda."));
  vgMarkSelection();
}

function setMonitorTab(tab) {
  vg.tab = tab === "vehicles" ? "vehicles" : "alerts";
  for (const button of document.querySelectorAll("[data-monitor-tab]")) {
    const selected = button.dataset.monitorTab === vg.tab;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-selected", String(selected));
  }
  for (const list of document.querySelectorAll("[data-monitor-list]")) list.hidden = list.dataset.monitorList !== vg.tab;
  $("vg-alert-filter").hidden = vg.tab !== "alerts";
  vgRenderLists();
}

// --- Selección ---
function vgSelect(selection, focus = true) {
  vg.selected = selection;
  selectionLayer.clearLayers();
  if (selection.kind === "alert") {
    const alert = vg.alerts.get(selection.id);
    if (!alert) return vgReset(false);
    vgShowAlert(alert, focus);
  } else {
    const vehicle = vg.vehicles.find((v) => v.vehicle_id === selection.id);
    if (!vehicle) return vgReset(false);
    vgShowVehicle(vehicle, focus);
  }
  vgMarkSelection();
}

function vgShowAlert(alert, focus) {
  const state = VG_STATES[alert.estado];
  L.polyline(alert.estela, {pane: "selectionPane", color: "#0b1f1a", weight: 9, opacity: 0.75, lineCap: "round", interactive: false}).addTo(selectionLayer);
  L.polyline(alert.estela, {pane: "selectionPane", color: "#d7301f", weight: 5, opacity: 1, lineCap: "round", interactive: false}).addTo(selectionLayer);
  L.circleMarker(alert.estela[0], {pane: "selectionPane", radius: 7, color: "#0b1f1a", weight: 3, fillColor: "#fff", fillOpacity: 1})
    .bindTooltip("Sale de la zona habitual · " + vgTime(alert.inicio), {permanent: true, direction: "top", className: "route-endpoint"})
    .addTo(selectionLayer);
  if (alert.corredor) {
    // Cuánto se alejó: línea desde su última posición hasta el corredor habitual más cercano.
    L.polyline([alert.posicion, alert.corredor.punto], {pane: "selectionPane", color: "#0b1f1a", weight: 2,
      dashArray: "4 7", opacity: 0.9, interactive: false}).addTo(selectionLayer);
    L.circleMarker(alert.corredor.punto, {pane: "selectionPane", radius: 7, color: "#fff", weight: 3, fillColor: "#1f6b50", fillOpacity: 1})
      .bindTooltip(`Corredor habitual · ${format(alert.corredor.km, 1)} km`, {permanent: true, direction: "left", className: "route-endpoint"})
      .addTo(selectionLayer);
  }
  L.circleMarker(alert.posicion, {pane: "selectionPane", radius: 9, color: "#fff", weight: 3, fillColor: "#d7301f", fillOpacity: 1})
    .bindTooltip((alert.estado === "terminada" ? "Último punto fuera · " : "Último dato · ") + vgTime(alert.fin),
      {permanent: true, direction: "bottom", className: "route-endpoint"})
    .addTo(selectionLayer);
  const detail = $("map-selection-detail");
  setSelectionDetail("Alerta · " + state.label, `${alert.patente} · ${alert.nombre || "Sin nombre"}`,
    alert.estado === "activa" ? "Anda por zonas que casi nadie de la flota usa. Revisa qué está haciendo."
      : alert.estado === "sin_senal" ? "Dejó de reportar mientras estaba fuera de las zonas habituales."
        : "Volvió a una zona habitual o entró a una faena.");
  const facts = el("dl", "vg-facts");
  const fact = (label, value) => { facts.append(el("dt", "", label), el("dd", "", value)); };
  fact("Patente", alert.patente);
  fact("Camión", alert.nombre || "Sin nombre");
  fact("Conductor", alert.conductor || "No informado");
  fact("Desde", vgTime(alert.inicio));
  fact(alert.estado === "terminada" ? "Hasta" : "Último dato", vgTime(alert.fin));
  fact("Duración", vgDuration(alert.minutos));
  fact("Distancia al corredor", alert.km_al_corredor === null ? "Más de 130 km" : format(alert.km_al_corredor, 1) + " km");
  fact("Km fuera del corredor", format(alert.km_recorridos, 1) + " km");
  fact("Uso de la zona", format(alert.pct_zona, 1) + " % de la flota");
  fact("Reportes", `${format(alert.reportes)} (${format(alert.reportes_en_movimiento)} en movimiento)`);
  if (alert.velocidad !== null && alert.velocidad !== undefined) fact("Velocidad", format(alert.velocidad, 0) + " km/h");
  if (alert.ubicacion) fact("Ubicación GPS", alert.ubicacion);
  fact("Coordenadas", alert.posicion.map((value) => value.toFixed(5)).join(", "));
  const link = el("a", "selection-link", "Ver su día completo →");
  link.href = `#camiones/${encodeURIComponent(alert.patente)}/${vgLocalDay(alert.fin)}`;
  detail.append(facts, link);
  if (focus) {
    const bounds = L.latLngBounds(alert.estela);
    if (alert.corredor) bounds.extend(alert.corredor.punto);
    fleetMap.fitBounds(bounds.pad(0.2), {maxZoom: 13});
  }
}

function vgShowVehicle(vehicle, focus) {
  const status = vehicleStatus(vehicle);
  const speed = vehicle.speed_kmh === null || vehicle.speed_kmh === undefined ? "Velocidad sin dato" : format(vehicle.speed_kmh, 0) + " km/h";
  setSelectionDetail("Camión · " + (vehicle.en_alerta ? "en alerta" : status.label), vehicle.vehicle_name || vehicle.vehicle_id,
    `${speed} · ${vehicle.event || "Sin evento"}`,
    `Patente ${vehicle.vehicle_id} · conductor ${vehicleDriver(vehicle)} · último dato ${formatLocal(vehicle.timestamp)} (${truckAgeText(vehicle)})`);
  const link = el("a", "selection-link", "Ver su recorrido de hoy →");
  link.href = "#camiones/" + encodeURIComponent(vehicle.vehicle_id);
  $("map-selection-detail").append(link);
  const marker = truckMarkers.get(vehicle.vehicle_id);
  if (marker && focus) {
    if (!vg.layers.vehicles) { vg.layers.vehicles = true; vgApplyLayers(); }
    fleetMap.setView(marker.getLatLng(), Math.max(fleetMap.getZoom(), 11));
    marker.openPopup();
  }
}

function vgMarkSelection() {
  const selected = vg.selected || {};
  for (const button of document.querySelectorAll("[data-alert-select]")) {
    button.classList.toggle("is-active", selected.kind === "alert" && button.dataset.alertSelect === selected.id);
  }
  for (const button of document.querySelectorAll("[data-vehicle-select]")) {
    button.classList.toggle("is-active", selected.kind === "vehicle" && button.dataset.vehicleSelect === selected.id);
  }
  for (const [vehicleId, marker] of truckMarkers) {
    marker.getElement()?.classList.toggle("is-selected", selected.kind === "vehicle" && vehicleId === selected.id);
  }
}

function vgRestoreSelection() {
  if (!vg.selected) return;
  const exists = vg.selected.kind === "alert" ? vg.alerts.has(vg.selected.id)
    : vg.vehicles.some((v) => v.vehicle_id === vg.selected.id);
  if (exists) vgSelect(vg.selected, false); else vgReset(false);
}

function vgReset(fit = true) {
  vg.selected = null;
  if (selectionLayer) selectionLayer.clearLayers();
  if (fleetMap) fleetMap.closePopup();
  setSelectionDetail("Selección", "Elige una alerta o un camión",
    "El mapa lo centra y muestra sus datos. Toca el mapa para ver qué % de la flota pasó por ese lugar.");
  vgMarkSelection();
  if (fit) vgFit(true);
}

function vgFit(force) {
  if (!fleetMap || !vg.data || (vg.fitted && !force) || vg.selected) return;
  fleetMap.invalidateSize();
  const bounds = L.latLngBounds([]);
  for (const v of vg.vehicles) if (Number.isFinite(Number(v.latitude))) bounds.extend([v.latitude, v.longitude]);
  for (const [lat, lon] of vg.data.nodos) bounds.extend([lat, lon]);
  if (bounds.isValid()) { fleetMap.fitBounds(bounds, {padding: [28, 28], maxZoom: 11}); vg.fitted = true; }
}

// Cada minuto llegan posiciones nuevas (/api/vehiculos/posiciones): se mueven los camiones sin
// recalcular la semana; la semana y las alertas se recalculan con /api/vigilancia.
function vgMergePositions(incoming) {
  const fresh = new Map(incoming.map((v) => [v.vehicle_id, v]));
  vg.vehicles = vg.vehicles.map((vehicle) => {
    const update = fresh.get(vehicle.vehicle_id);
    if (!update || Date.parse(update.timestamp) <= Date.parse(vehicle.timestamp)) return vehicle;
    return {...vehicle, ...update, en_alerta: vehicle.en_alerta,
      driver_name: update.driver_name || vehicle.driver_name,
      driver_last_name: update.driver_name ? update.driver_last_name : vehicle.driver_last_name};
  });
  if (fleetMap) vgRenderTrucks();
  vgRenderVehicleList();
  vgRenderLive();
}

// Los cuatro mapas comparten fondo, selector de capas, faenas y densidad (mapa-base.js).
const mapPedir = (path, timeoutMs) => api(path, {timeoutMs});

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

// --- Dashboard: dos pestañas --------------------------------------------------------------
// Datos globales: rutas clasificadas y estado de la flota (de /api/mapa).
// Datos detallados: reportes y km por patente del cierre diario (/api/dashboard), de un mes
// (día a día, con /api/reportes/mes-completo) o de todos los meses.
const DASH_METRICS = {
  reportes: {title: "Reportes", unit: "reportes", decimals: 0},
  km: {title: "Kilómetros observados", unit: "km", decimals: 0}
};
var dashState = {tab: "global", data: null, metric: "reportes", period: null, sort: "total", query: "",
  open: null, days: {}, months: {}};

const dashMonthLabel = (month, style = "short") => new Intl.DateTimeFormat("es-CL", style === "short"
  ? {month: "short", timeZone: "UTC"} : {month: "long", year: "numeric", timeZone: "UTC"})
  .format(new Date(month + "-15T12:00:00Z")).replace(".", "");
const dashDayLabel = (iso) => new Intl.DateTimeFormat("es-CL", {
  weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC"}).format(new Date(iso + "T12:00:00Z"));
const dashHeat = (value, max) => value ? (0.12 + 0.88 * Math.sqrt(value / max)).toFixed(3) : "0";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function dashSilence(days) {
  if (days === null || days === undefined) return "Nunca reportó";
  if (days <= 0) return "Hoy";
  return days === 1 ? "Ayer" : `Hace ${format(days)} días`;
}

function setDashTab(tab) {
  dashState.tab = tab;
  for (const button of document.querySelectorAll("[data-dash-tab]")) {
    const active = button.dataset.dashTab === tab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  }
  $("dash-global").hidden = tab !== "global";
  $("dash-detail").hidden = tab !== "detalle";
  renderDashSnapshot();
}

function renderDashSnapshot() {
  const status = $("dashboard-snapshot");
  if (dashState.tab === "detalle") {
    const data = dashState.data;
    status.textContent = data ? `Desde ${dashDayLabel(data.desde)} · ${format(data.meses.length)} meses` : "Pendiente";
    status.className = "status " + (data ? "info" : "neutral");
  } else {
    status.textContent = dashState.globalLabel || "Preparando indicadores…";
    status.className = "status " + (dashState.globalLabel ? "info" : "neutral");
  }
}

// --- Datos globales ---
function routeCenter(route) {
  const coordinates = route && route.geometry && route.geometry.type === "LineString" ? route.geometry.coordinates : [];
  let latitude = 0, longitude = 0, count = 0;
  for (const coordinate of coordinates) {
    const lon = Number(coordinate && coordinate[0]);
    const lat = Number(coordinate && coordinate[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    latitude += lat; longitude += lon; count += 1;
  }
  return count ? {latitude: latitude / count, longitude: longitude / count} : null;
}

// Zonas relativas: cada ruta cae en norte, sur, este, oeste o centro según su punto central
// respecto del centro del conjunto de rutas (no son regiones administrativas).
function routeZones(routes) {
  const centers = routes.map((route) => [route, routeCenter(route)]).filter(([, point]) => point);
  const zones = new Map(["Zona Norte", "Zona Centro", "Zona Sur", "Zona Oeste", "Zona Este"]
    .map((zone) => [zone, {label: zone, value: 0, routes: 0, vehicles: 0}]));
  if (!centers.length) return [];
  const lats = centers.map(([, p]) => p.latitude), lons = centers.map(([, p]) => p.longitude);
  const latMid = (Math.min(...lats) + Math.max(...lats)) / 2, lonMid = (Math.min(...lons) + Math.max(...lons)) / 2;
  const latR = Math.max((Math.max(...lats) - Math.min(...lats)) / 2, 0.0001);
  const lonR = Math.max((Math.max(...lons) - Math.min(...lons)) / 2, 0.0001);
  for (const [route, point] of centers) {
    const north = (point.latitude - latMid) / latR, east = (point.longitude - lonMid) / lonR;
    const name = Math.abs(north) <= 0.26 && Math.abs(east) <= 0.26 ? "Zona Centro"
      : Math.abs(north) >= Math.abs(east) ? (north >= 0 ? "Zona Norte" : "Zona Sur") : (east < 0 ? "Zona Oeste" : "Zona Este");
    const zone = zones.get(name);
    const properties = routeProperties(route);
    zone.value += Number(properties.usage_count || 0);
    zone.routes += 1;
    zone.vehicles += Number(properties.unique_vehicle_count || 0);
  }
  return [...zones.values()].sort((a, b) => b.value - a.value);
}

function renderDonut(containerId, items, centerLabel) {
  const container = $(containerId);
  const total = items.reduce((sum, item) => sum + item.value, 0);
  let angle = 0;
  const segments = items.map((item) => {
    const next = total ? angle + item.value / total * 360 : angle;
    const part = `${item.color} ${angle}deg ${next}deg`;
    angle = next;
    return part;
  });
  const chart = el("div", "fd-donut");
  chart.style.background = total ? `conic-gradient(${segments.join(",")})` : "#e6ece8";
  const center = el("div");
  center.append(el("strong", "", format(total)), el("span", "", centerLabel));
  chart.append(center);
  const legend = el("ul", "fd-donut-legend");
  for (const item of items) {
    const row = el("li");
    const swatch = el("i");
    swatch.style.backgroundColor = item.color;
    const share = total ? Math.round(item.value / total * 100) : 0;
    const name = el("span", "", item.label);
    const value = el("strong", "", format(item.value));
    const pct = el("small", "", share + " %");
    const bar = el("b");
    bar.style.width = share + "%";
    bar.style.backgroundColor = item.color;
    row.append(swatch, name, value, pct, bar);
    legend.append(row);
  }
  container.replaceChildren(chart, legend);
}

function renderDashboardGlobal(data) {
  const ids = ["dg-reports", "dg-trips", "dg-routes", "dg-signal"];
  if (machinePending(data)) {
    dashState.globalLabel = "Pendiente de Machine";
    for (const id of ids) $(id).textContent = "—";
    for (const id of ["dg-zones", "dg-levels", "dg-fleet", "dg-ranking"]) {
      $(id).replaceChildren(el(id === "dg-ranking" ? "li" : "span", "chart-empty", "Pendiente de procesamiento Machine."));
    }
    $("dg-top-zone").textContent = "—";
    renderDashSnapshot();
    return;
  }
  const summary = data.summary || {};
  const levels = {HIGH: 0, MEDIUM: 0, LOW: 0};
  for (const route of data.routes) {
    const level = routeProperties(route).usage_level;
    if (level in levels) levels[level] += 1;
  }
  const routeCount = data.routes.length;
  $("dg-reports").textContent = format(summary.available_report_count || 0);
  $("dg-trips").textContent = format(summary.trip_count || data.trips.length || 0);
  const window_ = data.metadata.window || {};
  $("dg-trips-d").textContent = window_.desde
    ? `Entre el ${formatDataDay(window_.desde)} y el ${formatDataDay(window_.hasta)}` : "Dentro de la ventana de la clasificación";
  $("dg-routes").textContent = format(routeCount);
  for (const [level, id] of [["HIGH", "dg-bar-high"], ["MEDIUM", "dg-bar-medium"], ["LOW", "dg-bar-low"]]) {
    $(id).style.flexGrow = String(levels[level] || 0);
    $(id).title = `${ROUTE_LABELS[level] || level}: ${format(levels[level])}`;
  }
  const fleet = {moving: 0, off: 0};
  for (const vehicle of data.vehicles) fleet[vehicleStatus(vehicle).className] += 1;
  $("dg-signal").textContent = `${format(fleet.moving)} de ${format(data.vehicles.length)}`;
  $("dg-signal-d").textContent = `${format(fleet.off)} sin señal reciente o apagados`;
  const coverage = data.metadata.metricas?.cobertura;
  dashState.globalLabel = Number(coverage?.dias_pendientes || 0) > 0
    ? `Histórico en procesamiento · ${coverage.dias_cerrados}/${coverage.dias_ventana} días`
    : "Corte " + formatDataDay(data.snapshotDate);

  // Zonas
  const zones = routeZones(data.routes);
  const totalZones = zones.reduce((sum, zone) => sum + zone.value, 0);
  const maxZone = Math.max(1, ...zones.map((zone) => zone.value));
  $("dg-top-zone").textContent = zones[0] && zones[0].value ? "Mayor uso: " + zones[0].label : "—";
  $("dg-zones").replaceChildren(...zones.map((zone, k) => {
    const row = el("div", "fd-zone" + (k === 0 ? " is-top" : ""));
    const share = totalZones ? zone.value / totalZones * 100 : 0;
    const head = el("div", "fd-zone-head");
    head.append(el("span", "", zone.label), el("strong", "", format(zone.value)), el("small", "", format(share, 1) + " %"));
    const track = el("div", "fd-zone-track");
    const fill = el("i");
    fill.style.width = (zone.value ? Math.max(2, zone.value / maxZone * 100) : 0) + "%";
    track.append(fill);
    row.append(head, track, el("small", "fd-zone-detail",
      `${format(zone.routes)} rutas · ${format(zone.vehicles)} participaciones de camiones`));
    return row;
  }));
  $("dg-method").textContent = "Cada ruta se cuenta en una zona (norte, centro, sur, oeste o este) según dónde queda su " +
    "punto central respecto del centro de todas las rutas. Son zonas relativas, no regiones. Se actualiza junto con el mapa.";

  renderDonut("dg-levels", [
    {label: "Más usadas", value: levels.HIGH, color: "#14532d"},
    {label: "Uso medio", value: levels.MEDIUM, color: "#f97316"},
    {label: "Poco usadas", value: levels.LOW, color: "#dc2626"}
  ], "rutas");
  renderDonut("dg-fleet", [
    {label: "Con señal", value: fleet.moving, color: "#3f9a5b"},
    {label: "Sin señal o apagados", value: fleet.off, color: "#708083"}
  ], "camiones");

  // Ranking
  const top = [...data.routes]
    .sort((a, b) => Number(routeProperties(b).usage_count || 0) - Number(routeProperties(a).usage_count || 0))
    .slice(0, 8);
  const maxTop = Math.max(1, ...top.map((route) => Number(routeProperties(route).usage_count || 0)));
  $("dg-ranking").replaceChildren(...top.map((route, k) => {
    const properties = routeProperties(route);
    const item = el("li");
    const button = el("div", "fd-rank");
    const head = el("span", "fd-rank-head");
    head.append(el("b", "fd-rank-n", String(k + 1)), el("span", "fd-rank-name", properties.label || properties.route_id),
      el("strong", "", format(properties.usage_count || 0)));
    const track = el("span", "fd-zone-track");
    const fill = el("i");
    fill.style.width = Math.max(2, Number(properties.usage_count || 0) / maxTop * 100) + "%";
    fill.style.background = properties.color;
    track.append(fill);
    button.append(head, track, el("small", "",
      `${properties.route_id} · ${format(properties.unique_vehicle_count || 0)} camiones · ${properties.usage_label || properties.usage_level}`));
    item.append(button);
    return item;
  }));
  renderDashSnapshot();
}

// --- Datos detallados ---
function renderDashboard(data) {
  if (!data || data.status !== "ok") {
    dashState.data = null;
    for (const id of ["dash-k-reportes", "dash-k-km", "dash-k-activos"]) $(id).textContent = "—";
    for (const id of ["dash-k-reportes-d", "dash-k-km-d", "dash-k-activos-d"]) $(id).textContent = "Se calcula con el cierre diario";
    $("dash-months").replaceChildren(el("span", "chart-empty", "Aún no hay días procesados."));
    for (const id of ["dash-table", "dash-silent", "dash-quality", "dash-periods"]) $(id).replaceChildren();
    renderDashSnapshot();
    return;
  }
  dashState.data = data;
  if (!dashState.period || (dashState.period !== "todos" && !data.meses.includes(dashState.period))) {
    dashState.period = data.mes_en_curso;
  }
  dashState.months = {};  // el mes en curso cambió: se vuelve a pedir al mostrarlo
  renderDashSilent(data);
  renderDashQuality(data);
  renderDashSnapshot();
  renderDashView();
}

function setDashPeriod(period) {
  dashState.period = period;
  dashState.open = null;
  renderDashView();
}

function renderDashPeriods(data) {
  const box = $("dash-periods");
  const options = [["todos", "Todos los meses"], ...data.meses.map((month) => [month, dashMonthLabel(month)])];
  box.replaceChildren(...options.map(([value, label]) => {
    const button = el("button", "fd-period" + (value === "todos" ? " is-all" : ""), label);
    button.type = "button";
    const active = dashState.period === value;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
    if (value !== "todos") {
      button.title = dashMonthLabel(value, "long") + (value === data.mes_en_curso ? " · mes en curso" : "");
      if (value === data.mes_en_curso) button.append(el("i", "fd-live"));
    }
    button.addEventListener("click", () => setDashPeriod(value));
    return button;
  }));
}

function renderDashView() {
  const data = dashState.data;
  if (!data) return;
  for (const button of document.querySelectorAll("[data-dash-metric]")) {
    const active = button.dataset.dashMetric === dashState.metric;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  renderDashPeriods(data);
  renderDashSortOptions();
  if (dashState.period === "todos") {
    renderDashAll(data);
    return;
  }
  const month = dashState.months[dashState.period];
  if (!month || month.loading) {
    if (!month) loadDashMonth(dashState.period);
    $("dash-months").replaceChildren(el("span", "chart-empty", "Cargando el mes…"));
    $("dash-table").replaceChildren();
    return;
  }
  if (month.error) {
    $("dash-months").replaceChildren(el("span", "chart-empty", "No se pudo cargar el mes: " + month.error));
    $("dash-table").replaceChildren();
    return;
  }
  renderDashMonth(data, month);
}

async function loadDashMonth(month) {
  dashState.months[month] = {loading: true};
  try {
    const result = await api(`/api/reportes/mes-completo?mes=${encodeURIComponent(month)}`, {timeoutMs: 60000});
    dashState.months[month] = monthModel(month, result.filas || []);
  } catch (error) {
    dashState.months[month] = {error: error.message};
  }
  if (dashState.period === month) renderDashView();
}

// {dias: [iso…], camiones: {patente: {reportes: [...], km: [...]}}} del mes, hasta hoy.
function monthModel(month, rows) {
  const data = dashState.data;
  const [year, number] = month.split("-").map(Number);
  const last = new Date(Date.UTC(year, number, 0)).getUTCDate();
  const days = [];
  for (let d = 1; d <= last; d++) {
    const iso = `${month}-${String(d).padStart(2, "0")}`;
    if (iso > data.hoy) break;
    if (iso >= data.desde) days.push(iso);
  }
  const index = new Map(days.map((iso, k) => [iso, k]));
  const trucks = {};
  for (const truck of data.camiones) {
    trucks[truck.patente] = {reportes: new Array(days.length).fill(0), km: new Array(days.length).fill(0)};
  }
  for (const row of rows) {
    const truck = trucks[row.asset_id];
    const k = index.get(String(row.fecha).slice(0, 10));
    if (!truck || k === undefined) continue;
    truck.reportes[k] = Number(row.reportes || 0);
    truck.km[k] = Math.round(Number(row.km_observados || 0) * 10) / 10;
  }
  return {mes: month, dias: days, camiones: trucks};
}

function periodValues(truck, month) {
  // Valores del camión en el período: por mes (todos) o por día (un mes).
  return month ? month.camiones[truck.patente][dashState.metric] : truck[dashState.metric];
}

const sum = (values) => values.reduce((total, value) => total + value, 0);  // suma de una lista

function renderDashKpis(totalReports, totalKm, active, totalTrucks, label) {
  $("dash-k-reportes").textContent = format(totalReports);
  $("dash-k-reportes-d").textContent = "GPS válidos · " + label;
  $("dash-k-km").textContent = format(totalKm);
  $("dash-k-km-d").textContent = "Km observados · " + label;
  $("dash-k-activos").textContent = `${format(active)} de ${format(totalTrucks)}`;
  $("dash-k-activos-d").textContent = "Con al menos un reporte · " + label;
}

function renderDashAll(data) {
  const metric = DASH_METRICS[dashState.metric];
  renderDashKpis(data.totales.reportes, data.totales.km,
    data.camiones.filter((truck) => truck.total > 0).length, data.totales.camiones, "desde " + dashDayLabel(data.desde));
  $("dash-chart-eyebrow").textContent = "MES A MES";
  $("dash-months-title").textContent = `${metric.title} de la flota por mes`;
  $("dash-months-unit").textContent = "Toca un mes para verlo día a día";
  renderDashBars(data.flota.map((month) => ({
    key: month.mes, label: dashMonthLabel(month.mes), value: month[dashState.metric],
    detail: month.mes === data.mes_en_curso ? "en curso" : `${format(month.camiones_activos)} cam.`,
    current: month.mes === data.mes_en_curso,
    title: `${dashMonthLabel(month.mes, "long")}: ${format(month[dashState.metric])} ${metric.unit} · ` +
      `${format(month.camiones_activos)} camiones con datos`,
    onClick: () => setDashPeriod(month.mes)
  })), false);
  $("dash-table-title").textContent = `${metric.title} por camión y mes`;
  renderDashTable(data.meses.map((month) => ({
    label: dashMonthLabel(month), title: dashMonthLabel(month, "long"), current: month === data.mes_en_curso,
    onClick: () => setDashPeriod(month)
  })), null);
}

function renderDashMonth(data, month) {
  const metric = DASH_METRICS[dashState.metric];
  const label = dashMonthLabel(month.mes, "long");
  const fleet = month.dias.map((_, k) => sum(data.camiones.map((truck) => month.camiones[truck.patente][dashState.metric][k])));
  const reports = sum(data.camiones.map((truck) => sum(month.camiones[truck.patente].reportes)));
  const km = sum(data.camiones.map((truck) => sum(month.camiones[truck.patente].km)));
  const active = data.camiones.filter((truck) => sum(month.camiones[truck.patente].reportes) > 0).length;
  renderDashKpis(reports, km, active, data.camiones.length, label + (month.mes === data.mes_en_curso ? " (hasta hoy)" : ""));
  $("dash-chart-eyebrow").textContent = "DÍA A DÍA";
  $("dash-months-title").textContent = `${metric.title} de la flota en ${label}`;
  $("dash-months-unit").textContent = month.mes === data.mes_en_curso ? "Mes en curso" : `${format(month.dias.length)} días`;
  renderDashBars(month.dias.map((iso, k) => {
    const weekday = new Date(iso + "T12:00:00Z").getUTCDay();
    return {
      key: iso, label: String(Number(iso.slice(8))), value: fleet[k], weekend: weekday === 0 || weekday === 6,
      current: iso === data.hoy, detail: "",
      title: `${dashDayLabel(iso)}: ${format(fleet[k])} ${metric.unit}` + (iso === data.hoy ? " · hoy, en curso" : "")
    };
  }), true);
  $("dash-table-title").textContent = `${metric.title} por camión en ${label}`;
  renderDashTable(null, month);
}

function renderDashBars(items, daily) {
  const chart = $("dash-months");
  chart.classList.toggle("is-daily", daily);
  const max = Math.max(1, ...items.map((item) => item.value));
  chart.replaceChildren(...items.map((item) => {
    const bar = el(item.onClick ? "button" : "div", "fd-month");
    if (item.onClick) { bar.type = "button"; bar.addEventListener("click", item.onClick); }
    bar.classList.toggle("is-current", Boolean(item.current));
    bar.classList.toggle("is-weekend", Boolean(item.weekend));
    bar.title = item.title;
    const track = el("span", "fd-month-track");
    const fill = el("i");
    fill.style.height = (item.value ? Math.max(3, item.value / max * 100) : 0) + "%";
    track.append(fill);
    if (!daily) bar.append(el("b", "", format(item.value)));
    bar.append(track, el("strong", "", item.label));
    if (item.detail) bar.append(el("small", "", item.detail));
    return bar;
  }));
  requestAnimationFrame(() => { chart.scrollLeft = chart.scrollWidth; });  // lo reciente a la vista
}

function renderDashSortOptions() {
  const options = [["total", "Mayor total"], ["menos", "Menor total"], ["silencio", "Más días sin reportar"],
    ["patente", "Patente (A–Z)"]];
  $("dash-sort").replaceChildren(...options.map(([value, label]) => {
    const option = el("option", "", label);
    option.value = value;
    return option;
  }));
  $("dash-sort").value = dashState.sort;
}

function dashTrucks(month) {
  const query = dashState.query.trim().toLowerCase();
  const trucks = dashState.data.camiones.filter((truck) => !query ||
    truck.patente.toLowerCase().includes(query) || (truck.nombre || "").toLowerCase().includes(query));
  const total = (truck) => sum(periodValues(truck, month));
  const compare = {
    total: (a, b) => total(b) - total(a),
    menos: (a, b) => total(a) - total(b),
    patente: (a, b) => a.patente.localeCompare(b.patente),
    silencio: (a, b) => (b.dias_sin_reportes ?? 1e9) - (a.dias_sin_reportes ?? 1e9)
  }[dashState.sort] || ((a, b) => total(b) - total(a));
  return trucks.sort((a, b) => compare(a, b) || a.patente.localeCompare(b.patente));
}

// columns: meses (todos) o null (un mes: total, días con datos, promedio y franja día a día).
function renderDashTable(columns, month) {
  const data = dashState.data;
  const metric = DASH_METRICS[dashState.metric];
  const trucks = dashTrucks(month);
  const all = data.camiones.map((truck) => periodValues(truck, month));
  const max = Math.max(1, ...all.flat());
  const totalMax = Math.max(1, ...all.map(sum));
  const head = el("thead");
  const headRow = el("tr");
  headRow.append(el("th", "fd-col-truck", "Camión"));
  if (columns) {
    for (const column of columns) {
      const th = el("th", "fd-col-month");
      const button = el("button", "", column.label);
      button.type = "button";
      button.title = `Ver ${column.title} día a día`;
      button.addEventListener("click", column.onClick);
      th.classList.toggle("is-current", column.current);
      th.append(button);
      headRow.append(th);
    }
    headRow.append(el("th", "fd-col-total", "Total"));
  } else {
    headRow.append(el("th", "fd-col-total", "Total del mes"), el("th", "fd-col-num", "Días con datos"),
      el("th", "fd-col-num", "Promedio diario"), el("th", "fd-col-strip", "Día a día"));
  }
  headRow.append(el("th", "fd-col-last", "Última señal"));
  head.append(headRow);
  const span = columns ? columns.length + 3 : 6;
  const body = el("tbody");
  for (const truck of trucks) {
    const values = periodValues(truck, month);
    const total = sum(values);
    const row = el("tr", "fd-row");
    row.tabIndex = 0;
    row.setAttribute("aria-expanded", String(dashState.open === truck.patente));
    const who = el("td", "fd-col-truck");
    who.append(el("span", "fd-plate", truck.patente), el("span", "fd-name", truck.nombre || "Sin nombre"));
    row.append(who);
    const totalCell = el("td", "fd-col-total");
    const bar = el("i");
    bar.style.width = Math.max(total ? 4 : 0, total / totalMax * 100) + "%";
    totalCell.append(el("strong", "", format(total, metric.decimals)), bar);
    if (columns) {
      values.forEach((value, k) => {
        const cell = el("td", "fd-cell", value ? format(value, metric.decimals) : "·");
        cell.style.setProperty("--heat", dashHeat(value, max));
        cell.classList.toggle("is-strong", value / max > 0.45);
        cell.classList.toggle("is-empty", !value);
        cell.title = `${truck.patente} · ${columns[k].title}: ${format(value, metric.decimals)} ${metric.unit}` +
          ` · ${format(truck.dias_activos[k])} días con datos`;
        row.append(cell);
      });
      row.append(totalCell);
    } else {
      const active = values.filter(Boolean).length;
      const strip = el("td", "fd-col-strip");
      const bars = el("span", "fd-strip");
      values.forEach((value, k) => {
        const day = el("i");
        day.style.height = (value ? Math.max(12, value / max * 100) : 100) + "%";
        day.classList.toggle("is-zero", !value);
        day.style.setProperty("--heat", dashHeat(value, max));
        day.title = `${dashDayLabel(month.dias[k])}: ${format(value, metric.decimals)} ${metric.unit}`;
        bars.append(day);
      });
      strip.append(bars);
      const activeCell = el("td", "fd-col-num", `${format(active)} de ${format(values.length)}`);
      activeCell.classList.toggle("is-late", active < values.length / 2);
      row.append(totalCell, activeCell,
        el("td", "fd-col-num", active ? format(total / active, metric.decimals) : "—"), strip);
    }
    const last = el("td", "fd-col-last", dashSilence(truck.dias_sin_reportes));
    last.classList.toggle("is-late", truck.dias_sin_reportes === null || truck.dias_sin_reportes >= data.sin_reportes_dias);
    row.append(last);
    const toggle = () => toggleDashTruck(truck.patente);
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
    });
    body.append(row);
    if (dashState.open === truck.patente) body.append(dashDetailRow(truck, span));
  }
  if (!trucks.length) {
    const row = el("tr");
    const cell = el("td", "fd-none", "Ningún camión coincide con la búsqueda.");
    cell.colSpan = span;
    row.append(cell);
    body.append(row);
  }
  const table = $("dash-table");
  table.replaceChildren(head, body);
  table.classList.toggle("is-month", !columns);
  // El detalle del camión ocupa solo el ancho visible, aunque la tabla se desplace de lado.
  table.style.setProperty("--fd-wrap", table.parentElement.clientWidth + "px");
  $("dash-table-foot").textContent = `${format(trucks.length)} de ${format(data.camiones.length)} camiones · ` +
    (columns ? "el color más intenso es el mes más alto de la tabla; toca un mes para verlo día a día. "
      : "en la franja, cada barra es un día; en salmón, los días sin reportes. ") +
    "Toca un camión para ver su calendario completo.";
}

function toggleDashTruck(patente) {
  dashState.open = dashState.open === patente ? null : patente;
  renderDashView();
  if (dashState.open && !dashState.days[patente]) loadDashTruck(patente);
}

async function loadDashTruck(patente) {
  try {
    const data = await api(`/api/dashboard/camion?patente=${encodeURIComponent(patente)}`, {timeoutMs: 30000});
    dashState.days[patente] = data.dias || [];
  } catch (error) {
    dashState.days[patente] = {error: error.message};
  }
  if (dashState.open === patente) renderDashView();
}

function dashDetailRow(truck, columns) {
  const row = el("tr", "fd-detail");
  const cell = el("td");
  cell.colSpan = columns;
  const days = dashState.days[truck.patente];
  if (!days) cell.append(el("p", "fd-loading", "Cargando el calendario…"));
  else if (days.error) cell.append(el("p", "fd-loading", "No se pudo cargar: " + days.error));
  else cell.append(dashCalendar(truck, days));
  row.append(cell);
  return row;
}

function dashCalendar(truck, days) {
  const data = dashState.data;
  const metric = DASH_METRICS[dashState.metric];
  const column = {reportes: 1, km: 2}[dashState.metric];
  const byDay = new Map(days.map((day) => [day[0], day]));
  const start = new Date(data.desde + "T12:00:00Z");
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7)); // lunes
  const end = new Date(data.hoy + "T12:00:00Z");
  const max = Math.max(1, ...days.map((day) => day[column]));
  const grid = el("div", "fd-cal");
  const months = el("div", "fd-cal-months");
  let lastMonth = "";
  for (const day = new Date(start); day <= end; day.setUTCDate(day.getUTCDate() + 7)) {
    const col = el("div", "fd-cal-week");
    for (let d = 0; d < 7; d++) {
      const date = new Date(day);
      date.setUTCDate(date.getUTCDate() + d);
      const iso = date.toISOString().slice(0, 10);
      const square = el("a", "fd-day");
      if (iso < data.desde || iso > data.hoy) {
        square.classList.add("is-out");
      } else {
        const entry = byDay.get(iso);
        const value = entry ? entry[column] : 0;
        square.style.setProperty("--heat", value ? (0.15 + 0.85 * Math.sqrt(value / max)).toFixed(3) : "0");
        square.classList.toggle("is-zero", !value);
        square.classList.toggle("is-period", dashState.period !== "todos" && iso.startsWith(dashState.period));
        square.href = `#camiones/${encodeURIComponent(truck.patente)}/${iso}`;
        square.title = `${dashDayLabel(iso)}: ${format(value, metric.decimals)} ${metric.unit}` + (value ? " · abrir el día" : "");
        square.setAttribute("aria-label", square.title);
      }
      col.append(square);
    }
    const monthKey = day.toISOString().slice(0, 7);
    months.append(el("span", "", monthKey !== lastMonth ? dashMonthLabel(monthKey) : ""));
    lastMonth = monthKey;
    grid.append(col);
  }
  const values = truck[dashState.metric];
  const best = values.indexOf(Math.max(...values));
  const active = days.filter((day) => day[1] > 0);
  const total = sum(days.map((day) => day[column]));
  const stats = el("dl", "fd-cal-stats");
  const stat = (label, value) => {
    const pair = el("div");
    pair.append(el("dt", "", label), el("dd", "", value));
    stats.append(pair);
  };
  stat("Mejor mes", values[best] ? `${dashMonthLabel(data.meses[best], "long")} · ${format(values[best], metric.decimals)}` : "—");
  stat("Días con datos", `${format(active.length)} de ${format(days.length)}`);
  stat("Promedio por día con datos", active.length ? format(total / active.length, metric.decimals) + " " + metric.unit : "—");
  stat("Última señal", truck.ultimo_dia ? dashDayLabel(truck.ultimo_dia) : "Nunca reportó");
  const scroller = el("div", "fd-cal-scroll");
  scroller.append(months, grid);
  const legend = el("div", "fd-cal-legend");
  legend.append(el("span", "", "Menos"), ...[0, 0.35, 0.6, 0.8, 1].map((heat) => {
    const swatch = el("i");
    swatch.style.setProperty("--heat", String(heat));
    return swatch;
  }), el("span", "", "Más · cada cuadro es un día; tócalo para abrir su recorrido" +
    (dashState.period !== "todos" ? "; con borde, el mes elegido" : "")));
  const side = el("div", "fd-cal-side");
  side.append(stats);
  if (truck.ultimo_dia) {
    const link = el("a", "fd-open", "Ver su último día →");
    link.href = `#camiones/${encodeURIComponent(truck.patente)}/${truck.ultimo_dia}`;
    side.append(link);
  }
  const wrap = el("div", "fd-cal-wrap");
  wrap.append(scroller, side);
  const box = el("div");
  box.append(wrap, legend);
  requestAnimationFrame(() => {
    const target = scroller.querySelector(".fd-day.is-period");
    scroller.scrollLeft = target ? target.parentElement.offsetLeft - 20 : scroller.scrollWidth;
  });
  return box;
}

function renderDashSilent(data) {
  $("dash-silent-count").textContent = format(data.sin_reportar.length);
  $("dash-silent-note").textContent = data.sin_reportar.length
    ? "Sin reportes ni ayer ni hoy. Toca uno para ver su último día con datos."
    : "Todos los camiones del catálogo reportaron ayer u hoy.";
  $("dash-silent").replaceChildren(...data.sin_reportar.map((truck) => {
    const item = el("li");
    const link = el("a");
    link.href = truck.ultimo_dia ? `#camiones/${encodeURIComponent(truck.patente)}/${truck.ultimo_dia}`
      : `#camiones/${encodeURIComponent(truck.patente)}`;
    link.append(el("span", "fd-plate", truck.patente), el("span", "fd-name", truck.nombre || "Sin nombre"),
      el("b", "", dashSilence(truck.dias_sin_reportes)));
    item.append(link);
    return item;
  }));
}

function renderDashQuality(data) {
  const totals = data.totales;
  $("dash-q-bar").style.width = (totals.dias ? totals.dias_cerrados / totals.dias * 100 : 0).toFixed(1) + "%";
  const discardedShare = totals.reportes + totals.descartados
    ? totals.descartados / (totals.reportes + totals.descartados) * 100 : 0;
  const rows = [
    ["Días cerrados", `${format(totals.dias_cerrados)} de ${format(totals.dias)}`,
      totals.dias - totals.dias_cerrados > 1 ? "Los demás esperan datos o son hoy" : "Hoy sigue en curso"],
    ["Reportes descartados", format(totals.descartados),
      `${format(discardedShare, 2)} % del total: GPS inválido (por ejemplo, 0,0)`],
    ["Camiones en el catálogo", format(totals.camiones), "Solo se cuentan camiones"]
  ];
  $("dash-quality").replaceChildren(...rows.map(([label, value, note]) => {
    const item = el("li");
    item.append(el("span", "", label), el("strong", "", value), el("small", "", note));
    return item;
  }));
}

function downloadDashCsv() {
  const data = dashState.data;
  if (!data) return;
  const metric = DASH_METRICS[dashState.metric];
  const number = (value) => format(value, 1).replace(/\./g, "");
  const month = dashState.period === "todos" ? null : dashState.months[dashState.period];
  if (dashState.period !== "todos" && (!month || month.error)) return;
  const columns = month ? month.dias.map((iso) => iso) : data.meses.map((m) => dashMonthLabel(m, "long"));
  const lines = [["Patente", "Camión", ...columns, "Total", "Último día con datos"].map(csvValue).join(";")];
  for (const truck of dashTrucks(month)) {
    const values = periodValues(truck, month);
    lines.push([truck.patente, truck.nombre, ...values.map(number), number(sum(values)), truck.ultimo_dia || ""]
      .map(csvValue).join(";"));
  }
  saveCsv(lines, `artisa-${metric.unit}-por-patente-${month ? month.mes : data.desde + "-a-" + data.hoy}.csv`);
}

for (const button of document.querySelectorAll("[data-dash-tab]")) {
  button.addEventListener("click", () => setDashTab(button.dataset.dashTab));
}
for (const button of document.querySelectorAll("[data-dash-metric]")) {
  button.addEventListener("click", () => { dashState.metric = button.dataset.dashMetric; renderDashView(); });
}
$("dash-sort").addEventListener("change", (event) => { dashState.sort = event.target.value; renderDashView(); });
$("dash-search").addEventListener("input", (event) => { dashState.query = event.target.value; renderDashView(); });
$("dash-csv").addEventListener("click", downloadDashCsv);

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
  $("vehicle-count-note").textContent = `camiones con reportes en las últimas ${format(summary.vehiculos_ventana_horas || 24)} h`;
  $("telemetry-range").textContent = connection.ultimo_dato
    ? `Último reporte ${formatLocal(connection.ultimo_dato)} · contacto con la API ${connection.ultimo_exito ? formatLocal(connection.ultimo_exito) : "sin contacto aún"}`
    : "La API está configurada, pero todavía no entrega reportes.";
  $("fleet-tab-reportes-note").textContent = connection.ultimo_dato
    ? "Último reporte " + formatAge((Date.now() - new Date(connection.ultimo_dato)) / 1000) : "Lo último que llegó de la API";
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
    // Cada reporte lleva a la ficha del camión ese día, marcando dónde estaba en ese momento.
    if (report.asset_id && report.report_date) {
      tr.className = "report-row";
      tr.tabIndex = 0;
      tr.title = "Ver el recorrido de este camión y dónde estaba en este reporte";
      const open = () => openTruckAtReport(report);
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
    }
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
      } else problems.push("reportes");
    if (mapResult.status === "fulfilled" && window.ArtisaMapData) {
      renderDashboardGlobal(window.ArtisaMapData.adaptMapPayload(mapResult.value));
    } else problems.push("dashboard global");
    if (dashboardResult.status === "fulfilled") renderDashboard(dashboardResult.value);
    else problems.push("dashboard");

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

async function refreshMapVehicles() {
  if (!token() || document.hidden || liveVehiclesRefreshing || !vg.data) return;
  const version = sessionVersion;
  liveVehiclesRefreshing = true;
  try {
    const payload = await api("/api/vehiculos/posiciones", {timeoutMs: 20000});
    if (version !== sessionVersion || !token() || !vg.data) return;
    vgMergePositions(payload.vehicles || []);
  } catch {
    // Se conservan las últimas posiciones y se reintenta al minuto siguiente.
  } finally {
    if (version === sessionVersion) liveVehiclesRefreshing = false;
  }
}

// --- Historial diario (00:00 a 00:00 hora de Chile) ---
var historyState = {month: null, detail: null};
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

async function loadTruckDetail(assetId, assetName, day, after = null) {
  const detail = after ? historyState.detail : {assetId, assetName, day, next: null, hasIgnition: false};
  historyState.detail = detail;
  $("history-detail-title").textContent = `${assetName || assetId} · ${predictionDayLabel(day, "long")} · hora de Chile`;
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
      link.addEventListener("click", () => { setFleetTab("flota"); setBoardDay(day.fecha); });
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

// CSV para Excel en español: separador ";" y BOM para que respete las tildes.
function saveCsv(lines, filename) {
  const blob = new Blob(["\uFEFF", lines.join("\r\n")], {type: "text/csv;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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
    saveCsv(lines, `artisa-informe-${month}.csv`);
    status.textContent = `CSV generado con ${format(rows.length)} registros del mes.`;
  } catch (error) {
    status.className = "history-download-status is-error";
    status.textContent = "No se pudo generar el CSV: " + error.message;
  } finally {
    button.disabled = false;
    button.textContent = "↓ Descargar CSV";
  }
}

$("history-month").addEventListener("change", () => { if ($("history-month").value) loadHistoryMonth($("history-month").value); });
$("history-month-download").addEventListener("click", downloadHistoryMonth);
$("history-detail-more").addEventListener("click", () => {
  const detail = historyState.detail;
  if (detail && detail.next) loadTruckDetail(detail.assetId, detail.assetName, detail.day, detail.next);
});

$("refresh").addEventListener("click", refresh);

// «Actualizar datos»: corta las consultas que quedaron pegadas, libera cada sección y vuelve
// a pedir todo lo que se está mirando, sin recargar la página ni cerrar la sesión.
async function refreshAll() {
  const button = $("refresh-all");
  if (button.disabled) return;
  button.disabled = true;
  button.classList.add("is-busy");
  for (const controller of pendingRequests) controller.abort();
  pendingRequests.clear();
  refreshing = false;
  telemetryRefreshing = false;
  liveVehiclesRefreshing = false;
  vg.loading = false;
  predictionState.loading = false;
  predictionState.cache = {};
  try {
    const onTruck = Boolean(truckHash()) && truckView.id && !$("truck-view").hidden;
    await Promise.allSettled([
      refresh(), refreshTelemetry(), loadFleet(), loadVigilance(false),
      onTruck ? loadTruckDay(truckView.id, truckView.day, true) : null,
      truckView.boardDay ? setBoardDay(truckView.boardDay) : null,
      window.location.hash === "#prediccion" ? loadPrediction(true) : null
    ]);
    button.title = "Actualizado a las " + new Intl.DateTimeFormat("es-CL", {timeStyle: "short", timeZone: HISTORY_ZONE}).format(new Date());
  } finally {
    button.disabled = false;
    button.classList.remove("is-busy");
  }
}
$("refresh-all").addEventListener("click", refreshAll);
for (const button of document.querySelectorAll("[data-map-mode]")) {
  button.addEventListener("click", () => setMapMode(button.dataset.mapMode));
}
for (const button of document.querySelectorAll("[data-zone-filter]")) {
  button.addEventListener("click", () => {
    vg.zone = button.dataset.zoneFilter;
    vgApplyLayers();
    if (usageLayer) vgDrawUsage();
  });
}
for (const input of document.querySelectorAll("[data-map-layer]")) {
  input.addEventListener("change", () => { vg.layers[input.dataset.mapLayer] = input.checked; vgApplyLayers(); });
}
for (const button of document.querySelectorAll("[data-monitor-tab]")) {
  button.addEventListener("click", () => setMonitorTab(button.dataset.monitorTab));
}
for (const button of document.querySelectorAll("[data-alert-filter]")) {
  button.addEventListener("click", () => { vg.alertFilter = button.dataset.alertFilter; vgRenderAlertList(); });
}
$("monitor-search").addEventListener("input", vgRenderLists);
$("reset-map-view").addEventListener("click", () => vgReset(true));
$("vg-alerts-card").addEventListener("click", () => {
  vg.alertFilter = "activa";
  setMonitorTab("alerts");
  const first = [...vg.alerts.entries()].find(([, alert]) => alert.estado === "activa");
  if (first) vgSelect({kind: "alert", id: first[0]});
});
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
// Dos modelos en pestañas: mapa de calor (zonas) y rutas completas.
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
    endpoint: "/api/prediccion-rutas", unit: "rutas", totalLabel: "Rutas con camiones esperados",
    chartUnit: "Rutas", errorUnit: "camiones por tramo y día",
    value: (item) => item.elementos, cardText: (item) => format(item.elementos) + " rutas",
    intro: "Rutas por las que se espera que pasen camiones",
    method: "El modelo predice cuántos camiones distintos pasarán por cada tramo de ~1 km. Cada ruta completa " +
      "(de la clasificación de rutas) recibe un solo valor: la mediana de lo predicho en los tramos que recorre"
  }
};
const predictionState = {
  tipo: "calor", cache: {}, data: null, loadedAt: 0, loading: false, map: null, layer: null, fitted: false,
  mode: "day", index: 0, from: 0, to: 0, timer: null, scrollTimer: null, wheelBound: false, renderer: null
};

function predictionConfig() { return PREDICTION_TYPES[predictionState.tipo]; }

// Nivel de cada ruta predicha con la misma regla que la clasificación de rutas:
// verde sobre el percentil 67, rojo en el percentil 33 o menos, naranja entre ambos.
function predictionRouteLevels(cells) {
  const values = cells.map((cell) => cell[1]).sort((a, b) => a - b);
  const percentile = (q) => {
    if (!values.length) return 0;
    const position = (values.length - 1) * q;
    const low = Math.floor(position);
    return values[low] + (values[Math.min(low + 1, values.length - 1)] - values[low]) * (position - low);
  };
  const p33 = percentile(1 / 3), p67 = percentile(2 / 3);
  const levels = new Map();
  for (const [id, value] of cells) {
    levels.set(id, values[0] === values[values.length - 1] ? "MEDIUM" : value > p67 ? "HIGH" : value <= p33 ? "LOW" : "MEDIUM");
  }
  return levels;
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

function predictionRouteTip(label, value, dayCount) {
  const tip = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = label;
  tip.append(name, document.createElement("br"),
    `~${format(value, 1)} camiones ${dayCount > 1 ? "por día (promedio)" : "esperados"}`);
  return tip;
}

function ensurePredictionMap() {
  if (predictionState.map || !window.L) return predictionState.map;
  predictionState.map = ArtisaMapaBase.crear($("prediction-map"),
    {pedir: mapPedir, centro: [-24.5, -70], zoom: 6, preferCanvas: false}).mapa;
  return predictionState.map;
}

function drawPrediction() {
  const data = predictionState.data;
  if (!data?.dias?.length) return;
  const days = predictionSelected();
  const config = predictionConfig();
  const routes = predictionState.tipo === "rutas";
  const cells = predictionCells(days);
  const total = routes ? cells.length : days.reduce((sum, item) => sum + item.total, 0);
  // Resumen de la selección
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
    const amount = routes ? `${format(days[0].elementos)} rutas con camiones esperados `
      : `${format(days[0].total)} reportes esperados `;
    text.append(strong, " · " + amount, badge,
      confidence.error === null ? "" : ` · se equivoca ~${format(confidence.error, 2)} ${routes ? "camiones por tramo" : "por zona"}`);
  } else {
    text.textContent = `${predictionDayLabel(days[0].fecha, "long")} → ${predictionDayLabel(days[days.length - 1].fecha, "long")}` +
      (routes ? ` · ${format(total)} rutas con camiones (promedio de camiones por día en cada ruta)`
        : ` · ${format(total)} reportes esperados en ${days.length} días`);
  }
  // Mapa de calor
  const map = ensurePredictionMap();
  if (map) {
    map.invalidateSize();
    if (predictionState.layer) map.removeLayer(predictionState.layer);
    if (routes) {
      // Una línea por ruta completa, de un solo color (su nivel). Primero las rojas, así
      // donde dos rutas comparten camino queda encima la de más camiones.
      predictionState.renderer = predictionState.renderer || L.canvas({padding: 0.5});
      const layer = L.layerGroup();
      const levels = predictionRouteLevels(cells);
      const order = {LOW: 0, MEDIUM: 1, HIGH: 2};
      const sorted = cells.filter(([id]) => data.rutas && data.rutas[id])
        .sort((a, b) => order[levels.get(a[0])] - order[levels.get(b[0])] || a[1] - b[1]);
      for (const [id, value] of sorted) {
        const level = levels.get(id);
        L.polyline(data.rutas[id].lineas, {
          renderer: predictionState.renderer, color: ROUTE_COLORS[level],
          weight: level === "HIGH" ? 5 : level === "MEDIUM" ? 4 : 3, opacity: 0.9, lineCap: "round", lineJoin: "round"
        }).bindTooltip(predictionRouteTip(data.rutas[id].label, value, days.length), {sticky: true}).addTo(layer);
      }
      predictionState.layer = layer;
    } else {
      // Escala logarítmica, igual que el mapa de calor del cuaderno (np.log1p).
      const max = cells.reduce((top, cell) => Math.max(top, cell[cell.length - 1]), 0);
      const points = cells.map(([lat, lon, value]) => [lat, lon, max ? Math.log1p(value) / Math.log1p(max) : 0]);
      predictionState.layer = L.heatLayer
        ? L.heatLayer(points, {radius: 9, blur: 11, minOpacity: 0.3, max: 1, maxZoom: 18, gradient: PREDICTION_GRADIENT})
        : L.layerGroup();
    }
    predictionState.layer.addTo(map);
    const points = routes ? cells.flatMap(([id]) => (data.rutas && data.rutas[id] ? data.rutas[id].lineas.flat() : []))
      : cells.map((cell) => [cell[0], cell[1]]);
    if (!predictionState.fitted && points.length) {
      map.fitBounds(L.latLngBounds(points), {padding: [30, 30], maxZoom: 9});
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
  const config = predictionConfig();
  // La pestaña elegida se marca siempre, aunque su modelo aún no tenga resultados.
  for (const tab of document.querySelectorAll("[data-prediction-tab]")) {
    const selected = tab.dataset.predictionTab === predictionState.tipo;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-selected", String(selected));
  }
  $("prediction-legend-heat").hidden = predictionState.tipo !== "calor";
  $("prediction-legend-routes").hidden = predictionState.tipo !== "rutas";
  $("prediction-chart-unit").textContent = config.chartUnit;
  $("prediction-total-label").textContent = config.totalLabel;
  if (!data || data.status !== "ok" || !data.dias?.length) {
    status.textContent = "Pendiente";
    status.className = "status neutral";
    $("prediction-intro").textContent = (data && data.message) ||
      "La predicción se calcula sola cuando hay al menos 21 días cerrados.";
    // No dejar a la vista los datos del otro modelo.
    for (const id of ["prediction-total", "prediction-peak", "prediction-error", "prediction-base"]) $(id).textContent = "—";
    for (const id of ["prediction-total-detail", "prediction-peak-detail", "prediction-error-detail", "prediction-base-detail",
      "prediction-selection"]) $(id).textContent = "";
    for (const id of ["prediction-wheel", "prediction-daily-chart", "prediction-methods-chart"]) $(id).replaceChildren();
    if (predictionState.layer && predictionState.map) predictionState.map.removeLayer(predictionState.layer);
    predictionState.layer = null;
    return;
  }
  const m = data.metricas || {};
  const usesModel = m.metodo === "lightgbm";
  const errors = m.errores || {};
  status.textContent = PREDICTION_METHODS[m.metodo] || "Predicción";
  status.className = "status " + (usesModel ? "" : "neutral");
  const today = localToday();
  const first = data.dias[0].fecha, last = data.dias[data.dias.length - 1].fecha;
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
  // Error real: lo que la versión anterior predijo para el último día cerrado contra lo que pasó.
  const real = m.error_real;
  const realText = real
    ? `Real el ${predictionDayLabel(real.fecha, "short")}: ${format(real.mae, 2)}` +
      (predictionState.tipo === "calor" ? ` (se esperaban ${format(real.esperado)}, hubo ${format(real.real)})` : "")
    : "";
  $("prediction-error-detail").textContent = [realText,
    others.length ? others.join(" · ") + " · menor es mejor" : `En ${config.errorUnit} · menor es mejor`]
    .filter(Boolean).join(" · ");
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

// --- Camiones: flota en vivo y ficha de cada camión por día (/api/camiones) ----------------
// La flota se lee de la última posición de cada camión; la ficha, del cierre diario
// (resumen, viajes y paradas) y de los reportes GPS del día. No se inventan recorridos.
const TRUCK_STATES = {
  en_ruta: "En ruta", detenido: "Detenido", sin_senal: "Sin señal reciente", sin_datos: "Sin datos"
};
const TRUCK_REFRESH_MS = 60000;
const TRUCK_COLORS = {base: "#9db5aa", halo: "#ffffff", stop: "#e07a1f", start: "#1f6b4f", end: "#172d32", gap: "#71817e"};
// Un color por viaje (se repiten después del séptimo); el naranja queda para las paradas.
const TRIP_COLORS = ["#1f6b4f", "#2563eb", "#9333ea", "#0e7490", "#db2777", "#4d7c0f", "#475569"];
const tripColor = (number) => TRIP_COLORS[(number - 1) % TRIP_COLORS.length];
const truckView = {fleet: null, filter: "todos", query: "", sort: "nombre", id: null, day: null, detail: null,
  map: null, layer: null, trips: new Map(), base: [], focus: null, play: null, request: 0,
  boardDay: null, dayRows: null, dayState: null, dayLoading: false, tab: "flota", pin: null};

function truckHash() {
  const match = window.location.hash.match(/^#camiones(?:\/([A-Za-z0-9_-]{1,16}))?(?:\/(\d{4}-\d{2}-\d{2}))?$/);
  return match ? {id: match[1] || null, day: match[2] || null} : null;
}

function openTruckAtReport(report) {
  const moment = new Date(report.report_date);
  truckView.pin = {
    id: report.asset_id, day: isoDayIn(moment),
    time: new Intl.DateTimeFormat("en-GB", {hour: "2-digit", minute: "2-digit", hour12: false, timeZone: HISTORY_ZONE}).format(moment),
    label: formatLocal(report.report_date), event: report.event_name || "Reporte",
    point: report.latitude === null || report.longitude === null ? null : [Number(report.latitude), Number(report.longitude)]
  };
  truckView.detail = null;  // aunque sea el mismo camión y día, se vuelve a dibujar con la marca
  openTruck(report.asset_id, truckView.pin.day);
}

function openTruck(id, day) {
  window.location.hash = "#camiones/" + encodeURIComponent(id) + (day ? "/" + day : "");
}

// Hora local "YYYY-MM-DD HH:MM:SS" (la API ya la entrega en hora de Chile).
const clock = (value) => value ? value.slice(11, 16) : "—";
const minuteOfDay = (value) => value ? Number(value.slice(11, 13)) * 60 + Number(value.slice(14, 16)) : 0;

function truckAge(minutes) {
  return minutes === null || minutes === undefined ? "Sin reportes" : formatAge(minutes * 60);
}

function syncTruckView() {
  const route = truckHash();
  if (!route) return;
  $("fleet-view").hidden = Boolean(route.id);
  $("truck-view").hidden = !route.id;
  if (!truckView.fleet) loadFleet();
  if (route.id) {
    const day = route.day || localToday();
    if (route.id !== truckView.id || day !== truckView.day || !truckView.detail) loadTruckDay(route.id, day);
    else if (truckView.map) window.setTimeout(() => truckView.map.invalidateSize(), 50);
  }
}

// Una sola petición de flota a la vez: la carga inicial y la pestaña no la duplican.
let fleetLoading = null;
let fleetRetry = null;
const FLEET_RETRY_MS = 10000;

function loadFleet() {
  if (!fleetLoading) fleetLoading = fetchFleet().finally(() => { fleetLoading = null; });
  return fleetLoading;
}

async function fetchFleet() {
  window.clearTimeout(fleetRetry);
  const version = sessionVersion;
  try {
    const data = await api("/api/camiones", {timeoutMs: 90000});
    truckView.fleet = data;
    renderFleet();
    if (truckView.detail) renderTruckHead();
  } catch (error) {
    if (version !== sessionVersion || !token() || truckView.fleet) return;
    // Mientras endpoints está ocupado (por ejemplo, calculando el mapa) se reintenta solo.
    $("fleet-board").replaceChildren(fleetMessage("No se pudo cargar la flota: " + error.message +
      " Reintentando en " + FLEET_RETRY_MS / 1000 + " s…"));
    fleetRetry = window.setTimeout(loadFleet, FLEET_RETRY_MS);
  }
}

function fleetMessage(text) {
  const note = document.createElement("p");
  note.className = "fleet-empty";
  note.textContent = text;
  return note;
}

function sparkline(points) {
  const width = 140, height = 30, max = Math.max(1, ...points.map((p) => p.km));
  const step = width / Math.max(1, points.length);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "fleet-spark");
  svg.setAttribute("aria-hidden", "true");
  points.forEach((point, index) => {
    const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    const h = point.km ? Math.max(2, (point.km / max) * (height - 2)) : 1;
    bar.setAttribute("x", String(index * step + 1));
    bar.setAttribute("y", String(height - h));
    bar.setAttribute("width", String(Math.max(1, step - 2)));
    bar.setAttribute("height", String(h));
    bar.setAttribute("rx", "1.5");
    if (!point.km) bar.setAttribute("class", "empty");
    if (index === points.length - 1) bar.setAttribute("class", point.km ? "today" : "empty");
    svg.append(bar);
  });
  return svg;
}

// El tablero muestra hoy (estado en vivo) o un día anterior (resumen del cierre diario).
const boardDay = () => truckView.boardDay || localToday();
const boardIsToday = () => boardDay() === localToday();
const shortTime = (value) => value ? new Intl.DateTimeFormat("es-CL", {timeStyle: "short", timeZone: HISTORY_ZONE}).format(new Date(value)) : "—";

// Lo que hizo el camión en el día del tablero: {km, viajes, minutos_detenido, primer, ultimo, reportes} o null.
function truckDay(truck) {
  if (boardIsToday()) return truck.hoy;
  const row = truckView.dayRows && truckView.dayRows.get(truck.asset_id);
  return row ? {km: row.km_observados, viajes: row.viajes, minutos_detenido: row.minutos_detenido,
    primer: shortTime(row.primer_reporte), ultimo: shortTime(row.ultimo_reporte), reportes: row.reportes} : null;
}

function fleetRow(truck) {
  const today = boardIsToday();
  const day = truckDay(truck);
  const row = document.createElement("button");
  row.type = "button";
  row.className = "fleet-row state-" + (today ? truck.estado : day ? "dia" : "sin_datos");
  row.addEventListener("click", () => openTruck(truck.asset_id, today ? null : boardDay()));
  const name = document.createElement("span");
  name.className = "fleet-name";
  const title = document.createElement("strong");
  title.textContent = truck.nombre;
  const sub = document.createElement("small");
  sub.className = "fleet-identity";
  sub.append(...truckIdentity(truck.asset_id, truck.conductor, null));
  name.append(title, sub);
  const state = document.createElement("span");
  state.className = "fleet-state";
  const label = document.createElement("strong");
  const note = document.createElement("small");
  if (today) {
    label.innerHTML = '<i class="state-dot"></i>';
    label.append(TRUCK_STATES[truck.estado] + (truck.estado === "en_ruta" && truck.velocidad ? ` · ${format(truck.velocidad)} km/h` : ""));
    note.textContent = truckAge(truck.edad_minutos);
  } else {
    label.textContent = day ? `${(day.primer || "—").slice(0, 5)} – ${(day.ultimo || "—").slice(0, 5)}` : "Sin reportes";
    note.textContent = day ? `${format(day.reportes)} reportes` : "";
  }
  state.append(label, note);
  const summary = document.createElement("span");
  summary.className = "fleet-today";
  if (day) {
    for (const [value, unit] of [[format(day.km, 1), "km"], [format(day.viajes), day.viajes === 1 ? "viaje" : "viajes"],
      [formatMinutes(day.minutos_detenido), "detenido"]]) {
      const item = document.createElement("span");
      const strong = document.createElement("b");
      strong.textContent = value;
      item.append(strong, " " + unit);
      summary.append(item);
    }
  } else {
    summary.textContent = today ? "Sin actividad hoy" : "Sin actividad ese día";
    summary.classList.add("is-empty");
  }
  row.append(name, state, summary, sparkline(truck.serie || []));
  return row;
}

function renderBoardDay() {
  const today = boardIsToday();
  const day = boardDay();
  $("board-date").value = day;
  $("board-date").max = localToday();
  $("board-next").disabled = today;
  $("fleet-head-state").textContent = today ? "Último dato" : "Primer y último reporte";
  $("fleet-head-day").textContent = today ? "Hoy" : "Ese día";
  // Los estados en vivo solo tienen sentido hoy; en un día anterior se filtra por actividad.
  for (const button of document.querySelectorAll('[data-fleet-filter]:not([data-fleet-filter="todos"])')) button.hidden = !today;
  if (!today && truckView.filter !== "todos") truckView.filter = "todos";
  const totals = $("fleet-day-totals");
  const fleet = truckView.fleet;
  const state = truckView.dayState;
  let note = "";
  if (today) {
    const summary = fleet && fleet.resumen;
    totals.textContent = summary ? `${format(summary.km_hoy, 0)} km · ${format(summary.viajes_hoy)} viajes hoy` : "";
    note = "Hoy, en curso: las cifras se actualizan cada minuto.";
  } else if (truckView.dayLoading) {
    totals.textContent = "";
    note = "Consultando el día…";
  } else {
    totals.textContent = state ? `${format(state.camiones)} camiones con reportes · ${format(state.km_observados, 0)} km · ${format(state.reportes)} reportes` : "";
    const [label] = state ? (HISTORY_STATES[state.estado] || [state.estado]) : ["Sin resumen calculado para este día"];
    note = label + (state && state.estado === "esperando_datos" ? ": cifras provisorias hasta que la API confirme los reportes hasta las 00:00." : "") +
      (state && state.recalculos ? ` · recalculado ${format(state.recalculos)} ${state.recalculos === 1 ? "vez" : "veces"} por reportes atrasados` : "");
  }
  $("board-day-note").textContent = `${predictionDayLabel(day, "long")} · ${note}`;
}

async function setBoardDay(day) {
  const today = localToday();
  if (!day || day >= today) {
    truckView.boardDay = null;
    renderBoardDay();
    renderFleet();
    return;
  }
  truckView.boardDay = day;
  truckView.dayLoading = true;
  truckView.dayRows = null;
  truckView.dayState = null;
  renderBoardDay();
  renderFleet();
  try {
    const data = await api(`/api/reportes/dia?fecha=${day}`);
    if (truckView.boardDay !== day) return;
    truckView.dayRows = new Map((data.camiones || []).map((row) => [row.asset_id, row]));
    truckView.dayState = data.estado;
  } catch (error) {
    if (truckView.boardDay !== day) return;
    $("fleet-board").replaceChildren(fleetMessage("No se pudo consultar el día: " + error.message));
  } finally {
    if (truckView.boardDay === day) {
      truckView.dayLoading = false;
      renderBoardDay();
      renderFleet();
    }
  }
}

function renderFleet() {
  const data = truckView.fleet;
  if (!data || data.status !== "ok") {
    $("fleet-board").replaceChildren(fleetMessage("La flota aparecerá cuando el catálogo de camiones esté cargado."));
    return;
  }
  const summary = data.resumen;
  for (const key of ["todos", "en_ruta", "detenido", "sin_senal"]) {
    $("fleet-count-" + key).textContent = format(key === "todos" ? summary.total : summary[key] + (key === "sin_senal" ? summary.sin_datos : 0));
  }
  for (const button of document.querySelectorAll("[data-fleet-filter]")) {
    button.setAttribute("aria-pressed", String(button.dataset.fleetFilter === truckView.filter));
  }
  if (!boardIsToday() && truckView.dayLoading) {
    $("fleet-board").replaceChildren(fleetMessage("Cargando el día…"));
    return;
  }
  const query = truckView.query.trim().toLocaleLowerCase("es-CL");
  let trucks = data.camiones.filter((truck) => truckView.filter === "todos" || truck.estado === truckView.filter
    || (truckView.filter === "sin_senal" && truck.estado === "sin_datos"));
  if (query) {
    trucks = trucks.filter((truck) => [truck.nombre, truck.asset_id, truck.conductor].some((value) =>
      value && value.toLocaleLowerCase("es-CL").includes(query)));
  }
  const byName = (a, b) => a.nombre.localeCompare(b.nombre, "es-CL", {numeric: true});
  const km = (truck) => (truckDay(truck) || {}).km || 0;
  const sorters = {
    nombre: byName,
    km: (a, b) => km(b) - km(a) || byName(a, b),
    dato: (a, b) => (a.edad_minutos ?? Infinity) - (b.edad_minutos ?? Infinity) || byName(a, b)
  };
  trucks.sort(sorters[truckView.sort] || byName);
  $("fleet-tab-flota-note").textContent = boardIsToday()
    ? `${format(summary.total)} camiones · ${format(summary.en_ruta)} en ruta ahora`
    : `${format(truckView.dayRows ? truckView.dayRows.size : 0)} con actividad el ${predictionDayLabel(boardDay())}`;
  // En un día anterior, primero los que tuvieron actividad.
  if (!boardIsToday()) trucks.sort((a, b) => Number(Boolean(truckDay(b))) - Number(Boolean(truckDay(a))));
  $("fleet-board").replaceChildren(...(trucks.length ? trucks.map(fleetRow)
    : [fleetMessage(query ? "Ningún camión coincide con la búsqueda." : "No hay camiones en este estado.")]));
  renderBoardDay();
}

async function loadTruckDay(id, day, quiet = false) {
  const request = ++truckView.request;
  truckView.id = id;
  truckView.day = day;
  $("truck-date").value = day;
  $("truck-date").max = localToday();
  $("truck-next").disabled = day >= localToday();
  if (!quiet) {
    stopTruckPlay();
    // La lista de reportes GPS es del día anterior: se cierra y se vuelve a pedir al abrirla.
    historyState.detail = null;
    $("history-detail").open = false;
    $("history-detail-rows").replaceChildren();
    $("history-detail-more").hidden = true;
    $("truck-day-note").textContent = "Cargando el día…";
    $("truck-image").disabled = true;
  }
  renderTruckHead();
  try {
    const data = await api(`/api/camiones/${encodeURIComponent(id)}?fecha=${day}`, {timeoutMs: 90000});
    if (request !== truckView.request) return;
    truckView.detail = data;
    renderTruckDay(quiet);
  } catch (error) {
    if (request !== truckView.request) return;
    truckView.detail = null;
    $("truck-day-note").textContent = "No se pudo cargar este día: " + error.message;
  }
}

function fleetTruck(id) {
  return truckView.fleet && truckView.fleet.camiones ? truckView.fleet.camiones.find((truck) => truck.asset_id === id) : null;
}

// Patente con aspecto de placa, conductor y última ruta (con el color de su nivel de uso).
function truckIdentity(plate, driver, route) {
  const nodes = [];
  const plateNode = document.createElement("span");
  plateNode.className = "plate";
  plateNode.textContent = plate;
  plateNode.title = "Patente";
  nodes.push(plateNode);
  const driverNode = document.createElement("span");
  driverNode.className = "driver" + (driver ? "" : " is-empty");
  driverNode.title = "Conductor";
  driverNode.textContent = driver ? driver.toLocaleLowerCase("es-CL").replace(/(^|\s)\S/g, (c) => c.toLocaleUpperCase("es-CL")) : "Sin conductor informado";
  nodes.push(driverNode);
  if (route) {
    const routeNode = document.createElement("span");
    routeNode.className = "last-route";
    routeNode.style.setProperty("--route", ROUTE_COLORS[route.usage_level] || "#718785");
    routeNode.title = "Última ruta clasificada";
    routeNode.textContent = route.label;
    nodes.push(routeNode);
  }
  return nodes;
}

function renderTruckHead() {
  const live = fleetTruck(truckView.id);
  const detail = truckView.detail && truckView.detail.asset_id === truckView.id ? truckView.detail : null;
  const name = (live && live.nombre) || (detail && detail.nombre) || truckView.id;
  $("truck-name").textContent = "Camión " + name;
  const driver = (detail && detail.conductor) || (live && live.conductor);
  $("truck-meta").replaceChildren(...truckIdentity(truckView.id, driver, live && live.ruta));
  const state = $("truck-state");
  state.className = "truck-state state-" + (live ? live.estado : "sin_datos");
  state.innerHTML = '<i class="state-dot"></i>';
  state.append(live ? `${TRUCK_STATES[live.estado]} · ${truckAge(live.edad_minutos)}` : "Estado en vivo no disponible");
}

function dayStatusNote(data) {
  if (!data.resumen) return "Este camión no tiene reportes este día.";
  if (data.estado_dia === "en_curso") return "Hoy, en curso: las cifras se actualizan cada pocos minutos hasta las 00:00.";
  if (data.estado_dia === "esperando_datos") return "El día terminó, pero faltan reportes de la API: las cifras son provisorias.";
  return "Día cerrado (00:00 a 00:00, hora de Chile).";
}

function renderTruckDay(keepView = false) {
  const data = truckView.detail;
  renderTruckHead();
  const summary = data.resumen;
  const pin = activePin(data);
  $("truck-day-note").textContent = `${predictionDayLabel(data.fecha, "long")} · ${dayStatusNote(data)}` +
    (pin ? ` · El punto rojo marca dónde estaba a las ${pin.time} (${pin.event}).` : "");
  $("truck-km").textContent = summary ? format(summary.km, 1) : "—";
  const average = (data.historial || []).filter((item) => item.fecha !== data.fecha && item.km > 0);
  const mean = average.length ? average.reduce((sum, item) => sum + item.km, 0) / average.length : null;
  $("truck-km-note").textContent = summary && mean
    ? `Su promedio en días con actividad: ${format(mean, 0)} km` : " ";
  $("truck-trips").textContent = summary ? format(summary.viajes) : "—";
  $("truck-stopped").textContent = summary ? formatMinutes(summary.minutos_detenido) : "—";
  $("truck-stopped-note").textContent = summary ? `${format(summary.paradas)} ${summary.paradas === 1 ? "parada" : "paradas"} de 10 min o más` : " ";
  $("truck-span").textContent = summary ? `${clock(summary.primer)} – ${clock(summary.ultimo)}` : "—";
  $("truck-span-note").textContent = summary ? `${formatMinutes(summary.minutos_activo)} entre ambos` : " ";
  const hasRoute = Boolean(data.recorrido && data.recorrido.lineas.length);
  $("truck-image").disabled = !hasRoute;
  $("truck-play").disabled = !data.viajes.length;
  $("truck-signal-note").textContent = data.recorrido && data.recorrido.cortes
    ? `El GPS dejó de reportar ${format(data.recorrido.cortes)} ${data.recorrido.cortes === 1 ? "vez" : "veces"} ` +
      `(${formatMinutes(data.recorrido.minutos_sin_senal)} en total).` : "";
  renderTruckRibbon(data);
  renderTruckEvents(data);
  renderTruckHistory(data);
  if (truckView.play) return;  // no interrumpir la reproducción con la actualización automática
  drawTruckMap(data, keepView);
}

// Orden cronológico de viajes, paradas e intervalos sin datos.
function truckTimeline(data) {
  const items = [
    ...data.viajes.map((trip) => ({kind: "viaje", from: trip.inicio, to: trip.fin, trip})),
    ...data.eventos.map((event) => ({kind: event.tipo, from: event.desde, to: event.hasta, event}))
  ];
  return items.sort((a, b) => a.from.localeCompare(b.from));
}

// --- Geometría auxiliar (mapa e imagen) ---
const distanceM = (a, b) => Math.hypot(b[0] - a[0], (b[1] - a[1]) * Math.cos((a[0] + b[0]) * Math.PI / 360)) * 111000;

// Paradas agrupadas por lugar (a menos de 300 m): una sola marca con todas sus visitas,
// así las paradas repetidas en la base no se tapan entre sí.
function stopPlaces(data) {
  const places = [];
  let number = 0;
  for (const event of data.eventos) {
    if (event.tipo !== "parada" || !event.lugar) continue;
    number += 1;
    let place = places.find((item) => distanceM(item.lugar, event.lugar) < 300);
    if (!place) { place = {lugar: event.lugar, nombre: event.nombre, visitas: []}; places.push(place); }
    if (!place.nombre && event.nombre) place.nombre = event.nombre;
    place.visitas.push({...event, numero: number});
  }
  return places;
}

function stopPlaceLabel(place) {
  const numbers = place.visitas.map((visit) => visit.numero);
  return numbers.length <= 3 ? numbers.join(" ") : `${numbers[0]}…${numbers[numbers.length - 1]}`;
}

// Cortes de señal: línea recta (punteada) entre el último punto antes del corte y el
// primero después. Se dibuja aparte para que se entienda el recorrido completo, pero no es
// el camino exacto.
function signalGaps(lines) {
  const gaps = [];
  for (let k = 1; k < lines.length; k++) {
    const from = lines[k - 1][lines[k - 1].length - 1], to = lines[k][0];
    if (distanceM(from, to) > 40) gaps.push([from, to]);
  }
  return gaps;
}

// Punto a una fracción del largo de un viaje y su rumbo (grados, 0 = este, sentido horario).
function pointAlong(lines, fraction) {
  const segments = [];
  let total = 0;
  for (const line of lines) {
    for (let k = 1; k < line.length; k++) {
      const length = distanceM(line[k - 1], line[k]);
      if (length > 0) { segments.push([line[k - 1], line[k], length]); total += length; }
    }
  }
  if (!segments.length) return null;
  let target = total * fraction;
  for (const [a, b, length] of segments) {
    if (target <= length) {
      const t = target / length;
      const angle = Math.atan2(-(b[0] - a[0]), (b[1] - a[1]) * Math.cos(a[0] * Math.PI / 180)) * 180 / Math.PI;
      return {point: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], angle};
    }
    target -= length;
  }
  const [a, b] = segments[segments.length - 1];
  return {point: b, angle: Math.atan2(-(b[0] - a[0]), (b[1] - a[1]) * Math.cos(a[0] * Math.PI / 180)) * 180 / Math.PI};
}

function tripLength(trip) {
  let total = 0;
  for (const line of trip.lineas) for (let k = 1; k < line.length; k++) total += distanceM(line[k - 1], line[k]);
  return total;
}

// --- Cinta de 24 horas ---
function renderTruckRibbon(data) {
  const ribbon = $("truck-ribbon");
  ribbon.replaceChildren();
  const dayStart = data.fecha + " 00:00:00";
  for (const item of truckTimeline(data)) {
    // Un tramo que empezó el día anterior se recorta a las 00:00.
    const from = item.from < dayStart ? 0 : minuteOfDay(item.from);
    const to = item.to.slice(0, 10) > data.fecha ? 1440 : minuteOfDay(item.to);
    if (to <= from) continue;
    const block = document.createElement("span");
    block.className = "ribbon-block " + (item.kind === "viaje" ? "move" : item.kind === "parada" ? "stop" : "gap");
    block.style.left = (from / 14.4) + "%";
    block.style.width = Math.max(0.3, (to - from) / 14.4) + "%";
    block.title = item.kind === "viaje"
      ? `Viaje ${item.trip.numero}: ${clock(item.from)}–${clock(item.to)} · ${format(item.trip.km, 1)} km`
      : `${item.kind === "parada" ? "Detenido" : "Sin datos"}: ${clock(item.from)}–${clock(item.to)} (${formatMinutes(item.event.minutos)})`;
    if (item.trip) {
      block.style.background = tripColor(item.trip.numero);
      block.addEventListener("click", () => focusTrip(item.trip.numero));
    }
    ribbon.append(block);
  }
  const pin = activePin(data);
  if (pin) {
    const mark = document.createElement("span");
    mark.className = "ribbon-pin";
    mark.style.left = ((Number(pin.time.slice(0, 2)) * 60 + Number(pin.time.slice(3, 5))) / 14.4) + "%";
    mark.title = `Reporte de las ${pin.time}`;
    ribbon.append(mark);
  }
  if (data.fecha === localToday()) {
    const now = document.createElement("span");
    now.className = "ribbon-now";
    const parts = new Intl.DateTimeFormat("en-GB", {hour: "2-digit", minute: "2-digit", hour12: false, timeZone: HISTORY_ZONE}).format(new Date()).split(":");
    now.style.left = ((Number(parts[0]) * 60 + Number(parts[1])) / 14.4) + "%";
    now.title = "Ahora";
    ribbon.append(now);
  }
  if (!ribbon.children.length) ribbon.append(Object.assign(document.createElement("span"), {className: "ribbon-empty", textContent: "Sin viajes ni paradas este día"}));
}

// --- El día, paso a paso ---
function renderTruckEvents(data) {
  const list = $("truck-events");
  list.replaceChildren();
  let stopNumber = 0;
  for (const item of truckTimeline(data)) {
    const li = document.createElement("li");
    const time = document.createElement("time");
    time.textContent = clock(item.from);
    const body = document.createElement("div");
    const title = document.createElement("strong");
    const detail = document.createElement("small");
    const mark = document.createElement("span");
    if (item.kind === "viaje") {
      const trip = item.trip;
      li.className = "event move";
      li.dataset.trip = String(trip.numero);
      li.style.setProperty("--trip", tripColor(trip.numero));
      mark.className = "event-mark trip";
      mark.textContent = "V" + trip.numero;
      title.textContent = `Viaje ${trip.numero} · ${format(trip.km, 1)} km`;
      detail.textContent = `${clock(trip.inicio)} a ${clock(trip.fin)} · ${formatMinutes(trip.minutos)}` +
        (trip.abierto_inicio ? " · venía del día anterior" : "") + (trip.abierto_fin ? " · sigue al día siguiente" : "") +
        (trip.con_saltos ? " · con cortes de señal" : "");
      li.tabIndex = 0;
      li.title = "Ver este viaje en el mapa";
      li.addEventListener("click", () => focusTrip(trip.numero));
      li.addEventListener("keydown", (event) => { if (event.key === "Enter") focusTrip(trip.numero); });
    } else if (item.kind === "parada") {
      stopNumber += 1;
      li.className = "event stop";
      mark.className = "event-mark stop";
      mark.textContent = String(stopNumber);
      title.textContent = `Detenido ${formatMinutes(item.event.minutos)}`;
      detail.textContent = `${clock(item.from)} a ${clock(item.to)} · ${item.event.nombre || "Lugar sin nombre"}`;
    } else {
      li.className = "event gap";
      mark.className = "event-mark gap";
      mark.textContent = "?";
      title.textContent = `Sin datos ${formatMinutes(item.event.minutos)}`;
      detail.textContent = `${clock(item.from)} a ${clock(item.to)} · el GPS no reportó; no se sabe dónde estuvo`;
    }
    body.append(title, detail);
    li.append(time, mark, body);
    list.append(li);
  }
  // Llegando desde un reporte: se marca qué estaba haciendo el camión a esa hora.
  const pin = activePin(data);
  if (pin) {
    const at = `${data.fecha} ${pin.time}:00`;
    const items = truckTimeline(data);
    const index = items.findIndex((item) => item.from <= at && at <= item.to);
    const node = index >= 0 ? list.children[index] : null;
    if (node) {
      node.classList.add("is-report");
      window.setTimeout(() => { list.scrollTop = node.offsetTop - list.offsetTop - list.clientHeight / 3; }, 50);
    }
  }
  if (!list.children.length) {
    const li = document.createElement("li");
    li.className = "event empty";
    li.textContent = data.resumen
      ? "Hubo reportes, pero ningún viaje de más de 1 km ni paradas de 10 min."
      : "Sin reportes este día. Elige otro día en el calendario o en los últimos 30 días.";
    list.append(li);
  }
}

// --- Últimos 30 días: ventana fija que termina hoy ---
function renderTruckHistory(data) {
  const chart = $("truck-history");
  const byDay = new Map((data.historial || []).map((item) => [item.fecha, item]));
  const today = data.hoy || localToday();
  const days = Array.from({length: 30}, (_, k) => shiftDay(today, k - 29));
  const max = Math.max(1, ...days.map((day) => (byDay.get(day) || {}).km || 0));
  chart.replaceChildren(...days.map((day) => {
    const item = byDay.get(day);
    const button = document.createElement("button");
    button.type = "button";
    const weekday = predictionDate(day).getUTCDay();
    button.className = "history-bar" + (day === data.fecha ? " is-selected" : "") + (weekday === 0 || weekday === 6 ? " weekend" : "");
    button.title = `${predictionDayLabel(day, "long")}: ` + (item ? `${format(item.km, 1)} km · ${format(item.viajes)} viajes` : "sin reportes");
    button.setAttribute("aria-pressed", String(day === data.fecha));
    const bar = document.createElement("i");
    bar.style.height = item && item.km ? Math.max(3, (item.km / max) * 100) + "%" : "2px";
    const label = document.createElement("span");
    label.textContent = String(Number(day.slice(8)));
    button.append(bar, label);
    button.addEventListener("click", () => openTruck(data.asset_id, day));
    return button;
  }));
}

// --- Mapa del día ---
function ensureTruckMap() {
  if (truckView.map || !window.L) return truckView.map;
  truckView.map = ArtisaMapaBase.crear($("truck-map"), {pedir: mapPedir, centro: [-23.6, -70.4], zoom: 7}).mapa;
  truckView.map.createPane("truckMarks").style.zIndex = 650;
  truckView.map.on("zoomend", applyTripLanes);
  return truckView.map;
}

function pinIcon(text, className, color) {
  const style = color ? ` style="--pin:${color}"` : "";
  return L.divIcon({className: "truck-pin-icon", html: `<span class="truck-pin ${className}"${style}>${text}</span>`,
    iconSize: [0, 0], iconAnchor: [0, 0]});
}

function arrowIcon(angle, color) {
  return L.divIcon({className: "truck-pin-icon",
    html: `<span class="truck-arrow" style="--pin:${color};transform:rotate(${angle.toFixed(0)}deg)"></span>`,
    iconSize: [14, 14], iconAnchor: [7, 7]});
}

// Los viajes se corren unos píxeles a la derecha de su sentido de marcha, como pistas de una
// carretera: la ida y la vuelta por el mismo camino quedan lado a lado en vez de taparse.
const TRIP_LANE_PX = 4;

function offsetPath(points, px) {
  if (points.length < 2) return points.map((p) => [p[0], p[1]]);
  return points.map((p, k) => {
    const a = points[Math.max(0, k - 1)], b = points[Math.min(points.length - 1, k + 1)];
    const dx = b[0] - a[0], dy = b[1] - a[1], length = Math.hypot(dx, dy) || 1;
    return [p[0] - dy / length * px, p[1] + dx / length * px];
  });
}

function laneLines(map, lines) {
  return lines.map((line) => offsetPath(line.map((p) => {
    const point = map.latLngToLayerPoint(p);
    return [point.x, point.y];
  }), TRIP_LANE_PX).map(([x, y]) => {
    const latlng = map.layerPointToLatLng([x, y]);
    return [latlng.lat, latlng.lng];
  }));
}

function applyTripLanes() {
  const map = truckView.map;
  if (!map) return;
  for (const entry of truckView.trips.values()) {
    const lanes = laneLines(map, entry.trip.lineas);
    entry.lanes = lanes;
    for (const part of entry.parts) part.setLatLngs(lanes);
    for (const mark of entry.marks) {
      const at = pointAlong(lanes, mark.options.fraction);
      if (at) mark.setLatLng(at.point);
    }
  }
}

// Marcas fijas del mapa: paradas agrupadas por lugar, y A (primer punto) y B (último). Si A o
// B caen donde hay paradas, van en la misma marca para que no se tapen.
function placeMarks(data) {
  const lines = data.recorrido ? data.recorrido.lineas : [];
  const marks = stopPlaces(data).map((place) => ({lugar: place.lugar, place, start: false, end: false}));
  if (lines.length) {
    const first = lines[0][0], lastLine = lines[lines.length - 1], last = lastLine[lastLine.length - 1];
    for (const [point, key] of [[first, "start"], [last, "end"]]) {
      let mark = marks.find((item) => distanceM(item.lugar, point) < 600);
      if (!mark) { mark = {lugar: point, place: null, start: false, end: false}; marks.push(mark); }
      mark[key] = true;
    }
  }
  return marks.map((mark) => ({...mark, parts: [
    ...(mark.start ? [["start", "A"]] : []),
    ...(mark.place ? [["stop", stopPlaceLabel(mark.place)]] : []),
    ...(mark.end ? [["end", "B"]] : [])
  ]}));
}

function placeMarkTooltip(mark, data) {
  const lines = [];
  if (mark.start) lines.push(`<strong>A</strong> · primer punto del día · ${clock(data.recorrido.inicio)}`);
  if (mark.place) {
    lines.push(`<strong>${mark.place.visitas.length > 1 ? `${mark.place.visitas.length} paradas en ` : "Parada en "}` +
      `${escapeText(mark.place.nombre || "lugar sin nombre")}</strong>`);
    for (const visit of mark.place.visitas) {
      lines.push(`${visit.numero}. ${clock(visit.desde)}–${clock(visit.hasta)} (${formatMinutes(visit.minutos)})`);
    }
  }
  if (mark.end) lines.push(`<strong>B</strong> · último punto · ${clock(data.recorrido.fin)}`);
  return lines.join("<br>");
}

function drawTruckMap(data, keepView = false) {
  const map = ensureTruckMap();
  if (!map) return;
  map.invalidateSize();
  if (truckView.layer) map.removeLayer(truckView.layer);
  truckView.trips.clear();
  truckView.base = [];
  truckView.focus = null;
  $("truck-show-all").hidden = true;
  const layer = L.featureGroup();
  const lines = data.recorrido ? data.recorrido.lineas : [];
  // 1. Todo lo que registró el GPS (también movimientos cortos que no son viajes), de fondo.
  if (lines.length) {
    truckView.base.push(
      L.polyline(lines, {color: TRUCK_COLORS.halo, weight: 6, opacity: 0.85, interactive: false}).addTo(layer),
      L.polyline(lines, {color: TRUCK_COLORS.base, weight: 3, opacity: 0.9, interactive: false}).addTo(layer));
    for (const gap of signalGaps(lines)) {
      truckView.base.push(L.polyline(gap, {color: TRUCK_COLORS.gap, weight: 2, opacity: 0.8, dashArray: "2 7"})
        .bindTooltip("Sin señal: línea recta entre dos reportes, no es el camino exacto", {sticky: true}).addTo(layer));
    }
  }
  // 2. Cada viaje con su color, flechas de sentido y su número (V1, V2…).
  for (const trip of data.viajes) {
    const color = tripColor(trip.numero);
    const parts = [
      L.polyline(trip.lineas, {color: TRUCK_COLORS.halo, weight: 8, opacity: 0.95, lineJoin: "round", lineCap: "round"}),
      L.polyline(trip.lineas, {color, weight: 4, opacity: 1, lineJoin: "round", lineCap: "round"})
    ];
    const tip = `Viaje ${trip.numero}: ${clock(trip.inicio)}–${clock(trip.fin)} · ${format(trip.km, 1)} km`;
    for (const part of parts) part.bindTooltip(tip, {sticky: true}).on("click", () => focusTrip(trip.numero));
    const marks = [];
    if (tripLength(trip) > 400) {
      for (const fraction of [0.2, 0.8]) {
        const at = pointAlong(trip.lineas, fraction);
        if (at) marks.push(L.marker(at.point, {icon: arrowIcon(at.angle, color), interactive: false, pane: "truckMarks", fraction}));
      }
      const fraction = 0.35;
      const middle = pointAlong(trip.lineas, fraction);
      if (middle) marks.push(L.marker(middle.point, {icon: pinIcon("V" + trip.numero, "trip", color), pane: "truckMarks", fraction})
        .bindTooltip(tip).on("click", () => focusTrip(trip.numero)));
    }
    for (const item of [...parts, ...marks]) item.addTo(layer);
    truckView.trips.set(trip.numero, {trip, parts, marks, lanes: trip.lineas});
  }
  // 3. Paradas (agrupadas por lugar) y los puntos A y B.
  for (const mark of placeMarks(data)) {
    const html = `<span class="truck-pin group">${mark.parts.map(([kind, text]) => `<b class="${kind}">${text}</b>`).join("")}</span>`;
    L.marker(mark.lugar, {icon: L.divIcon({className: "truck-pin-icon", html, iconSize: [0, 0], iconAnchor: [0, 0]}),
      zIndexOffset: mark.start || mark.end ? 600 : 400, pane: "truckMarks"})
      .bindTooltip(placeMarkTooltip(mark, data)).addTo(layer);
  }
  layer.addTo(map);
  truckView.layer = layer;
  // Si se llegó desde un reporte, se marca dónde estaba el camión en ese momento.
  const pin = activePin(data);
  if (pin && pin.point) {
    L.marker(pin.point, {icon: L.divIcon({className: "truck-pin-icon", iconSize: [0, 0], iconAnchor: [0, 0],
      html: `<span class="report-pin"><b></b><em>${escapeText(pin.time)}</em></span>`}), zIndexOffset: 1000, pane: "truckMarks"})
      .bindTooltip(`Reporte del ${escapeText(pin.label)} · ${escapeText(pin.event)}`).addTo(layer);
  }
  if (!keepView && layer.getLayers().length) map.fitBounds(layer.getBounds(), {padding: [30, 30], maxZoom: 14, animate: false});
  applyTripLanes();
}

function activePin(data) {
  const pin = truckView.pin;
  return pin && data && pin.id === data.asset_id && pin.day === data.fecha ? pin : null;
}

function escapeText(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

// Ver un viaje: él al frente, el resto atenuado.
function focusTrip(number, fit = true) {
  const entry = truckView.trips.get(number);
  if (!entry || !truckView.map) return;
  truckView.focus = number;
  for (const [key, item] of truckView.trips) {
    const active = key === number;
    item.parts[0].setStyle({opacity: active ? 0.95 : 0});
    item.parts[1].setStyle({opacity: active ? 1 : 0.18, weight: active ? 6 : 3.5});
    for (const mark of item.marks) mark.setOpacity(active ? 1 : 0);
    if (active) { item.parts[0].bringToFront(); item.parts[1].bringToFront(); }
  }
  for (const base of truckView.base) base.setStyle({opacity: 0.35});
  for (const node of document.querySelectorAll(".truck-events .event.move")) {
    node.classList.toggle("is-selected", node.dataset.trip === String(number));
  }
  $("truck-show-all").hidden = false;
  if (fit) truckView.map.fitBounds(L.featureGroup(entry.parts).getBounds(), {padding: [50, 50], maxZoom: 15});
}

function showWholeDay() {
  stopTruckPlay();
  if (truckView.detail) drawTruckMap(truckView.detail);
  for (const node of document.querySelectorAll(".truck-events .event.is-selected")) node.classList.remove("is-selected");
}

// --- Reproducir el día: el camión recorre sus viajes en orden y se detiene en cada parada ---
function playbackSteps(data) {
  const steps = [];
  let stopNumber = 0;
  for (const item of truckTimeline(data)) {
    if (item.kind === "viaje") {
      if (item.trip.lineas.flat().length > 1) steps.push({kind: "viaje", trip: item.trip});
    } else if (item.kind === "parada") {
      stopNumber += 1;
      if (item.event.lugar) steps.push({kind: "parada", event: item.event, numero: stopNumber});
    } else {
      steps.push({kind: "sin_datos", event: item.event});
    }
  }
  return steps;
}

function setPlayStatus(text) {
  $("truck-play-status").textContent = text;
}

function stopTruckPlay() {
  const play = truckView.play;
  if (!play) return;
  cancelAnimationFrame(play.frame);
  clearTimeout(play.timer);
  if (play.layer && truckView.map) truckView.map.removeLayer(play.layer);
  truckView.play = null;
  $("truck-play").textContent = "▶ Reproducir el día";
  $("truck-play").setAttribute("aria-pressed", "false");
  setPlayStatus("");
  for (const node of document.querySelectorAll(".truck-events .event.is-playing")) node.classList.remove("is-playing");
}

function toggleTruckPlay() {
  if (truckView.play) { showWholeDay(); return; }
  const data = truckView.detail;
  const map = truckView.map;
  if (!data || !map || !data.viajes.length) return;
  drawTruckMap(data);
  for (const item of truckView.trips.values()) {
    item.parts[0].setStyle({opacity: 0});
    item.parts[1].setStyle({opacity: 0.15});
    for (const mark of item.marks) mark.setOpacity(0);
  }
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const play = {steps: playbackSteps(data), index: 0, frame: 0, timer: 0, layer: L.layerGroup().addTo(map)};
  play.truck = L.marker(data.viajes[0].lineas[0][0],
    {icon: L.divIcon({className: "truck-pin-icon", html: '<span class="truck-pin play">⛟</span>', iconSize: [0, 0], iconAnchor: [0, 0]}),
      zIndexOffset: 900, pane: "truckMarks"}).addTo(play.layer);
  truckView.play = play;
  $("truck-play").textContent = "■ Detener";
  $("truck-play").setAttribute("aria-pressed", "true");
  const markItem = (selector) => {
    for (const node of document.querySelectorAll(".truck-events .event.is-playing")) node.classList.remove("is-playing");
    const node = selector && document.querySelector(selector);
    if (node) {
      node.classList.add("is-playing");
      const list = $("truck-events");
      list.scrollTop = node.offsetTop - list.offsetTop - list.clientHeight / 3;
    }
  };
  const next = () => {
    if (truckView.play !== play) return;
    if (play.index >= play.steps.length) {
      setPlayStatus("Fin del día · " + clock(data.recorrido.fin));
      play.timer = setTimeout(() => { if (truckView.play === play) showWholeDay(); }, 2500);
      return;
    }
    const step = play.steps[play.index++];
    if (step.kind === "parada") {
      play.truck.setLatLng(step.event.lugar);
      setPlayStatus(`Parada ${step.numero} · detenido ${formatMinutes(step.event.minutos)} · ${clock(step.event.desde)}–${clock(step.event.hasta)}` +
        (step.event.nombre ? ` · ${step.event.nombre}` : ""));
      markItem(null);
      play.timer = setTimeout(next, reduceMotion ? 300 : 900);
      return;
    }
    if (step.kind === "sin_datos") {
      setPlayStatus(`Sin datos · ${formatMinutes(step.event.minutos)} sin reportes`);
      play.timer = setTimeout(next, reduceMotion ? 200 : 600);
      return;
    }
    const trip = step.trip, color = tripColor(trip.numero);
    // Si el viaje no cabe en la vista, el mapa lo sigue (sin animar, para que las pistas se recalculen).
    const tripBounds = L.latLngBounds(trip.lineas.flat());
    if (!map.getBounds().contains(tripBounds)) map.fitBounds(tripBounds, {padding: [40, 40], maxZoom: 15, animate: false});
    const entryLanes = truckView.trips.get(trip.numero);
    step.points = (entryLanes ? entryLanes.lanes : trip.lineas).flat();
    setPlayStatus(`Viaje ${trip.numero} · ${clock(trip.inicio)} → ${clock(trip.fin)} · ${format(trip.km, 1)} km`);
    markItem(`.truck-events .event.move[data-trip="${trip.numero}"]`);
    const entry = truckView.trips.get(trip.numero);
    if (entry) for (const mark of entry.marks) mark.setOpacity(1);
    L.polyline(step.points, {color: TRUCK_COLORS.halo, weight: 9, opacity: 0.9, interactive: false}).addTo(play.layer);
    const drawn = L.polyline([step.points[0]], {color, weight: 5, opacity: 1, interactive: false}).addTo(play.layer);
    const duration = reduceMotion ? 1 : Math.min(5000, 1200 + trip.km * 25);
    const started = performance.now();
    const animate = (now) => {
      if (truckView.play !== play) return;
      const t = Math.min(1, (now - started) / duration);
      const count = Math.max(1, Math.round(t * (step.points.length - 1)));
      drawn.setLatLngs(step.points.slice(0, count + 1));
      play.truck.setLatLng(step.points[count]);
      if (t < 1) play.frame = requestAnimationFrame(animate);
      else play.timer = setTimeout(next, 250);
    };
    play.frame = requestAnimationFrame(animate);
  };
  next();
}

// --- Imagen del recorrido ---
// Lienzo con el mapa base de OpenStreetMap si el navegador lo permite (si no, fondo liso),
// los viajes con su color, sentido y número, las paradas, y a la derecha el día paso a paso.
const TILE = 256;
const mercator = (lat, lon, zoom) => {
  const scale = TILE * 2 ** zoom, sin = Math.sin(lat * Math.PI / 180);
  return [(lon + 180) / 360 * scale, (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale];
};

function loadTile(url) {
  return new Promise((resolve) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    const timer = setTimeout(() => resolve(null), 8000);
    image.onload = () => { clearTimeout(timer); resolve(image); };
    image.onerror = () => { clearTimeout(timer); resolve(null); };
    image.src = url;
  });
}

function roundedRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

async function drawTruckImage(data, withTiles) {
  const width = 1600, height = 1100, header = 150, footer = 64, side = 450, pad = 40;
  const mapW = width - side;
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#eef2ee";
  ctx.fillRect(0, 0, width, height);
  const lines = data.recorrido.lineas;
  const points = lines.flat();
  const lats = points.map((p) => p[0]), lons = points.map((p) => p[1]);
  const top = header, bottom = height - footer;
  let zoom = 17;
  for (; zoom > 3; zoom--) {
    const [x0, y0] = mercator(Math.max(...lats), Math.min(...lons), zoom);
    const [x1, y1] = mercator(Math.min(...lats), Math.max(...lons), zoom);
    if (x1 - x0 <= mapW - pad * 2 && y1 - y0 <= bottom - top - pad * 2) break;
  }
  const [cx0, cy0] = mercator(Math.max(...lats), Math.min(...lons), zoom);
  const [cx1, cy1] = mercator(Math.min(...lats), Math.max(...lons), zoom);
  const origin = [(cx0 + cx1) / 2 - mapW / 2, (cy0 + cy1) / 2 - (top + bottom) / 2];
  const project = ([lat, lon]) => { const [x, y] = mercator(lat, lon, zoom); return [x - origin[0], y - origin[1]]; };
  ctx.save();
  ctx.beginPath(); ctx.rect(0, top, mapW, bottom - top); ctx.clip();
  let tilesDrawn = 0;
  if (withTiles) {
    const tiles = [];
    const maxTile = 2 ** zoom;
    for (let tx = Math.floor(origin[0] / TILE); tx <= Math.floor((origin[0] + mapW) / TILE); tx++) {
      for (let ty = Math.floor((origin[1] + top) / TILE); ty <= Math.floor((origin[1] + bottom) / TILE); ty++) {
        if (ty < 0 || ty >= maxTile) continue;
        const wrapped = ((tx % maxTile) + maxTile) % maxTile;
        tiles.push(loadTile(`https://tile.openstreetmap.org/${zoom}/${wrapped}/${ty}.png`).then((image) => ({image, tx, ty})));
      }
    }
    for (const {image, tx, ty} of await Promise.all(tiles)) {
      if (image) { ctx.drawImage(image, tx * TILE - origin[0], ty * TILE - origin[1]); tilesDrawn += 1; }
    }
    if (tilesDrawn) { ctx.fillStyle = "rgba(244,246,243,.35)"; ctx.fillRect(0, top, mapW, bottom - top); }
  }
  const strokePixels = (paths, color, lineWidth, dash = []) => {
    ctx.strokeStyle = color; ctx.lineWidth = lineWidth; ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.setLineDash(dash);
    for (const line of paths) {
      ctx.beginPath();
      line.forEach(([x, y], index) => { if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
      ctx.stroke();
    }
    ctx.setLineDash([]);
  };
  const strokeLines = (paths, color, lineWidth, dash) => strokePixels(paths.map((line) => line.map(project)), color, lineWidth, dash);
  // Viajes en su "pista" (a la derecha del sentido de marcha), igual que en el mapa de la web.
  const lanes = new Map(data.viajes.map((trip) => [trip.numero, trip.lineas.map((line) => offsetPath(line.map(project), 5))]));
  const alongPixels = (paths, fraction) => {
    const segments = [];
    let total = 0;
    for (const line of paths) for (let k = 1; k < line.length; k++) {
      const length = Math.hypot(line[k][0] - line[k - 1][0], line[k][1] - line[k - 1][1]);
      if (length) { segments.push([line[k - 1], line[k], length]); total += length; }
    }
    let target = total * fraction;
    for (const [a, b, length] of segments) {
      if (target <= length) {
        const t = target / length;
        return {x: a[0] + (b[0] - a[0]) * t, y: a[1] + (b[1] - a[1]) * t, angle: Math.atan2(b[1] - a[1], b[0] - a[0])};
      }
      target -= length;
    }
    return null;
  };
  strokeLines(lines, "#ffffff", 7);
  strokeLines(lines, TRUCK_COLORS.base, 3.5);
  strokeLines(signalGaps(lines), TRUCK_COLORS.gap, 2.5, [3, 9]);
  for (const trip of data.viajes) {
    strokePixels(lanes.get(trip.numero), "#ffffff", 9);
    strokePixels(lanes.get(trip.numero), tripColor(trip.numero), 4.5);
  }
  const font = (weight, size) => `${weight} ${size}px Inter, "Segoe UI", Arial, sans-serif`;
  for (const trip of data.viajes) {
    if (tripLength(trip) <= 400) continue;
    const color = tripColor(trip.numero);
    for (const fraction of [0.2, 0.8]) {
      const at = alongPixels(lanes.get(trip.numero), fraction);
      if (!at) continue;
      ctx.save(); ctx.translate(at.x, at.y); ctx.rotate(at.angle);
      ctx.beginPath(); ctx.moveTo(9, 0); ctx.lineTo(-7, -7); ctx.lineTo(-3, 0); ctx.lineTo(-7, 7); ctx.closePath();
      ctx.fillStyle = color; ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2.5; ctx.stroke(); ctx.fill();
      ctx.restore();
    }
    const middle = alongPixels(lanes.get(trip.numero), 0.35);
    if (middle) {
      const {x, y} = middle;
      ctx.font = font(700, 15);
      const label = "V" + trip.numero, w = ctx.measureText(label).width + 16;
      roundedRect(ctx, x - w / 2, y - 13, w, 26, 6);
      ctx.fillStyle = color; ctx.fill(); ctx.lineWidth = 2.5; ctx.strokeStyle = "#ffffff"; ctx.stroke();
      ctx.fillStyle = "#ffffff"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(label, x, y + 1);
    }
  }
  // Paradas agrupadas y A/B: una sola marca por lugar, con un tramo de color por cada parte.
  const fills = {start: TRUCK_COLORS.start, stop: TRUCK_COLORS.stop, end: TRUCK_COLORS.end};
  for (const mark of placeMarks(data)) {
    const [x, y] = project(mark.lugar);
    ctx.font = font(700, 15);
    const widths = mark.parts.map(([, text]) => Math.max(32, ctx.measureText(text).width + 18));
    const total = widths.reduce((sum, w) => sum + w, 0);
    let left = x - total / 2;
    roundedRect(ctx, left - 3, y - 19, total + 6, 38, 19);
    ctx.fillStyle = "#ffffff"; ctx.fill();
    ctx.save();
    roundedRect(ctx, left, y - 16, total, 32, 16); ctx.clip();
    mark.parts.forEach(([kind, text], index) => {
      ctx.fillStyle = fills[kind]; ctx.fillRect(left, y - 16, widths[index], 32);
      if (index) { ctx.fillStyle = "#ffffff"; ctx.fillRect(left, y - 16, 2, 32); }
      ctx.fillStyle = "#ffffff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(text, left + widths[index] / 2, y + 1);
      left += widths[index];
    });
    ctx.restore();
  }
  ctx.restore();
  // Encabezado
  ctx.fillStyle = "#172d32";
  ctx.fillRect(0, 0, width, header);
  ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "#d5ee9c"; ctx.font = font(600, 19);
  ctx.fillText("Artisa · recorrido del día", pad, 44);
  ctx.fillStyle = "#ffffff"; ctx.font = font(700, 40);
  const label = predictionDayLabel(data.fecha, "long");
  ctx.fillText(`Camión ${data.nombre}  ·  ${label.charAt(0).toUpperCase() + label.slice(1)}`, pad, 94);
  const summary = data.resumen;
  ctx.fillStyle = "#b8c9c4"; ctx.font = font(400, 20);
  ctx.fillText([
    summary ? `${format(summary.km, 1)} km` : null,
    summary ? `${format(summary.viajes)} ${summary.viajes === 1 ? "viaje" : "viajes"}` : null,
    summary ? `${formatMinutes(summary.minutos_detenido)} detenido` : null,
    summary ? `reportes de ${clock(summary.primer)} a ${clock(summary.ultimo)}` : null,
    data.conductor ? `conductor ${data.conductor}` : null
  ].filter(Boolean).join("   ·   "), pad, 128);
  // Columna derecha: el día paso a paso
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(mapW, top, side, bottom - top);
  ctx.fillStyle = "#dce4e2"; ctx.fillRect(mapW, top, 1, bottom - top);
  ctx.fillStyle = "#172d32"; ctx.font = font(700, 20); ctx.textAlign = "left";
  ctx.fillText("El día, paso a paso", mapW + 28, top + 40);
  const rows = truckTimeline(data);
  const rowHeight = 36, maxRows = Math.floor((bottom - top - 80) / rowHeight);
  let y = top + 78, stopNumber = 0;
  rows.slice(0, rows.length > maxRows ? maxRows - 1 : maxRows).forEach((item) => {
    ctx.font = font(400, 15); ctx.fillStyle = "#687b7c"; ctx.textAlign = "left";
    ctx.fillText(clock(item.from), mapW + 28, y);
    let mark, color, text;
    if (item.kind === "viaje") {
      mark = "V" + item.trip.numero; color = tripColor(item.trip.numero);
      text = `Viaje · ${format(item.trip.km, 1)} km · ${formatMinutes(item.trip.minutos)}`;
    } else if (item.kind === "parada") {
      mark = String(++stopNumber); color = TRUCK_COLORS.stop;
      text = `Detenido ${formatMinutes(item.event.minutos)}` + (item.event.nombre ? ` · ${item.event.nombre}` : "");
    } else {
      mark = "?"; color = "#9aa8a6"; text = `Sin datos ${formatMinutes(item.event.minutos)}`;
    }
    ctx.font = font(700, 13);
    const w = Math.max(30, ctx.measureText(mark).width + 14);
    roundedRect(ctx, mapW + 86, y - 17, w, 24, 6);
    ctx.fillStyle = color; ctx.fill();
    ctx.fillStyle = "#ffffff"; ctx.textAlign = "center"; ctx.fillText(mark, mapW + 86 + w / 2, y);
    ctx.textAlign = "left"; ctx.fillStyle = "#172d32"; ctx.font = font(500, 15);
    let shown = text;
    while (ctx.measureText(shown).width > side - 160 && shown.length > 4) shown = shown.slice(0, -2);
    ctx.fillText(shown === text ? text : shown.trimEnd() + "…", mapW + 130, y);
    y += rowHeight;
  });
  if (rows.length > maxRows) {
    ctx.font = font(400, 14); ctx.fillStyle = "#687b7c";
    ctx.fillText(`… y ${rows.length - maxRows + 1} más (ver en la web)`, mapW + 28, y);
  }
  // Pie
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, bottom, width, footer);
  ctx.fillStyle = "#dce4e2"; ctx.fillRect(0, bottom, width, 1);
  ctx.font = font(400, 16); ctx.textAlign = "left"; ctx.textBaseline = "middle";
  const legend = [[TRUCK_COLORS.start, "A primer punto"], [TRIP_COLORS[1], "V1, V2… viajes (flecha = sentido)"],
    [TRUCK_COLORS.stop, "1, 2… paradas"], [TRUCK_COLORS.gap, "punteado: sin señal"], [TRUCK_COLORS.end, "B último punto"]];
  let x = pad;
  for (const [color, text] of legend) {
    ctx.beginPath(); ctx.arc(x + 8, bottom + footer / 2, 8, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
    ctx.fillStyle = "#4d6264"; ctx.fillText(text, x + 22, bottom + footer / 2 + 1);
    x += ctx.measureText(text).width + 52;
  }
  ctx.textAlign = "right";
  ctx.fillText(`GPS real · hora de Chile${tilesDrawn ? " · © OpenStreetMap" : ""}`, width - pad, bottom + footer / 2 + 1);
  return canvas;
}
async function downloadTruckImage() {
  const data = truckView.detail;
  if (!data || !data.recorrido || !data.recorrido.lineas.length) return;
  const button = $("truck-image");
  button.disabled = true;
  button.textContent = "Generando imagen…";
  try {
    let canvas = await drawTruckImage(data, true);
    let blob;
    try {
      blob = await new Promise((resolve, reject) => {
        try { canvas.toBlob((value) => value ? resolve(value) : reject(new Error("vacío")), "image/png"); }
        catch (error) { reject(error); }
      });
    } catch {
      // El mapa base no permitió exportarse: se genera igual, sin fondo de mapa.
      canvas = await drawTruckImage(data, false);
      blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `recorrido-${data.nombre}-${data.fecha}.png`.replace(/[^\w.-]+/g, "_");
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
  } finally {
    button.disabled = false;
    button.textContent = "↓ Imagen del recorrido";
  }
}

for (const button of document.querySelectorAll("[data-fleet-filter]")) {
  button.addEventListener("click", () => { truckView.filter = button.dataset.fleetFilter; renderFleet(); });
}
$("fleet-search").addEventListener("input", () => { truckView.query = $("fleet-search").value; renderFleet(); });
$("fleet-sort").addEventListener("change", () => { truckView.sort = $("fleet-sort").value; renderFleet(); });
$("truck-back").addEventListener("click", () => { truckView.pin = null; window.location.hash = "#camiones"; });
$("truck-date").addEventListener("change", () => { if ($("truck-date").value) openTruck(truckView.id, $("truck-date").value); });
$("truck-prev").addEventListener("click", () => openTruck(truckView.id, shiftDay(truckView.day || localToday(), -1)));
$("truck-next").addEventListener("click", () => {
  const next = shiftDay(truckView.day || localToday(), 1);
  if (next <= localToday()) openTruck(truckView.id, next);
});
$("truck-today").addEventListener("click", () => openTruck(truckView.id, localToday()));
$("truck-yesterday").addEventListener("click", () => openTruck(truckView.id, shiftDay(localToday(), -1)));
$("truck-image").addEventListener("click", downloadTruckImage);
$("truck-play").addEventListener("click", toggleTruckPlay);
$("history-detail").addEventListener("toggle", () => {
  const detail = truckView.detail;
  if ($("history-detail").open && detail && !historyState.detail) loadTruckDetail(detail.asset_id, detail.nombre, detail.fecha);
});
$("board-date").addEventListener("change", () => setBoardDay($("board-date").value));
$("board-prev").addEventListener("click", () => setBoardDay(shiftDay(boardDay(), -1)));
$("board-next").addEventListener("click", () => setBoardDay(shiftDay(boardDay(), 1)));
$("board-today").addEventListener("click", () => setBoardDay(null));
$("board-yesterday").addEventListener("click", () => setBoardDay(shiftDay(localToday(), -1)));
// Tres tareas en el mismo espacio: la lista de camiones, los reportes en vivo y el mes.
function setFleetTab(tab) {
  if (tab === "flota") truckView.pin = null;
  truckView.tab = tab;
  for (const button of document.querySelectorAll("[data-fleet-tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.fleetTab === tab));
  }
  $("fleet-panel-flota").hidden = tab !== "flota";
  $("live-reports").hidden = tab !== "reportes";
  $("month-panel").hidden = tab !== "mes";
  if (tab === "mes" && historyState.month !== boardDay().slice(0, 7)) loadHistoryMonth(boardDay().slice(0, 7));
}
for (const button of document.querySelectorAll("[data-fleet-tab]")) {
  button.addEventListener("click", () => setFleetTab(button.dataset.fleetTab));
}
$("truck-show-all").addEventListener("click", showWholeDay);
window.addEventListener("hashchange", () => { if (!truckHash()) stopTruckPlay(); });
setInterval(() => {
  if (!token() || !truckHash()) return;
  loadFleet();
  // Hoy se actualiza solo, sin mover el mapa ni cortar la reproducción.
  if (truckView.id && truckView.day === localToday() && !$("truck-view").hidden && !truckView.play) {
    loadTruckDay(truckView.id, truckView.day, true);
  }
}, TRUCK_REFRESH_MS);

restoreSession();
setInterval(refresh, 300000);
setInterval(refreshMapVehicles, 60000);
// Los últimos 7 días avanzan con la hora real: se recalculan cada minuto aunque no lleguen datos.
setInterval(() => { if (!document.hidden) loadVigilance(); }, VG_REFRESH_MS);
setInterval(refreshTelemetry, TELEMETRY_REFRESH_MS);
// Al recuperar la red o volver a la pestaña, actualizar de inmediato sin esperar el minuto.
window.addEventListener("online", () => { refreshTelemetry(); refreshMapVehicles(); loadVigilance(); refresh(); });
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshTelemetry();
    refreshMapVehicles();
    loadVigilance();
    refresh();
  }
});
