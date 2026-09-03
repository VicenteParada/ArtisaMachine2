"use strict";

const $ = (id) => document.getElementById(id);
const format = (value, decimals = 0) => Number(value).toLocaleString("es-CL", {maximumFractionDigits: decimals});
let trips = [];
let expanded = false;
let refreshing = false;

async function api(path, options = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(10000)});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "El servicio está temporalmente ocupado.");
  return payload;
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
    const [state, summary, dataset] = await Promise.all([api("/api/estado"), api("/api/resumen"), api("/api/viajes?limite=500")]);
    renderState(state);
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
refresh();
setInterval(refresh, 15000);
