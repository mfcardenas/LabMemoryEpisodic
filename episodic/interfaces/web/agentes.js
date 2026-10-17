/* Consola de agentes: ventana flotante con la acción de los agentes, tipo log.
   Mientras una pregunta se responde, lee en directo el registro de actividad
   (componente «agente», filtrado por run_id): quién actúa, qué herramienta usa,
   qué encuentra, cuánto tarda. Al terminar muestra la traza guardada con la
   respuesta; también reabre la de cualquier respuesta anterior. */
(() => {
"use strict";

const G = () => window.EpiGraph;
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c]);

const KIND = { question: "PREGUNTA", route: "ENRUTADO", plan: "PLAN", map: "COBERTURA", found: "HALLAZGOS", narrate: "NARRADOR",
  thinking: "MODELO", turn: "DECISIÓN", tool: "HERRAMIENTA", retry: "REINTENTO", budget: "PRESUPUESTO", verify: "VERIFICACIÓN",
  error: "ERROR", answer: "RESPUESTA" };
const FILTERS = { all: null, tools: ["tool", "map"], decisions: ["route", "plan", "turn", "found", "narrate", "verify", "answer"], problems: ["error", "retry", "budget"] };
const STATE_LABEL = { working: "trabajando", done: "terminado", error: "error", waiting: "en espera" };

const A = {
  run: null,          // { id, question, startedAt, finished, network, trace: [], meta }
  afterId: 0, timer: 0, clock: 0, busy: false,
  agent: null, filter: "all", follow: true, onUpdate: null,
};

// -- Quién es quién ---------------------------------------------------------------------
const roleOf = name => (name || "").split(" ")[0];
function agentOf(item) {
  if (item.agent) return item.agent;
  return A.run?.network ? "coordinador" : "agente de consulta";
}
function roster() {
  const list = new Map();
  const touch = (name, item) => {
    if (!list.has(name)) list.set(name, { name, role: roleOf(name), first: item.at, last: item.at, turns: 0, tools: 0, errors: 0, found: null, state: "working" });
    const agent = list.get(name);
    agent.last = item.at || agent.last;
    return agent;
  };
  for (const item of A.run?.trace || []) {
    const agent = touch(agentOf(item), item);
    if (item.kind === "turn") agent.turns += 1;
    if (item.kind === "tool") agent.tools += 1;
    if (item.kind === "error") { agent.errors += 1; agent.state = "error"; }
    if (item.kind === "found") { agent.found = item.findings ?? null; agent.state = item.error ? "error" : "done"; }
    if (["plan", "narrate"].includes(item.kind) || (item.kind === "verify" && item.agent)) agent.state = "done";
  }
  // Al terminar, los datos guardados mandan (vueltas, herramientas y tiempo exactos).
  if (A.run?.finished) {
    for (const agent of list.values()) if (agent.state === "working") agent.state = "done";
    for (const saved of A.run.meta?.agents || []) {
      const agent = list.get(saved.name);
      if (!agent) continue;
      Object.assign(agent, { turns: saved.turns ?? agent.turns, tools: saved.tool_calls ?? agent.tools, took: saved.took_ms, found: saved.findings ?? agent.found });
      if (saved.error) agent.state = "error";
    }
  }
  return [...list.values()];
}
const seconds = (a, b) => (a && b ? Math.max(0, (new Date(b) - new Date(a)) / 1000) : 0);

