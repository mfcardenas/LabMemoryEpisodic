/* Proyectos: selector en la barra lateral y pantalla del catálogo (crear, editar, activar, borrar).
   Cada proyecto es un espacio aislado (base de datos, medios, índice y colección de Qdrant); su
   consola vive en /p/<id>/ y cambiar de proyecto recarga la página, así no queda estado mezclado. */
(() => {
"use strict";

const G = () => window.EpiGraph;
const $ = selector => document.querySelector(selector);
const esc = value => G().esc(value);
const P = { list: [], editing: null, deleting: null, busy: false };
const KIND = { real: "Real", prueba: "Prueba" };

const urlFor = id => `${id === "principal" ? "/" : `/p/${id}/`}${location.hash}`;
const current = () => G().project;

async function load() {
  const data = await G().api("/api/projects");
  P.list = data.projects;
  renderSwitch();
  if ($("#view-projects")?.classList.contains("active")) renderList();
  return P.list;
}

// -- Selector de la barra lateral ------------------------------------------------------
function renderSwitch() {
  const select = $("#project-select"), box = $(".project-switch");
  if (!select) return;
  const here = P.list.find(item => item.id === current());
  select.innerHTML = P.list.map(item => `<option value="${esc(item.id)}"${item.id === current() ? " selected" : ""}>${esc(item.name)}${item.kind === "prueba" ? " · prueba" : ""}${item.active ? "" : " · en pausa"}</option>`).join("");
  box.dataset.kind = here?.kind || "real";
  box.hidden = false;
  if (here) document.title = `EpiGraph · ${here.name}`;
}

// -- Pantalla de proyectos -----------------------------------------------------------------
function renderList() {
  const root = $("#project-list");
  $("#projects-count").textContent = String(P.list.length);
  root.innerHTML = P.list.map(card).join("") || '<p class="empty">No hay proyectos.</p>';
}

function card(item) {
  const s = item.stats, here = item.id === current();
  const folders = item.folders.length
    ? item.folders.map(folder => `<code>${esc(folder)}</code>`).join("")
    : '<span class="muted">ninguna (se carga desde Ingesta)</span>';
  const state = item.active ? (item.running ? "Activo" : "Activo (arrancando)") : "En pausa";
  const deleting = P.deleting === item.id ? `
      <div class="project-delete">
        <p>Se borrarán su base de datos, sus medios y su índice en Qdrant (<code>${esc(item.qdrant_collection)}</code>). Las carpetas vigiladas y sus originales no se tocan.</p>
        <label>Escriba <b>${esc(item.id)}</b> para confirmar<input type="text" data-delete-confirm autocomplete="off" spellcheck="false"></label>
        <div><button type="button" class="button danger" data-project-delete-go="${esc(item.id)}">Borrar definitivamente</button><button type="button" class="button" data-project-delete-cancel>Cancelar</button></div>
      </div>` : "";
  return `<article class="project-card${here ? " current" : ""}" data-project="${esc(item.id)}">
      <header>
        <div><h3>${esc(item.name)}</h3><code>${esc(item.id)}</code></div>
        <div class="project-tags"><span class="state-chip ${item.kind === "prueba" ? "pending" : "ready"}">${KIND[item.kind] || esc(item.kind)}</span><span class="project-state ${item.active ? "on" : "off"}"><i></i>${state}</span></div>
      </header>
      ${item.description ? `<p class="project-desc">${esc(item.description)}</p>` : ""}
      <dl class="project-stats">
        <div><dt>Episodios</dt><dd>${s.episodes.toLocaleString("es-ES")}</dd></div>
        <div><dt>Ambientes</dt><dd>${s.environments.toLocaleString("es-ES")}</dd></div>
        <div><dt>Eventos</dt><dd>${s.events.toLocaleString("es-ES")}</dd></div>
        <div><dt>Capturas</dt><dd>${s.captures.toLocaleString("es-ES")}</dd></div>
        <div><dt>En cola</dt><dd>${s.pending_jobs.toLocaleString("es-ES")}</dd></div>
      </dl>
      <dl class="project-where">
        <dt>Datos</dt><dd><code>${esc(item.data_dir)}</code></dd>
        <dt>Qdrant</dt><dd><code>${esc(item.qdrant_collection)}</code></dd>
        <dt>Carpetas</dt><dd>${folders}</dd>
        <dt>Creado</dt><dd>${new Date(item.created_at).toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" })}</dd>
      </dl>
      <footer>
        ${here ? '<span class="project-here">Estás en este proyecto</span>' : `<a class="button primary" href="${urlFor(item.id)}">Abrir</a>`}
        <button type="button" class="button" data-project-edit="${esc(item.id)}">Editar</button>
        ${item.is_default ? "" : `<button type="button" class="button" data-project-toggle="${esc(item.id)}">${item.active ? "Pausar" : "Activar"}</button>`}
        ${item.is_default ? '<span class="project-note">El principal no se pausa ni se borra.</span>' : `<button type="button" class="button ghost-danger" data-project-delete="${esc(item.id)}">Borrar…</button>`}
      </footer>${deleting}
    </article>`;
}

// -- Formulario: crear o editar ------------------------------------------------------------
function fillForm(item) {
  const form = $("#project-form");
  P.editing = item;
  form.reset();
  $("#project-form-title").textContent = item ? `Editar «${item.name}»` : "Crear proyecto";
  $("#project-form-kicker").textContent = item ? "EDICIÓN" : "NUEVO";
  $("#project-name").value = item?.name || "";
  $("#project-description").value = item?.description || "";
  $("#project-kind").value = item?.kind || "prueba";
  $("#project-kind").disabled = Boolean(item);
  $("#project-folders").value = (item?.folders || []).join("\n");
  $("#project-folders").disabled = Boolean(item?.is_default);
  $("#project-folders-note").textContent = item?.is_default
    ? "Las carpetas del principal se configuran en su watch.toml (EPIGRAPH_WATCH_CONFIG)."
    : "Una ruta completa por línea. Se leen por manifiesto (capture.json, con el ambiente de cada captura) y los originales no se mueven. Una carpeta solo puede pertenecer a un proyecto.";
  $("#project-active").checked = item ? item.active : true;
  $("#project-active").disabled = Boolean(item?.is_default);
  $("#project-submit").textContent = item ? "Guardar cambios" : "Crear proyecto";
  $("#project-form-cancel").hidden = !item;
  showFormMessage("");
  if (item) $("#project-name").focus();
}

function showFormMessage(text, kind = "warn") {
  const box = $("#project-form-message");
  box.hidden = !text;
  box.className = kind === "ok" ? "project-ok" : "warn";
  box.innerHTML = text;
}

async function submitForm(event) {
  event.preventDefault();
  if (P.busy) return;
  const editing = P.editing;
  const folders = $("#project-folders").value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const body = { name: $("#project-name").value.trim(), description: $("#project-description").value.trim() };
  if (!editing?.is_default) { body.folders = folders; body.active = $("#project-active").checked; }
  if (!editing) body.kind = $("#project-kind").value;
  setBusy(true);
  try {
    const saved = await G().api(editing ? `/api/projects/${encodeURIComponent(editing.id)}` : "/api/projects", {
      method: editing ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    fillForm(null);
    await load();
    renderList();
    showFormMessage(editing
      ? `Guardado «${esc(saved.name)}».`
      : `Creado «${esc(saved.name)}» con su base de datos y su colección <code>${esc(saved.qdrant_collection)}</code>. <a href="${urlFor(saved.id)}">Abrirlo</a>`, "ok");
  } catch (error) {
    showFormMessage(esc(error.message));
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  P.busy = busy;
  $("#project-submit").disabled = busy;
  $("#project-form").setAttribute("aria-busy", String(busy));
}

async function toggle(id) {
  const item = P.list.find(entry => entry.id === id);
  if (!item || P.busy) return;
  setBusy(true);
  try {
    await G().api(`/api/projects/${encodeURIComponent(id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: !item.active }),
    });
    await load(); renderList();
  } catch (error) {
    showFormMessage(esc(error.message));
  } finally {
    setBusy(false);
  }
}

async function remove(id, confirm) {
  if (P.busy) return;
  setBusy(true);
  try {
    await G().api(`/api/projects/${encodeURIComponent(id)}?${new URLSearchParams({ confirm })}`, { method: "DELETE" });
    P.deleting = null;
    if (id === current()) { location.href = "/#projects"; return; }
    await load(); renderList();
    showFormMessage(`Borrado el proyecto «${esc(id)}».`, "ok");
  } catch (error) {
    const box = document.querySelector(`[data-project="${CSS.escape(id)}"] .project-delete p`);
    if (box) box.innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  } finally {
    setBusy(false);
  }
}

function bind() {
  $("#project-select")?.addEventListener("change", event => {
    if (event.target.value !== current()) location.href = urlFor(event.target.value);
  });
  $("#project-form")?.addEventListener("submit", submitForm);
  $("#project-form-cancel")?.addEventListener("click", () => fillForm(null));
  $("#project-list")?.addEventListener("click", event => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.dataset.projectEdit) { fillForm(P.list.find(item => item.id === target.dataset.projectEdit)); $("#project-form").scrollIntoView({ behavior: "smooth", block: "start" }); }
    else if (target.dataset.projectToggle) toggle(target.dataset.projectToggle);
    else if (target.dataset.projectDelete) { P.deleting = target.dataset.projectDelete; renderList(); document.querySelector("[data-delete-confirm]")?.focus(); }
    else if ("projectDeleteCancel" in target.dataset) { P.deleting = null; renderList(); }
    else if (target.dataset.projectDeleteGo) {
      const input = target.closest(".project-delete").querySelector("[data-delete-confirm]");
      remove(target.dataset.projectDeleteGo, input.value.trim());
    }
  });
}

// Al entrar en la pantalla: cifras al día.
async function activate() {
  $("#project-list").innerHTML = '<div class="empty loading"><span class="spinner" aria-hidden="true"></span>Cargando proyectos…</div>';
  try { await load(); renderList(); }
  catch (error) { $("#project-list").innerHTML = `<p class="empty warn">${esc(error.message)}</p>`; }
}

// app.js publica window.EpiGraph en su DOMContentLoaded, que se registró antes que este.
window.EpiGraphProjects = { activate, load };
document.addEventListener("DOMContentLoaded", () => {
  bind();
  if (!P.editing) fillForm(null);
  load().catch(error => console.error("Proyectos:", error));
});
})();
