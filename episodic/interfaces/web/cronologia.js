// Línea temporal a escala (Fase T): pulso, pistas por fuente, inspector y relato.
// El servidor solo devuelve lo que cabe en pantalla: /api/timeline/density para
// el pulso y el minimapa, /api/timeline/items para la ventana visible (o grupos
// por cubo si no caben). Usa de app.js lo que expone en window.EpiGraph.
(() => {
"use strict";
const $ = (s, root = document) => root.querySelector(s);
const G = () => window.EpiGraph || {};
const api = (url, options) => G().api(url, options);
const esc = value => String(value ?? "").replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c]);
const num = n => Math.round(n).toLocaleString("es-ES");
const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : many}`;
const pad = n => String(n).padStart(2, "0");
const mmss = s => `${pad(Math.floor(s / 60))}:${pad(Math.floor(s % 60))}`;
const iso = t => new Date(t).toISOString();
const root = () => $("#view-cronologia");
const token = name => getComputedStyle(root()).getPropertyValue(name).trim();

// -- Tiempo: siempre en la zona horaria de la memoria (la del servidor) ---------------
let F = {};
function setTimezone(tz) {
  let zone = tz;
  try { new Intl.DateTimeFormat("es-ES", { timeZone: zone }); } catch (_) { zone = undefined; }
  const fmt = (options, locale = "es-ES") => new Intl.DateTimeFormat(locale, { timeZone: zone, ...options });
  F = {
    clock: fmt({ hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }),
    hm: fmt({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" }),
    day: fmt({ day: "numeric", month: "short", year: "numeric" }),
    dayShort: fmt({ day: "numeric", month: "short" }),
    dayHour: fmt({ day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }),
    parts: fmt({ year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }, "en-US"),
  };
}
setTimezone(undefined);
const clock = (t, tenth = false) => F.clock.format(new Date(t)) + (tenth ? "," + Math.floor((((t % 1000) + 1000) % 1000) / 100) : "");
const dur = ms => {
  const s = ms / 1000;
  if (s < 60) return s.toLocaleString("es-ES", { maximumFractionDigits: s < 10 ? 1 : 0 }) + " s";
  if (s < 3600) { const m = Math.floor(s / 60), r = Math.round(s % 60); return r ? `${m} min ${r} s` : `${m} min`; }
  if (s < 172800) { const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return m ? `${h} h ${m} min` : `${h} h`; }
  return `${Math.round(s / 86400)} días`;
};
function tzOffset(t) {
  const p = Object.fromEntries(F.parts.formatToParts(new Date(t)).map(x => [x.type, x.value]));
  return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second) - Math.floor(t / 1000) * 1000;
}
const localDayStart = t => { const off = tzOffset(t); return Math.floor((t + off) / 864e5) * 864e5 - off; };

// -- Estado ---------------------------------------------------------------------------
const C = {
  bound: false, span: null, envs: [], pulse: null, vs: 0, ve: 0, minSpan: 2000, maxSpan: 864e5,
  loaded: null, hidden: new Set(), collapsed: new Set(), sel: null, mode: "tracks",
  ctl: null, timer: 0, fitPending: false, mini: null, miniKey: "", miniCtl: null,
  marks: null, pendingSelect: null, marked: null,
};
const envName = id => {
  const env = C.envs.find(item => item.id === id);
  const name = env?.name || id || "Sin ambiente";
  return name === id ? String(id).replace(/^env_context_/, "") : name;
};
const sourceOf = id => (G().state?.sources || []).find(item => item.id === id);
const shortSrc = (id, env) => (env && String(id).startsWith(env + ".")) ? String(id).slice(env.length + 1) : String(id || "");
const typeLabel = id => { const s = sourceOf(id); return s ? (G().sourceTypeLabel?.(s.source_type) || s.source_type).toLowerCase() : "fuente"; };
const modOf = id => ({ microphone: "speech", audio: "speech", document: "doc", file: "doc", text: "doc" })[sourceOf(id)?.source_type] || "video";
const kf = (id, i, side) => `${G().base || ""}/api/memory/events/${encodeURIComponent(id)}/keyframes/${i}?max_side=${side}`;
const isSel = (type, id) => !!C.sel && C.sel.type === type && C.sel.id === id;
const visibleEnvIds = () => C.envs.map(e => e.id).filter(id => !C.hidden.has(id));
const envKey = () => C.hidden.size ? visibleEnvIds().join("|") || "∅" : "*";
const labelW = () => parseFloat(token("--cr-label-w")) || 200;
const laneW = () => Math.max(120, $("#cr-stage").clientWidth - labelW());
// Un repintado por fotograma; el temporizador cubre los casos sin fotogramas
// (pestaña en segundo plano, navegador sin interfaz).
let raf = 0, rafTimer = 0;
const runRender = () => { cancelAnimationFrame(raf); clearTimeout(rafTimer); raf = 0; rafTimer = 0; render(); };
const schedule = () => { if (raf || rafTimer) return; raf = requestAnimationFrame(runRender); rafTimer = setTimeout(runRender, 120); };

// -- Carga ----------------------------------------------------------------------------
async function activate() {
  if (!C.bound) bind();
  try { await loadSpan(); }
  catch (error) { banner(`No se pudo cargar la línea temporal: ${error.message}`); }
}

async function loadSpan() {
  const span = await api("/api/timeline/span");
  setTimezone(span.timezone);
  const changed = !C.span || C.span.events !== span.events || C.span.last !== span.last;
  C.span = span;
  C.envs = span.environments || [];
  renderChips();
  if (!span.events) {
    $("#cr-readout").textContent = "Sin eventos todavía";
    $("#cr-rows").innerHTML = '<div class="cr-empty">Todavía no hay eventos en la memoria. Procesa una captura desde Ingesta o Vigilancia.</div>';
    $("#cr-inspector").innerHTML = "";
    return;
  }
  if (changed || !C.pulse) {
    await loadPulse();
    if (C.loaded) C.loaded = null;  // hay capturas nuevas: recargar la ventana
  }
  if (!C.vs) initialView();
  schedule();
  ensureItems(true);
}

async function loadPulse() {
  const first = Date.parse(C.span.first), last = Date.parse(C.span.last);
  let a = first, b = last + 1000;
  const minRange = 30 * 60e3;
  if (b - a < minRange) { const c = (a + b) / 2; a = c - minRange / 2; b = c + minRange / 2; }
  const P = await api(`/api/timeline/density?${new URLSearchParams({ start: iso(a), end: iso(b) })}`);
  P.bs = P.buckets.map(x => Date.parse(x.start));
  P.be = P.buckets.map(x => Date.parse(x.end));
  C.pulse = P;
  C.maxSpan = Math.max(60e3, (P.be[P.be.length - 1] - P.bs[0]) * 1.05);
  drawPulse();
}

function initialView() {
  const P = C.pulse;
  let i = P.totals.length - 1;
  if (P.bucket === "day") {
    while (i > 0 && !P.totals[i]) i--;
    setViewRaw(P.bs[i], P.be[i]);
  } else setViewRaw(P.bs[0], P.be[P.be.length - 1]);
  C.fitPending = true;
}

function setViewRaw(vs, ve) {
  const span = Math.min(C.maxSpan, Math.max(C.minSpan, ve - vs));
  const c = (vs + ve) / 2;
  C.vs = c - span / 2; C.ve = c + span / 2;
}

function setView(vs, ve) {
  const span = Math.min(C.maxSpan, Math.max(C.minSpan, ve - vs));
  let start = (vs + ve) / 2 - span / 2;
  if (C.pulse) {
    const lo = C.pulse.bs[0] - span * 0.25, hi = C.pulse.be[C.pulse.be.length - 1] - span * 0.75;
    if (hi > lo) start = Math.min(hi, Math.max(lo, start));
  }
  C.vs = start; C.ve = start + span;
  schedule();
  ensureItems(false);
}
const zoomBy = k => { const c = (C.vs + C.ve) / 2, s = (C.ve - C.vs) * k; setView(c - s / 2, c + s / 2); };

function needFetch() {
  const L = C.loaded, span = C.ve - C.vs;
  if (envKey() === "∅") return false;
  if (!L || L.envKey !== envKey()) return true;
  if (C.vs < L.ls || C.ve > L.le) return true;
  return L.mode === "clusters" && span < L.span / 3;
}
function ensureItems(now) {
  if (!needFetch()) return;
  clearTimeout(C.timer);
  C.timer = setTimeout(fetchItems, now ? 0 : 180);
}

async function fetchItems() {
  const span = C.ve - C.vs, ls = C.vs - span * 0.5, le = C.ve + span * 0.5;
  C.ctl?.abort();
  const ctl = new AbortController(); C.ctl = ctl;
  const params = new URLSearchParams({ start: iso(ls), end: iso(le), limit: String(Math.round(Math.max(400, Math.min(3000, laneW() * 2)))) });
  if (C.hidden.size) visibleEnvIds().forEach(id => params.append("environment_id", id));
  loading(true);
  try {
    const data = await api(`/api/timeline/items?${params}`, { signal: ctl.signal });
    if (ctl !== C.ctl) return;
    prepare(data);
    C.loaded = { ls, le, span: le - ls, mode: data.mode, data, envKey: envKey() };
    if (C.fitPending && data.mode === "items" && data.events.length) {
      // Primer encuadre: el comienzo de la actividad a una escala en la que los
      // clips muestran sus fotogramas (unas seis veces la duración mediana).
      const durations = data.events.map(e => e.e - e.s).filter(d => d > 0).sort((x, y) => x - y);
      const median = durations.length ? durations[durations.length >> 1] : 5000;
      const a = Math.min(...data.events.map(e => e.s)), b = Math.max(...data.events.map(e => e.e));
      const want = Math.min(b - a, Math.max(30000, median * 6)), margin = Math.max(2000, want * 0.08);
      if (want + 2 * margin < C.ve - C.vs) setViewRaw(a - margin, a + want + margin);
    }
    C.fitPending = false;
    if (C.pendingSelect && data.byId?.get(C.pendingSelect)) { C.sel = { type: "ev", id: C.pendingSelect, item: data.byId.get(C.pendingSelect) }; C.pendingSelect = null; }
    schedule();
    ensureItems(false);
  } catch (error) {
    if (error.name !== "AbortError") banner(`No se pudo cargar la ventana: ${error.message}`);
  } finally {
    if (ctl === C.ctl) loading(false);
  }
}

function prepare(data) {
  const ms = x => { x.s = Date.parse(x.start); x.e = Date.parse(x.end || x.start); return x; };
  (data.episodes || []).forEach(ms);
  if (data.mode === "clusters") {
    const d = data.density;
    d.bs = d.buckets.map(x => Date.parse(x.start));
    d.be = d.buckets.map(x => Date.parse(x.end));
    return;
  }
  data.events.forEach(ms); data.speech.forEach(ms); data.documents.forEach(ms);
  data.byId = new Map(data.events.map(e => [e.id, e]));
  data.speechById = new Map(data.speech.map(o => [o.id, o]));
  const stacks = new Map();
  data.documents.forEach(o => {
    const key = `${o.source_id}@${o.s}`;
    if (!stacks.has(key)) stacks.set(key, { id: key, s: o.s, e: o.s, src: o.source_id, env: o.environment_id, frags: [] });
    stacks.get(key).frags.push(o);
  });
  stacks.forEach(st => st.frags.sort((a, b) => (a.page || 0) - (b.page || 0)));
  data.stacks = [...stacks.values()];
  data.stackById = stacks;
}

// -- Pistas ---------------------------------------------------------------------------
function buildGroups() {
  const L = C.loaded;
  if (!L) return [];
  const D = L.data;
  if (D.mode === "clusters") {
    return visibleEnvIds().map(env => {
      const lanes = D.density.lanes.filter(l => l.environment_id === env);
      const eps = D.episodes.filter(e => e.environment_id === env);
      if (!lanes.length && !eps.length) return null;
      const tracks = lanes.map(l => ({ kind: "cluster", mod: modOf(l.source_id), src: l.source_id, label: shortSrc(l.source_id, env),
        sub: `${typeLabel(l.source_id)} · ${plural(l.total, "evento", "eventos")}`, counts: l.counts }));
      return { env, eps, tracks, n: lanes.reduce((a, l) => a + l.total, 0) };
    }).filter(Boolean);
  }
  return visibleEnvIds().map(env => {
    const evs = D.events.filter(e => e.environment_id === env);
    const eps = D.episodes.filter(e => e.environment_id === env);
    const tracks = [];
    [...new Set(evs.map(e => e.source_id))].sort().forEach(src => {
      const items = evs.filter(e => e.source_id === src);
      tracks.push({ kind: "video", src, label: shortSrc(src, env), sub: `${typeLabel(src)} · ${plural(items.length, "evento", "eventos")}`, items });
    });
    const sp = D.speech.filter(o => o.environment_id === env);
    if (sp.length) tracks.push({ kind: "speech", label: "Habla transcrita", sub: plural(sp.length, "frase", "frases"), items: sp });
    const stacks = D.stacks.filter(st => st.env === env);
    [...new Set(stacks.map(st => st.src))].sort().forEach(src => {
      const items = stacks.filter(st => st.src === src);
      const frags = items.reduce((a, st) => a + st.frags.length, 0);
      tracks.push({ kind: "doc", src, label: shortSrc(src, env), sub: `documento · ${plural(frags, "fragmento", "fragmentos")}`, items });
    });
    if (!tracks.length && !eps.length) return null;
    return { env, eps, tracks, n: evs.length };
  }).filter(Boolean);
}

const STEPS = [100, 200, 500, 1e3, 2e3, 5e3, 1e4, 15e3, 3e4, 6e4, 12e4, 3e5, 6e5, 9e5, 18e5, 36e5, 72e5, 108e5, 216e5, 432e5, 864e5, 1728e5, 6048e5];
function render() {
  renderChips();
  drawViewBox();
  $("#cr-span").textContent = dur(C.ve - C.vs);
  if (C.mode === "story") { renderStory(); renderInspector(); return; }
  const W = laneW(), span = C.ve - C.vs, X = t => (t - C.vs) / span * W;
  const step = STEPS.find(s => s / span * W >= 92) || STEPS[STEPS.length - 1];
  const minor = step >= 36e5 ? step / 4 : step / 5;
  const off = tzOffset(C.vs);
  const label = t => step >= 864e5 ? F.dayShort.format(new Date(t)) : step >= 36e5 && span > 864e5 ? F.dayHour.format(new Date(t)) : step >= 6e4 ? F.hm.format(new Date(t)) : clock(t, step < 1000);
  let ruler = "", grid = "";
  for (let t = Math.ceil((C.vs + off) / minor) * minor - off; t <= C.ve; t += minor) {
    const x = X(t).toFixed(1), major = Math.abs(((t + off) % step + step) % step) < 1;
    ruler += `<i class="${major ? "" : "minor"}" style="left:${x}px"></i>`;
    grid += `<i class="${major ? "" : "minor"}" style="left:${x}px"></i>`;
    if (major && +x > 30 && +x < W - 30) ruler += `<span style="left:${x}px">${label(t)}</span>`;
  }
  const marked = new Set();
  let bands = "";
  if (C.marks) {
    // Las banderas que caen casi en el mismo sitio se juntan en una («2·3»).
    const flags = [];
    C.marks.items.forEach((m, i) => {
      if (m.eventId) marked.add(m.eventId);
      const x1 = X(m.s), x2 = X(m.e);
      if (x2 < -4 || x1 > W + 4) return;
      const active = i === C.marks.index;
      bands += `<i class="cr-band${active ? " active" : ""}" style="left:${x1.toFixed(1)}px;width:${Math.max(3, x2 - x1).toFixed(1)}px"></i>`;
      flags.push({ x: Math.max(10, Math.min(W - 10, x1)), marks: [[i, m]], active });
    });
    flags.sort((a, b) => a.x - b.x);
    const merged = [];
    flags.forEach(f => {
      const last = merged[merged.length - 1];
      if (last && f.x - last.x < 22) { last.marks.push(...f.marks); last.active ||= f.active; }
      else merged.push(f);
    });
    merged.forEach(f => {
      ruler += `<button type="button" class="cr-flag${f.active ? " active" : ""}" data-mark-go="${f.marks[0][0]}" style="left:${f.x.toFixed(1)}px" title="${esc(f.marks.map(([, m]) => `${m.n}. ${m.label}`).join(" · "))}">${f.marks.map(([, m]) => m.n).join("·")}</button>`;
    });
  }
  C.marked = marked;
  $("#cr-ruler").innerHTML = ruler;
  $("#cr-grid").innerHTML = grid;
  $("#cr-marklayer").innerHTML = bands;
  $("#cr-day").textContent = F.day.format(new Date(C.vs + span / 2));
  const L = C.loaded;
  const bannerEl = $("#cr-banner");
  if (L?.mode === "clusters") {
    const bucket = { minute: "minutos", hour: "horas", day: "días" }[L.data.density.bucket] || L.data.density.bucket;
    bannerEl.textContent = `En este intervalo hay ${plural(L.data.events_total, "evento", "eventos")}: se muestran agrupados por ${bucket}. Acerca el zoom o haz clic en una barra para ver los clips.`;
    bannerEl.hidden = false;
  } else if (!bannerEl.dataset.error) bannerEl.hidden = true;
  const groups = buildGroups();
  let html = "";
  for (const g of groups) {
    const open = !C.collapsed.has(g.env);
    const ticks = open || L.mode === "clusters" ? "" : g.tracks.filter(t => t.kind === "video").flatMap(t => t.items).map(e => {
      const x1 = X(e.s), x2 = X(e.e);
      return x2 < 0 || x1 > W ? "" : `<i class="cr-minev" style="left:${x1.toFixed(1)}px;width:${Math.max(2, x2 - x1).toFixed(1)}px"></i>`;
    }).join("");
    html += `<div class="cr-row cr-grp"><button type="button" class="cr-lab" data-toggle="${esc(g.env)}" aria-expanded="${open}" title="${open ? "Plegar" : "Desplegar"} ${esc(g.env)}"><b><span class="cr-caret"></span>${esc(envName(g.env))}</b><small>${plural(g.n, "evento", "eventos")} · ${plural(g.tracks.length, "pista", "pistas")}</small></button><div class="cr-lane">${g.eps.map(p => epBar(p, X, W)).join("")}${ticks}</div></div>`;
    if (open) for (const t of g.tracks) html += trackRow(t, X, W);
  }
  $("#cr-rows").innerHTML = html || `<div class="cr-empty">${L ? (envKey() === "∅" ? "No hay ambientes visibles: actívalos en la barra de arriba." : "No hay nada en este intervalo. Muévete o elige otro en el pulso.") : "Cargando…"}</div>`;
  drawMini();
  renderInspector();
}

function epBar(p, X, W) {
  const x1 = X(p.s), x2 = X(p.e);
  if (x2 < -10 || x1 > W + 10) return "";
  const w = Math.max(6, x2 - x1), text = `${clock(p.s)} · ${dur(p.e - p.s)}`;
  return `<button type="button" class="cr-ep" data-ep="${esc(p.id)}" style="left:${x1.toFixed(1)}px;width:${w.toFixed(1)}px" title="Episodio ${esc(p.external_id || p.title || "")} · ${esc(text)}. Clic para encuadrarlo">${w > 80 ? "Episodio · " + esc(text) : ""}</button>`;
}

function trackRow(t, X, W) {
  const mod = t.kind === "cluster" ? t.mod : t.kind;
  const head = `<div class="cr-lab" title="${esc(t.src || t.label)}"><b><span class="cr-mod ${mod}"></span>${esc(t.label)}</b><small>${esc(t.sub)}</small></div>`;
  if (t.kind === "video") return `<div class="cr-row cr-r-video">${head}<div class="cr-lane">${t.items.map(e => clip(e, X, W)).join("")}</div></div>`;
  if (t.kind === "doc") return `<div class="cr-row cr-r-doc">${head}<div class="cr-lane">${t.items.map(d => docMark(d, X, W)).join("")}</div></div>`;
  if (t.kind === "cluster") return `<div class="cr-row cr-r-cluster">${head}<div class="cr-lane">${bins(t, X, W)}</div></div>`;
  const { placed, rows } = packSpeech(t.items, X, W);
  return `<div class="cr-row cr-r-speech">${head}<div class="cr-lane" style="height:${rows * 28 + 8}px">${placed.map(pill).join("")}</div></div>`;
}

function clip(e, X, W) {
  const x1 = X(e.s), x2 = X(e.e);
  if (x2 < -60 || x1 > W + 60) return "";
  const w = Math.max(3, x2 - x1), sel = (isSel("ev", e.id) ? " cr-sel" : "") + (C.marked?.has(e.id) ? " cr-marked" : "");
  const pos = `left:${x1.toFixed(1)}px;width:${w.toFixed(1)}px`, title = esc(`${clock(e.s)} · ${e.title}`);
  if (w < 22 || !e.keyframes) return `<button type="button" class="cr-clip${w < 22 ? " tick" : ""}${sel}" data-ev="${esc(e.id)}" style="${pos}" title="${title}" aria-label="${title}">${w >= 64 ? `<span class="cr-cap">${esc(e.title)}</span>` : ""}</button>`;
  const n = Math.max(1, Math.round(w / 72));
  let film = "";
  for (let i = 0; i < n; i++) film += `<span style="background-image:url('${kf(e.id, Math.min(e.keyframes - 1, Math.floor(i * e.keyframes / n)), 160)}')"></span>`;
  const capPad = Math.max(0, -x1) + 6;
  return `<button type="button" class="cr-clip${sel}" data-ev="${esc(e.id)}" style="${pos}" title="${title}" aria-label="${title}"><span class="cr-film">${film}</span>${w > 64 ? `<span class="cr-cap" style="padding-left:${capPad.toFixed(0)}px">${esc(e.title)}</span>` : ""}${e.speech?.length ? '<i class="cr-spk"></i>' : ""}</button>`;
}

// Las frases se apilan en filas para que ninguna tape a otra.
function packSpeech(items, X, W) {
  const ends = [], placed = [];
  for (const o of items) {
    const x1 = X(o.s), xd = X(o.e);
    const x2 = Math.max(xd, x1 + Math.min(280, 30 + (o.text || "").length * 6.2));
    if (x2 < -20 || x1 > W + 20) continue;
    let r = ends.findIndex(end => end + 6 <= x1);
    if (r < 0) { r = ends.length; ends.push(x2); } else ends[r] = x2;
    placed.push({ o, x1, w: x2 - x1, dw: Math.max(2, xd - x1), r });
  }
  return { placed, rows: Math.max(1, ends.length) };
}
const pill = p => `<button type="button" class="cr-pill${isSel("sp", p.o.id) ? " cr-sel" : ""}" data-sp="${esc(p.o.id)}" style="left:${p.x1.toFixed(1)}px;top:${4 + p.r * 28}px;width:${p.w.toFixed(1)}px" title="${esc(p.o.text)}"><span class="cr-dur" style="width:${Math.min(p.w, p.dw).toFixed(1)}px"></span><span class="cr-q">“${esc(p.o.text)}”</span></button>`;

function docMark(d, X, W) {
  const x = X(d.s);
  if (x < -200 || x > W + 10) return "";
  const pages = d.frags.map(f => f.page).filter(p => p != null);
  const lo = Math.min(...pages), hi = Math.max(...pages);
  const range = pages.length ? (lo === hi ? `pág. ${lo}` : `pág. ${lo}–${hi}`) : "";
  return `<button type="button" class="cr-docmark${isSel("doc", d.id) ? " cr-sel" : ""}" data-doc="${esc(d.id)}" style="left:${x.toFixed(1)}px" title="${plural(d.frags.length, "fragmento", "fragmentos")}"><span class="cr-pages">${d.frags.length}</span>${esc(range)}</button>`;
}

function bins(t, X, W) {
  const d = C.loaded.data.density, max = Math.max(1, d.max);
  let html = "";
  t.counts.forEach((n, i) => {
    if (!n) return;
    const x1 = X(d.bs[i]), x2 = X(d.be[i]);
    if (x2 < 0 || x1 > W) return;
    const h = Math.max(3, 36 * Math.sqrt(n / max));
    html += `<button type="button" class="cr-bin ${t.mod}" data-zoom="${d.bs[i]}|${d.be[i]}" style="left:${x1.toFixed(1)}px;width:${Math.max(2, x2 - x1 - 1).toFixed(1)}px;height:${h.toFixed(0)}px" title="${esc(`${clock(d.bs[i])} – ${clock(d.be[i])} · ${plural(n, "evento", "eventos")}`)}"></button>`;
  });
  return html;
}

// -- Minimapa: el día alrededor de la vista (o toda la memoria si la vista es mayor) ----
function miniExtent() {
  const span = C.ve - C.vs;
  if (span <= 12 * 36e5) {
    const d0 = localDayStart((C.vs + C.ve) / 2);
    return { a: d0, b: localDayStart(d0 + 30 * 36e5), label: F.day.format(new Date(d0 + 12 * 36e5)), day: true };
  }
  const P = C.pulse;
  return { a: P.bs[0], b: P.be[P.be.length - 1], label: "Toda la memoria", day: false };
}
async function loadMini(ext, key) {
  C.miniCtl?.abort();
  const ctl = new AbortController(); C.miniCtl = ctl;
  try {
    const params = new URLSearchParams({ start: iso(ext.a), end: iso(ext.b), bucket: "minute" });
    const d = await api(`/api/timeline/density?${params}`, { signal: ctl.signal });
    d.bs = d.buckets.map(x => Date.parse(x.start)); d.be = d.buckets.map(x => Date.parse(x.end));
    C.mini = { key, d };
    drawMini();
  } catch (error) { if (error.name !== "AbortError") console.error(error); }
}
function drawMini() {
  if (!C.pulse) return;
  const ext = miniExtent(), key = `${ext.a}|${ext.b}`;
  let d = null;
  if (!ext.day) d = C.pulse;
  else if (C.mini?.key === key) d = C.mini.d;
  else if (C.miniKey !== key) { C.miniKey = key; loadMini(ext, key); }
  const wrap = $("#cr-mini"), c = $("#cr-mini-canvas"), w = wrap.clientWidth, h = wrap.clientHeight, dpr = window.devicePixelRatio || 1;
  if (!w) return;
  c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  const XF = t => (t - ext.a) / (ext.b - ext.a) * w;
  if (d) {
    const rows = d.lanes.filter(l => !C.hidden.has(l.environment_id));
    const rowH = Math.max(2, Math.min(8, (h - 6) / Math.max(1, rows.length)));
    const max = Math.max(1, ...rows.map(l => Math.max(...l.counts)));
    g.fillStyle = token("--cr-video");
    rows.forEach((l, r) => l.counts.forEach((n, i) => {
      if (!n) return;
      g.globalAlpha = 0.35 + 0.65 * Math.sqrt(n / max);
      const x = XF(d.bs[i]);
      g.fillRect(x, 3 + r * rowH, Math.max(1.5, XF(d.be[i]) - x), Math.max(1.5, rowH - 1.5));
    }));
    g.globalAlpha = 1;
  }
  if (C.marks) {
    g.fillStyle = token("--cr-sel");
    C.marks.items.forEach(m => { const x = XF(m.s); if (x >= -2 && x <= w + 2) g.fillRect(x - 1, 0, 2, h); });
  }
  const win = $("#cr-mini-win"), a = Math.max(0, XF(C.vs)), b = Math.min(w, XF(C.ve));
  win.style.left = `${a - 1}px`;
  win.style.width = `${Math.max(6, b - a + 2)}px`;
  $("#cr-mini-label").textContent = ext.label;
}
function fitExtent() {
  const ext = miniExtent();
  const d = ext.day ? (C.mini?.key === `${ext.a}|${ext.b}` ? C.mini.d : null) : C.pulse;
  let a = ext.a, b = ext.b;
  if (d) {
    const active = d.totals.map((n, i) => n ? i : -1).filter(i => i >= 0);
    if (active.length) { a = d.bs[active[0]]; b = d.be[active[active.length - 1]]; }
  }
  const margin = Math.max(2000, (b - a) * 0.04);
  setView(a - margin, b + margin);
}

// -- Pulso ----------------------------------------------------------------------------
const PH = { hist: 40, gap: 8, cell: 10, cg: 1, axis: 18 };
let pg = null, pdrag = null;
function drawPulse() {
  const P = C.pulse;
  if (!P) return;
  const wrap = $("#cr-pulse-wrap"), c = $("#cr-pulse"), w = wrap.clientWidth, dpr = window.devicePixelRatio || 1;
  if (!w) return;
  const ml = w < 560 ? 44 : 92, rows = P.lanes, gridH = rows.length * (PH.cell + PH.cg), h = PH.hist + PH.gap + gridH + PH.axis;
  c.style.height = `${h}px`; c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  const n = P.buckets.length, cw = (w - ml) / n, gap = cw > 3 ? 1 : 0, top = PH.hist + PH.gap;
  pg = { ml, cw, top, gridH, w };
  const rgb = hex => { const x = hex.replace("#", ""); return [0, 2, 4].map(i => parseInt(x.slice(i, i + 2), 16)); };
  const h0 = rgb(token("--cr-heat0")), h1 = rgb(token("--cr-heat1"));
  const ramp = Array.from({ length: 33 }, (_, i) => `rgb(${h0.map((v, k) => Math.round(v + (h1[k] - v) * i / 32)).join(",")})`);
  const maxTot = Math.max(1, ...P.totals);
  g.fillStyle = token("--cr-video");
  P.totals.forEach((v, i) => { const bh = v / maxTot * (PH.hist - 6); g.fillRect(ml + i * cw, PH.hist - bh, Math.max(1, cw - gap), bh); });
  const max = Math.max(1, P.max);
  rows.forEach((lane, r) => {
    const y = top + r * (PH.cell + PH.cg);
    g.globalAlpha = C.hidden.has(lane.environment_id) ? 0.25 : 1;
    lane.counts.forEach((v, i) => { g.fillStyle = ramp[Math.round(Math.sqrt(v / max) * 32)]; g.fillRect(ml + i * cw, y, Math.max(1, cw - gap), PH.cell); });
  });
  g.globalAlpha = 1;
  g.fillStyle = token("--muted") || "#696b62";
  g.font = `10px ${getComputedStyle(document.body).getPropertyValue("--mono") || "monospace"}`;
  g.textBaseline = "middle";
  g.fillText("total", 0, PH.hist - 8);
  rows.forEach((lane, r) => { if (ml > 50 || r % 2 === 0) g.fillText(envName(lane.environment_id).slice(0, ml > 50 ? 14 : 5), 0, top + r * (PH.cell + PH.cg) + PH.cell / 2); });
  g.textBaseline = "alphabetic";
  const tickEvery = { day: k => k.endsWith("-01"), hour: k => k.endsWith("T00"), minute: k => k.endsWith(":00") }[P.bucket] || (() => false);
  const tickLabel = t => P.bucket === "day" ? new Intl.DateTimeFormat("es-ES", { month: "short", timeZone: P.timezone }).format(new Date(t)) : P.bucket === "hour" ? F.dayShort.format(new Date(t)) : F.hm.format(new Date(t));
  let lastX = -999;
  P.buckets.forEach((b, i) => {
    if (!tickEvery(b.key)) return;
    const x = ml + i * cw;
    g.fillRect(x, top + gridH, 1, 5);
    if (x - lastX > 44) { g.fillText(tickLabel(P.bs[i]), x + 3, top + gridH + PH.axis - 3); lastX = x; }
  });
  if (C.marks) {
    g.fillStyle = token("--cr-sel");
    C.marks.items.forEach((m, i) => { const x = pulseX(m.s); g.globalAlpha = i === C.marks.index ? 1 : 0.7; g.fillRect(x - 1, 0, 2, top + gridH); });
    g.globalAlpha = 1;
  }
  const sub = { day: "por día", hour: "por hora", minute: "por minuto" }[P.bucket];
  $("#cr-pulse-sub").textContent = `Eventos ${sub} y ambiente, de ${F.day.format(new Date(P.bs[0]))} a ${F.day.format(new Date(P.be[P.be.length - 1] - 1))}.`;
  readout(null);
  drawViewBox();
}
const bucketAt = x => Math.max(0, Math.min(C.pulse.buckets.length - 1, Math.floor((x - pg.ml) / pg.cw)));
function pulseX(t) {
  const P = C.pulse;
  if (t <= P.bs[0]) return pg.ml;
  if (t >= P.be[P.be.length - 1]) return pg.ml + P.buckets.length * pg.cw;
  let lo = 0, hi = P.bs.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (P.bs[mid] <= t) lo = mid; else hi = mid - 1; }
  return pg.ml + (lo + (t - P.bs[lo]) / (P.be[lo] - P.bs[lo])) * pg.cw;
}
function drawViewBox(range) {
  const box = $("#cr-view-box");
  if (!pg || !C.pulse || (!range && !C.vs)) { box.hidden = true; return; }
  const [a, b] = range ? [pg.ml + range[0] * pg.cw, pg.ml + (range[1] + 1) * pg.cw] : [pulseX(C.vs), pulseX(C.ve)];
  box.hidden = false;
  box.style.left = `${a}px`; box.style.width = `${Math.max(3, b - a)}px`; box.style.height = `${pg.top + pg.gridH}px`;
}
function readout(range) {
  const P = C.pulse, el = $("#cr-readout");
  if (!range) {
    el.innerHTML = `<b>${plural(C.span.events, "evento", "eventos")}</b> en ${plural(C.envs.length, "ambiente", "ambientes")}<br>${esc(F.day.format(new Date(Date.parse(C.span.first))))} – ${esc(F.day.format(new Date(Date.parse(C.span.last))))}`;
    return;
  }
  const [a, z] = range;
  let sum = 0, active = 0;
  for (let i = a; i <= z; i++) { sum += P.totals[i]; if (P.totals[i]) active++; }
  const unit = { day: ["día", "días"], hour: ["hora", "horas"], minute: ["minuto", "minutos"] }[P.bucket];
  el.innerHTML = `<b>${esc(F.dayHour.format(new Date(P.bs[a])))} – ${esc(F.dayHour.format(new Date(P.be[z])))}</b><br>${plural(sum, "evento", "eventos")} · ${plural(active, unit[0], unit[1])} con actividad`;
}

// -- Relato e inspector ----------------------------------------------------------------
function inView(x) { return x.e >= C.vs && x.s <= C.ve && !C.hidden.has(x.environment_id); }
function renderStory() {
  const L = C.loaded, box = $("#cr-story");
  if (!L) { box.innerHTML = '<div class="cr-empty">Cargando…</div>'; return; }
  if (L.mode === "clusters") { box.innerHTML = `<div class="cr-empty">En este intervalo hay ${plural(L.data.events_total, "evento", "eventos")}. Elige un intervalo menor en el pulso o acerca el zoom en Pistas para leerlos aquí.</div>`; return; }
  const evs = L.data.events.filter(inView).sort((a, b) => a.s - b.s || String(a.environment_id).localeCompare(b.environment_id));
  const shown = evs.slice(0, 300), beats = new Map();
  shown.forEach(e => { const k = Math.floor(e.s / 1000); if (!beats.has(k)) beats.set(k, []); beats.get(k).push(e); });
  box.innerHTML = [...beats.entries()].map(([k, list]) => `<section class="cr-beat"><header><b>${clock(k * 1000)}</b><span>${list.length === 1 ? "1 evento" : `${list.length} eventos a la vez`} · ${esc([...new Set(list.map(e => envName(e.environment_id)))].join(", "))}</span></header><div class="cr-cards">${list.map(card).join("")}</div></section>`).join("")
    + (evs.length > shown.length ? `<p class="cr-note">Se muestran los primeros 300 de ${num(evs.length)}. Acerca el zoom para ver el resto.</p>` : "")
    || '<div class="cr-empty">No hay eventos en este intervalo.</div>';
}
const card = e => `<button type="button" class="cr-card${isSel("ev", e.id) ? " cr-sel" : ""}${C.marks?.items.some(m => m.eventId === e.id) ? " cr-marked" : ""}" data-ev="${esc(e.id)}"><span class="cr-thumb" style="${e.keyframes ? `background-image:url('${kf(e.id, 0, 200)}')` : ""}"></span><span class="cr-body2"><span class="cr-tag">${esc(envName(e.environment_id))} · ${esc(shortSrc(e.source_id, e.environment_id))} · ${dur(e.e - e.s)}</span><b>${esc(e.title)}</b>${e.speech?.[0] ? `<span class="cr-quote">“${esc(e.speech[0])}”</span>` : ""}${e.actions?.length ? `<span class="cr-chips">${e.actions.slice(0, 3).map(a => `<span class="cr-chip">${esc(a)}</span>`).join("")}</span>` : ""}</span></button>`;

let inspectorKey = "";
function renderInspector() {
  const s = C.sel, D = C.loaded?.data;
  const key = s ? `${s.type}:${s.id}:${C.loaded?.ls}` : `view:${Math.round(C.vs / 1000)}:${Math.round(C.ve / 1000)}:${envKey()}:${C.loaded?.ls}`;
  if (key === inspectorKey) return;
  inspectorKey = key;
  const box = $("#cr-inspector");
  if (s?.type === "ev" && s.item) box.innerHTML = inspectEvent(s.item);
  else if (s?.type === "sp" && s.item) box.innerHTML = inspectSpeech(s.item);
  else if (s?.type === "doc" && s.item) box.innerHTML = inspectDoc(s.item);
  else box.innerHTML = D ? inspectView(D) : "";
}
const facet = (title, list, cls = "") => list?.length ? `<div class="cr-facet"><h4>${title}</h4><div class="cr-chips">${list.map(v => `<span class="cr-chip ${cls}">${esc(v)}</span>`).join("")}</div></div>` : "";
function inspectEvent(e) {
  const D = C.loaded?.data, siblings = D?.events ? D.events.filter(x => x.source_id === e.source_id).sort((a, b) => a.s - b.s) : [];
  const i = siblings.findIndex(x => x.id === e.id), prev = siblings[i - 1], next = siblings[i + 1];
  const off = e.start_offset_seconds != null ? `en el vídeo ${mmss(e.start_offset_seconds)}–${mmss(e.end_offset_seconds ?? e.start_offset_seconds)}` : "";
  const shots = Array.from({ length: Math.min(3, e.keyframes || 0) }, (_, k) => `<span style="background-image:url('${kf(e.id, Math.floor(k * e.keyframes / Math.min(3, e.keyframes)), 360)}')"></span>`).join("");
  return `<p class="cr-kicker">Evento · ${esc(envName(e.environment_id))} · ${esc(typeLabel(e.source_id))}</p>
    ${shots ? `<div class="cr-shots">${shots}</div>` : ""}
    <div class="cr-when"><b>${clock(e.s, true)} → ${clock(e.e, true)}</b><small>${esc(F.day.format(new Date(e.s)))} · ${dur(e.e - e.s)}${off ? " · " + off : ""} · ${esc(shortSrc(e.source_id, e.environment_id))}</small></div>
    <h3>${esc(e.title)}</h3>${e.summary ? `<p>${esc(e.summary)}</p>` : ""}
    ${e.speech?.length ? `<div class="cr-facet"><h4>Habla</h4>${e.speech.map(q => `<blockquote>“${esc(q)}”</blockquote>`).join("")}</div>` : ""}
    ${facet("Acciones", e.actions)}${facet("Actores", e.actors)}${facet("Objetos", e.objects)}${facet("Texto visible", e.visible_text, "txt")}
    <div class="cr-nav2"><button type="button" class="cr-btn" data-go="${prev ? esc(prev.id) : ""}"${prev ? "" : " disabled"}>← Anterior</button><button type="button" class="cr-btn" data-go="${next ? esc(next.id) : ""}"${next ? "" : " disabled"}>Siguiente →</button><button type="button" class="cr-btn primary" data-open="${esc(e.id)}"${e.episode_id ? "" : " disabled title=\"Este evento no pertenece a ningún episodio\""}>Abrir en el episodio</button></div>`;
}
function inspectSpeech(o) {
  const near = (C.loaded?.data?.events || []).filter(e => e.environment_id === o.environment_id && e.s <= o.e && e.e >= o.s);
  return `<p class="cr-kicker">Habla · ${esc(envName(o.environment_id))}</p><blockquote style="font-size:17px">“${esc(o.text)}”</blockquote>
    <div class="cr-when"><b>${clock(o.s, true)} → ${clock(o.e, true)}</b><small>${dur(o.e - o.s)} · ${esc(shortSrc(o.source_id, o.environment_id))}</small></div>
    ${near.length ? `<div class="cr-facet"><h4>Se ve mientras tanto</h4>${near.map(e => `<button type="button" class="cr-link" data-go="${esc(e.id)}">${clock(e.s)} · ${esc(e.title)}</button>`).join("")}</div>` : ""}`;
}
function inspectDoc(d) {
  return `<p class="cr-kicker">Documento · ${esc(envName(d.env))}</p><h3>${esc(shortSrc(d.src, d.env))}</h3>
    <div class="cr-when"><b>${clock(d.s)}</b><small>${plural(d.frags.length, "fragmento indexado", "fragmentos indexados")}</small></div>
    ${d.frags.map(f => `<div class="cr-frag"><small>${f.page != null ? "pág. " + f.page : "sin página"}</small>${esc(f.text)}</div>`).join("")}`;
}
function inspectView(D) {
  if (D.mode === "clusters") {
    return `<p class="cr-kicker">En pantalla</p><h3>${esc(F.dayHour.format(new Date(C.vs)))} – ${esc(F.dayHour.format(new Date(C.ve)))}</h3>
      <div class="cr-stats"><div><b>${num(D.events_total)}</b><span>eventos en la ventana cargada</span></div><div><b>${num(D.episodes.length)}</b><span>episodios</span></div></div>
      <p class="cr-note">Acerca el zoom o haz clic en una barra para ver los clips de ese tramo.</p>`;
  }
  const evs = D.events.filter(inView), sp = D.speech.filter(inView), dc = D.documents.filter(inView);
  return `<p class="cr-kicker">En pantalla</p><h3 class="cr-mono">${clock(C.vs)} – ${clock(C.ve)}</h3>
    <div class="cr-stats"><div><b>${evs.length}</b><span>eventos</span></div><div><b>${new Set(evs.map(e => e.environment_id)).size}</b><span>ambientes</span></div><div><b>${sp.length}</b><span>frases</span></div><div><b>${dc.length}</b><span>fragmentos de documento</span></div></div>
    <p class="cr-note">Haz clic en un clip, una frase o un documento para ver su detalle.</p>`;
}

function renderChips() {
  const box = $("#cr-envs");
  const key = C.envs.map(e => `${e.id}:${e.events}`).join("|");
  if (box.dataset.key !== key) {
    box.innerHTML = C.envs.map(e => `<button type="button" class="cr-env" data-env="${esc(e.id)}" aria-pressed="true" title="Mostrar u ocultar ${esc(e.name || e.id)}">${esc(envName(e.id))}<small>${num(e.events)}</small></button>`).join("");
    box.dataset.key = key;
  }
  box.querySelectorAll("[data-env]").forEach(b => b.setAttribute("aria-pressed", String(!C.hidden.has(b.dataset.env))));
}

function select(type, item) { C.sel = { type, id: item.id, item }; schedule(); }
function setMode(mode) {
  C.mode = mode;
  $("#cr-mode-tracks").setAttribute("aria-pressed", String(mode === "tracks"));
  $("#cr-mode-story").setAttribute("aria-pressed", String(mode === "story"));
  $("#cr-tracks-view").hidden = mode !== "tracks";
  $("#cr-story").hidden = mode !== "story";
  schedule();
}
function loading(on) { $("#cr-loading").hidden = !on; }
function banner(text) {
  const el = $("#cr-banner");
  el.textContent = text; el.hidden = false; el.dataset.error = "1";
  setTimeout(() => { delete el.dataset.error; }, 6000);
}

// -- Marcas desde Consultas ---------------------------------------------------------------
// window.EpiGraphChrono.focus({ title, marks: [{start, end, environment_id, event_id, label, kind}], index })
// abre la línea temporal con esos momentos marcados; index -1 encuadra todos.
function focus({ title, marks, index = -1 }) {
  const items = (marks || []).map((m, i) => ({
    n: i + 1, s: Date.parse(m.start), e: Date.parse(m.end || m.start), env: m.environment_id || null,
    eventId: m.event_id || null, label: m.label || "", kind: m.kind || "",
  })).filter(m => !Number.isNaN(m.s));
  if (!items.length) return false;
  items.forEach(m => { if (Number.isNaN(m.e) || m.e < m.s) m.e = m.s; if (m.env) C.hidden.delete(m.env); });
  C.marks = { title: title || "Momentos", items, index: index >= 0 ? Math.min(index, items.length - 1) : -1 };
  C.sel = null;
  frameMarks();
  renderMarkbar();
  if (C.pulse) drawPulse();
  if (location.hash !== "#cronologia") location.hash = "cronologia";
  else activate();
  return true;
}
function frameMarks() {
  const M = C.marks;
  let a, b;
  if (M.index >= 0) {
    const m = M.items[M.index], span = Math.max(60e3, Math.max(1000, m.e - m.s) * 6);
    a = m.s - span * 0.3; b = a + span;
    C.pendingSelect = m.eventId;
    const ev = m.eventId && C.loaded?.data?.byId?.get(m.eventId);
    if (ev) { C.sel = { type: "ev", id: ev.id, item: ev }; C.pendingSelect = null; }
  } else {
    a = Math.min(...M.items.map(m => m.s)); b = Math.max(...M.items.map(m => m.e));
    const margin = Math.max(5000, (b - a) * 0.06);
    a -= margin; b += margin;
    C.pendingSelect = null;
  }
  C.fitPending = false;
  if (C.pulse) setView(a, b); else setViewRaw(a, b);
}
function renderMarkbar() {
  const bar = $("#cr-markbar"), M = C.marks;
  if (!M) { bar.hidden = true; bar.innerHTML = ""; return; }
  const current = M.index >= 0 ? M.items[M.index] : null;
  bar.hidden = false;
  bar.innerHTML = `<b>${esc(M.title)}</b><span class="cr-count">${current ? `momento ${current.n} de ${M.items.length}` : plural(M.items.length, "momento marcado", "momentos marcados")}</span>`
    + `<span class="cr-label">${current ? esc(`${clock(current.s)} · ${current.label}`) : ""}</span>`
    + `<button type="button" data-mark="prev" aria-label="Momento anterior">←</button><button type="button" data-mark="next" aria-label="Momento siguiente">→</button>`
    + `<button type="button" data-mark="all">Ver todos</button><button type="button" data-mark="clear">Quitar marcas</button>`;
}
function goToMark(index) {
  if (!C.marks) return;
  C.marks.index = index;
  frameMarks(); renderMarkbar(); drawPulse(); schedule();
}

// -- Interacción ----------------------------------------------------------------------
function bind() {
  C.bound = true;
  const stage = $("#cr-stage"), laneX = e => e.clientX - stage.getBoundingClientRect().left - labelW();
  $("#cr-mode-tracks").addEventListener("click", () => setMode("tracks"));
  $("#cr-mode-story").addEventListener("click", () => setMode("story"));
  $("#cr-zoom-in").addEventListener("click", () => zoomBy(0.6));
  $("#cr-zoom-out").addEventListener("click", () => zoomBy(1 / 0.6));
  $("#cr-fit").addEventListener("click", fitExtent);
  $("#cr-envs").addEventListener("click", e => {
    const b = e.target.closest("[data-env]");
    if (!b) return;
    if (C.hidden.has(b.dataset.env)) C.hidden.delete(b.dataset.env); else C.hidden.add(b.dataset.env);
    drawPulse(); schedule(); ensureItems(true);
  });
  $("#cr-markbar").addEventListener("click", e => {
    const b = e.target.closest("[data-mark]");
    if (!b || !C.marks) return;
    const n = C.marks.items.length, i = C.marks.index;
    if (b.dataset.mark === "prev") goToMark(i <= 0 ? n - 1 : i - 1);
    else if (b.dataset.mark === "next") goToMark(i < 0 || i >= n - 1 ? 0 : i + 1);
    else if (b.dataset.mark === "all") goToMark(-1);
    else { C.marks = null; C.pendingSelect = null; renderMarkbar(); drawPulse(); schedule(); }
  });
  $("#cr-ruler").addEventListener("click", e => {
    const b = e.target.closest("[data-mark-go]");
    if (b) goToMark(Number(b.dataset.markGo));
  });
  $("#cr-rows").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    const D = C.loaded?.data;
    if (b.dataset.toggle) {
      if (C.collapsed.has(b.dataset.toggle)) C.collapsed.delete(b.dataset.toggle); else C.collapsed.add(b.dataset.toggle);
      schedule();
    } else if (b.dataset.ep) {
      const p = D?.episodes.find(x => x.id === b.dataset.ep);
      if (p) { const margin = Math.max(1500, (p.e - p.s) * 0.12); setView(p.s - margin, p.e + margin); }
    } else if (b.dataset.zoom) {
      const [a, z] = b.dataset.zoom.split("|").map(Number);
      setView(a, z);
    } else if (b.dataset.ev && D?.byId?.get(b.dataset.ev)) select("ev", D.byId.get(b.dataset.ev));
    else if (b.dataset.sp && D?.speechById?.get(b.dataset.sp)) select("sp", D.speechById.get(b.dataset.sp));
    else if (b.dataset.doc && D?.stackById?.get(b.dataset.doc)) select("doc", D.stackById.get(b.dataset.doc));
  });
  $("#cr-story").addEventListener("click", e => {
    const b = e.target.closest("[data-ev]"), ev = b && C.loaded?.data?.byId?.get(b.dataset.ev);
    if (ev) select("ev", ev);
  });
  $("#cr-inspector").addEventListener("click", e => {
    const go = e.target.closest("[data-go]");
    if (go?.dataset.go) {
      const ev = C.loaded?.data?.byId?.get(go.dataset.go);
      if (!ev) return;
      select("ev", ev);
      if (ev.e < C.vs || ev.s > C.ve) { const span = C.ve - C.vs, c = (ev.s + ev.e) / 2; setView(c - span / 2, c + span / 2); }
      return;
    }
    const open = e.target.closest("[data-open]");
    if (open && C.sel?.item?.episode_id) {
      const ev = C.sel.item;
      G().openEpisode(ev.episode_id, { observationId: ev.observation_id || null, seek: ev.start_offset_seconds ?? null });
    }
  });

  let drag = null, suppress = false;
  stage.addEventListener("pointerdown", e => {
    if (e.button !== 0 || laneX(e) < 0) return;
    drag = { x: e.clientX, vs: C.vs, ve: C.ve, moved: false, id: e.pointerId };
  });
  stage.addEventListener("pointermove", e => {
    const lx = laneX(e), ph = $("#cr-playhead");
    if (lx < 0 || lx > laneW()) ph.hidden = true;
    else { ph.hidden = false; ph.style.left = `${labelW() + lx}px`; ph.firstElementChild.textContent = clock(C.vs + lx / laneW() * (C.ve - C.vs), true); }
    if (!drag) return;
    const dx = e.clientX - drag.x;
    if (!drag.moved && Math.abs(dx) > 4) { drag.moved = true; stage.setPointerCapture(drag.id); stage.classList.add("dragging"); }
    if (drag.moved) { const dt = -dx / laneW() * (drag.ve - drag.vs); setView(drag.vs + dt, drag.ve + dt); }
  });
  const endDrag = () => {
    if (drag?.moved) { suppress = true; setTimeout(() => { suppress = false; }, 0); }
    drag = null; stage.classList.remove("dragging");
  };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
  stage.addEventListener("click", e => { if (suppress) { e.stopPropagation(); e.preventDefault(); } }, true);
  stage.addEventListener("pointerleave", () => { $("#cr-playhead").hidden = true; });
  stage.addEventListener("wheel", e => {
    const lx = laneX(e);
    if (lx < 0) return;
    e.preventDefault();
    const W = laneW(), span = C.ve - C.vs, horizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY);
    if (horizontal || e.shiftKey) { const dt = (horizontal ? e.deltaX : e.deltaY) / W * span; setView(C.vs + dt, C.ve + dt); return; }
    const k = Math.exp(e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0018));
    const t = C.vs + lx / W * span, ns = Math.min(C.maxSpan, Math.max(C.minSpan, span * k)), vs = t - lx / W * ns;
    setView(vs, vs + ns);
  }, { passive: false });
  stage.addEventListener("keydown", e => {
    const span = C.ve - C.vs;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") { const dt = (e.key === "ArrowLeft" ? -1 : 1) * span * 0.2; setView(C.vs + dt, C.ve + dt); }
    else if (e.key === "+" || e.key === "=") zoomBy(0.7);
    else if (e.key === "-" || e.key === "_") zoomBy(1 / 0.7);
    else if (e.key === "f" || e.key === "F") fitExtent();
    else if (e.key === "Escape") { C.sel = null; schedule(); }
    else return;
    e.preventDefault();
  });

  const mini = $("#cr-mini");
  let miniDrag = false;
  const miniTo = e => {
    const r = mini.getBoundingClientRect(), ext = miniExtent();
    const t = ext.a + (e.clientX - r.left) / r.width * (ext.b - ext.a), span = C.ve - C.vs;
    setView(t - span / 2, t + span / 2);
  };
  mini.addEventListener("pointerdown", e => { miniDrag = true; mini.setPointerCapture(e.pointerId); miniTo(e); });
  mini.addEventListener("pointermove", e => { if (miniDrag) miniTo(e); });
  ["pointerup", "pointercancel"].forEach(t => mini.addEventListener(t, () => { miniDrag = false; }));

  const pwrap = $("#cr-pulse-wrap"), tip = $("#cr-tip");
  pwrap.addEventListener("pointerdown", e => {
    const x = e.clientX - pwrap.getBoundingClientRect().left;
    if (!pg || x < pg.ml) return;
    pdrag = { a: bucketAt(x), z: bucketAt(x) };
    pwrap.setPointerCapture(e.pointerId);
    drawViewBox([pdrag.a, pdrag.z]); readout([pdrag.a, pdrag.z]);
  });
  pwrap.addEventListener("pointermove", e => {
    if (!pg || !C.pulse) return;
    const r = pwrap.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, P = C.pulse;
    if (pdrag) { pdrag.z = bucketAt(x); const range = [Math.min(pdrag.a, pdrag.z), Math.max(pdrag.a, pdrag.z)]; drawViewBox(range); readout(range); }
    if (x < pg.ml) { tip.hidden = true; return; }
    const i = bucketAt(x), row = Math.floor((y - pg.top) / (PH.cell + PH.cg));
    let text = "";
    const when = P.bucket === "day" ? F.day.format(new Date(P.bs[i])) : F.dayHour.format(new Date(P.bs[i]));
    if (y < PH.hist) text = `${when} · ${plural(P.totals[i], "evento", "eventos")}`;
    else if (row >= 0 && row < P.lanes.length) text = `${when} · ${envName(P.lanes[row].environment_id)} · ${plural(P.lanes[row].counts[i], "evento", "eventos")}`;
    tip.hidden = !text;
    if (text) { tip.textContent = text; tip.style.left = `${Math.min(r.width - 110, Math.max(110, x))}px`; tip.style.top = `${y}px`; }
  });
  const endPulse = () => {
    if (!pdrag) return;
    const P = C.pulse, a = Math.min(pdrag.a, pdrag.z), z = Math.max(pdrag.a, pdrag.z);
    pdrag = null;
    setView(P.bs[a], P.be[z]);
    readout([a, z]);
  };
  pwrap.addEventListener("pointerup", endPulse);
  pwrap.addEventListener("pointercancel", () => { pdrag = null; drawViewBox(); });
  pwrap.addEventListener("pointerleave", () => { tip.hidden = true; });

  let lastW = { stage: 0, pulse: 0 };
  new ResizeObserver(() => {
    if (!root().classList.contains("active")) return;
    const sw = stage.clientWidth, pw = pwrap.clientWidth;
    if (pw !== lastW.pulse) { lastW.pulse = pw; drawPulse(); }
    if (sw !== lastW.stage) { lastW.stage = sw; schedule(); }
  }).observe(root());
}

// Al cambiar de estilo, los lienzos (pulso y minimapa) se repintan con los colores nuevos.
window.addEventListener("epigraph:estilo", () => {
  if (!root()?.classList.contains("active") || !C.pulse) return;
  drawPulse(); drawMini(); schedule();
});

window.EpiGraphChrono = { activate, focus };
if (root()?.classList.contains("active")) activate();
})();