// -- Pintado ------------------------------------------------------------------------------
function render() {
  const box = $("#agent-console");
  if (!box || !A.run) return;
  const run = A.run, agents = roster();
  const elapsed = run.finished ? (run.meta?.took_ms || 0) / 1000 : (Date.now() - run.startedAt) / 1000;
  const status = run.finished
    ? `${run.meta?.error ? "con error" : "terminada"} · ${elapsed.toFixed(1)} s · ${agents.length} agente(s)${run.meta?.confidence ? ` · ${esc(run.meta.confidence)}` : ""}`
    : `en curso · ${elapsed.toFixed(0)} s · ${agents.length} agente(s)`;
  box.dataset.running = String(!run.finished);
  $(".ac-status", box).innerHTML = `${run.finished ? "" : '<span class="spinner" aria-hidden="true"></span>'}${status}`;
  $(".ac-question", box).innerHTML = `<b>${run.network ? "Red de agentes" : "Agente único"}</b> ${esc(run.question)}`;
  $(".ac-roster", box).innerHTML = agents.map(agent => {
    const took = agent.took != null ? agent.took / 1000 : seconds(agent.first, agent.last);
    const on = A.agent === agent.name ? " on" : "";
    return `<button type="button" class="ac-agent-card role-${esc(agent.role)} state-${agent.state}${on}" data-agent="${esc(agent.name)}" title="Filtrar el log por este agente">
      <i></i><b>${esc(agent.name)}</b><small>${STATE_LABEL[agent.state] || agent.state} · ${agent.turns} vuelta(s) · ${agent.tools} herr.${agent.found != null ? ` · ${agent.found} hallazgo(s)` : ""} · ${took.toFixed(1)} s</small></button>`;
  }).join("");
  const wanted = FILTERS[A.filter];
  const t0 = run.trace[0]?.at ? new Date(run.trace[0].at).getTime() : run.startedAt;
  const lines = run.trace.filter(item => (!A.agent || agentOf(item) === A.agent) && (!wanted || wanted.includes(item.kind)));
  const log = $(".ac-log", box);
  log.innerHTML = lines.map(item => {
    const name = agentOf(item);
    const message = String(item.message || "").replace(new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}:\\s*`, "i"), "");
    const delta = item.at ? `+${((new Date(item.at).getTime() - t0) / 1000).toFixed(1)}s` : "";
    return `<div class="ac-line kind-${esc(item.kind)} role-${esc(roleOf(name))}${item.error ? " has-error" : ""}"><time>${esc(delta)}</time><span class="ac-who" title="${esc(name)}">${esc(name)}</span><span class="ac-kind">${esc(KIND[item.kind] || item.kind || "")}</span><span class="ac-msg">${esc(message)}</span></div>`;
  }).join("") + (run.finished ? "" : '<div class="ac-line pending"><time></time><span class="ac-who"></span><span class="ac-kind"><span class="spinner" aria-hidden="true"></span></span><span class="ac-msg">esperando la siguiente acción…</span></div>');
  if (!lines.length && run.finished) log.innerHTML = '<div class="ac-empty">Sin líneas con este filtro.</div>';
  if (A.follow) log.scrollTop = log.scrollHeight;
  $$(".ac-filters [data-filter]", box).forEach(button => button.setAttribute("aria-pressed", String(button.dataset.filter === A.filter)));
  $(".ac-agent-clear", box).hidden = !A.agent;
  const pill = $("#agent-console-pill");
  pill.innerHTML = `${run.finished ? "" : '<span class="spinner" aria-hidden="true"></span>'}Agentes · ${run.finished ? "terminada" : `${elapsed.toFixed(0)} s`} · ${agents.length}`;
  if (A.onUpdate && !run.finished) A.onUpdate(run.trace, agents);
}
function setState(state) {
  const box = $("#agent-console");
  box.dataset.state = state;
  box.hidden = state === "closed" || state === "min";
  $("#agent-console-pill").hidden = !(state === "min" || (state === "closed" && A.run && !A.run.finished));
  if (state === "open" || state === "max") { render(); }
}

// -- Directo -------------------------------------------------------------------------------
async function poll() {
  if (A.busy || !A.run || A.run.finished) return;
  A.busy = true;
  try {
    const data = await G().api(`/api/activity?component=agente&after_id=${A.afterId}&limit=300`);
    if (data.entries.length) A.afterId = Math.max(A.afterId, ...data.entries.map(entry => entry.id));
    data.entries.filter(entry => entry.data?.run_id === A.run.id).sort((a, b) => a.id - b.id).forEach(entry => {
      const { kind, agent, error, findings } = entry.data || {};
      A.run.trace.push({ at: entry.at, kind, agent, error, findings, message: entry.message });
      if (kind === "plan" || kind === "map" || (kind === "route" && /red de agentes/i.test(entry.message))) A.run.network = true;
    });
    render();
  } catch (_) { /* el siguiente sondeo lo reintenta */ }
  finally { A.busy = false; }
}

async function start({ runId, question, onUpdate }) {
  stopTimers();
  A.run = { id: runId, question, startedAt: Date.now(), finished: false, network: false, trace: [], meta: null };
  A.agent = null; A.filter = "all"; A.follow = true; A.onUpdate = onUpdate || null;
  try { A.afterId = (await G().api("/api/activity?limit=1")).last_id || 0; } catch (_) { A.afterId = 0; }
  setState(window.innerWidth < 720 ? "min" : "open");
  A.timer = setInterval(poll, 1000);
  A.clock = setInterval(render, 1000);
  render();
}

function stopTimers() { clearInterval(A.timer); clearInterval(A.clock); A.timer = A.clock = 0; }

// Al terminar: la traza guardada es la completa y en orden.
async function finish(run, error) {
  stopTimers();
  if (!A.run) return;
  if (run) load(run, { open: false });
  else { await poll(); A.run.finished = true; A.run.meta = { error: error?.message || "sin respuesta" }; render(); }
  const box = $("#agent-console");
  if (box.dataset.state === "closed") $("#agent-console-pill").hidden = true;
}

function load(run, { open = true } = {}) {
  A.run = {
    id: run.id, question: run.question, startedAt: run.trace?.[0]?.at ? new Date(run.trace[0].at).getTime() : Date.now(), finished: true,
    network: run.metadata?.mode === "red", trace: (run.trace || []).map(item => ({ ...item })),
    meta: { agents: run.metadata?.agents || [], took_ms: run.took_ms, confidence: run.confidence, error: run.error },
  };
  if (open) { A.agent = null; A.filter = "all"; A.follow = true; setState("open"); }
  else render();
}

async function show(run) { load(run); await fillRuns(); }
async function showById(id) {
  try { load(await G().api(`/api/answers/${encodeURIComponent(id)}`)); }
  catch (error) { console.error(error); }
}

async function fillRuns() {
  const select = $("#agent-console .ac-runs");
  try {
    const data = await G().api("/api/answers?limit=30");
    select.innerHTML = '<option value="">Ejecuciones recientes…</option>' + (data.answers || []).map(run =>
      `<option value="${esc(run.id)}"${A.run?.id === run.id ? " selected" : ""}>${esc(new Date(run.created_at).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }))} · ${run.metadata?.mode === "red" ? "red" : "1 agente"} · ${esc((run.question || "").slice(0, 60))}</option>`).join("");
  } catch (_) { select.innerHTML = '<option value="">Sin historial</option>'; }
}

function copyLog() {
  if (!A.run) return;
  const text = A.run.trace.map(item => `${item.at || ""}\t${agentOf(item)}\t${KIND[item.kind] || item.kind}\t${item.message || ""}`).join("\n");
  navigator.clipboard?.writeText(`${A.run.question}\n${text}`).catch(() => {});
}

function bind() {
  const box = $("#agent-console");
  if (!box) return;
  box.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.ac === "min") setState("min");
    else if (button.dataset.ac === "max") setState(box.dataset.state === "max" ? "open" : "max");
    else if (button.dataset.ac === "close") setState("closed");
    else if (button.dataset.ac === "copy") copyLog();
    else if (button.dataset.agent) { A.agent = A.agent === button.dataset.agent ? null : button.dataset.agent; render(); }
    else if (button.classList.contains("ac-agent-clear")) { A.agent = null; render(); }
    else if (button.dataset.filter) { A.filter = button.dataset.filter; render(); }
  });
  $(".ac-follow", box).addEventListener("change", event => { A.follow = event.target.checked; if (A.follow) render(); });
  $(".ac-runs", box).addEventListener("focus", fillRuns);
  $(".ac-runs", box).addEventListener("change", event => { if (event.target.value) showById(event.target.value); });
  $(".ac-log", box).addEventListener("wheel", () => {
    const log = $(".ac-log", box);
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
    if (A.follow !== atBottom && !A.run?.finished) { A.follow = atBottom; $(".ac-follow", box).checked = atBottom; }
  }, { passive: true });
  $("#agent-console-pill").addEventListener("click", () => setState("open"));
  $("#agent-console-open")?.addEventListener("click", async () => { if (A.run) setState("open"); else { await fillRuns(); setState("open"); render(); emptyState(); } });
}

function emptyState() {
  if (A.run) return;
  const box = $("#agent-console");
  $(".ac-question", box).innerHTML = "<b>Consola de agentes</b> Lanza una pregunta o elige una ejecución reciente.";
  $(".ac-roster", box).innerHTML = "";
  $(".ac-log", box).innerHTML = '<div class="ac-empty">Aquí aparece, línea a línea, lo que hace cada agente: plan, cobertura de episodios, herramientas, hallazgos, narración y verificación.</div>';
  $(".ac-status", box).textContent = "";
}

window.EpiGraphAgents = { start, finish, show, showById, open: () => setState("open") };
document.addEventListener("DOMContentLoaded", () => { bind(); });
})();
