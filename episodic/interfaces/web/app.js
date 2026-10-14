document.addEventListener("DOMContentLoaded", () => {
  const state = {
    environments: [],
    sources: [],
    entities: [],
    episodes: [],
    nextCursor: null,
    activeEpisode: null,
    inspectedEpisode: null,
    inspectRequest: 0,
    episodesRequest: 0,
    episodeGroupMode: "blocks",
    timelineZoom: 1,
    timelineRawData: null,
    watchTimer: null,
    watchBusy: false,
    logLastId: 0,
    logFilterKey: null,
  };
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const esc = value => String(value ?? "").replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
  const shortId = value => value ? `${value.slice(0, 8)}…` : "—";
  const fmtDeltaSeconds = seconds => {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return "—";
    const s = Math.max(0, Number(seconds));
    if (s < 60) return `${Math.round(s)}s`;
    if (s < 3600) {
      const m = Math.floor(s / 60);
      const rem = Math.round(s % 60);
      return rem ? `${m}m ${rem}s` : `${m}m`;
    }
    const h = Math.floor(s / 3600);
    const remM = Math.round((s % 3600) / 60);
    return remM ? `${h}h ${remM}m` : `${h}h`;
  };
  const fmtClock = seconds => {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return "—";
    const total = Math.max(0, Number(seconds));
    const minutes = Math.floor(total / 60);
    const rest = total - minutes * 60;
    return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
  };
  const paragraphs = text => String(text || "").split(/\n+/).filter(Boolean).map(line => `<p>${esc(line)}</p>`).join("");
  const itemDurationSeconds = item => {
    const rawStart = item.start_time ?? item.interval?.start?.absolute;
    const rawEnd = item.end_time ?? item.interval?.end?.absolute ?? item.start_time;
    const start = new Date(rawStart || 0).getTime();
    const end = new Date(rawEnd || 0).getTime();
    return Math.max(0, (end - start) / 1000);
  };
  const fmtTime = value => value ? new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium", timeStyle: "medium",
  }).format(new Date(value)) : "Tiempo no declarado";
  const fmtInterval = item => {
    const start = item.start_time ?? item.interval?.start?.absolute;
    const end = item.end_time ?? item.interval?.end?.absolute;
    if (!start && !end) return "Tiempo no declarado";
    if (start === end || !end) return fmtTime(start);
    return `${fmtTime(start)} → ${fmtTime(end)}`;
  };
  const episodeTitle = episode => {
    const title = episode?.title || "Episodio sin título";
    if (!title.startsWith("Episodio compuesto")) return title;
    const environment = episode.environment_ids?.[0] || episode.location || "ambiente sin declarar";
    return `Episodio · ${environmentLabel(environment)}`;
  };
  const kindLabel = kind => ({
    observation: "Observación", event: "Evento", microepisode: "Microepisodio",
    episode: "Episodio", processing_window: "Ventana técnica",
  })[kind] || kind;
  const humanize = value => String(value || "").replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase());
  const modalityLabel = value => ({
    video: "Vídeo", audio: "Audio", image: "Imagen", document: "Documento",
    text: "Texto", sensor: "Sensor", multimodal: "Multimodal",
  })[value] || humanize(value || "modalidad no declarada");
  const observationLabel = value => ({
    text_fragment: "Fragmento de texto", document_fragment: "Fragmento documental",
    audio_segment: "Segmento de audio", speech_segment: "Habla transcrita", visual_observation: "Observación visual",
    image_observation: "Observación de imagen", video_observation: "Observación de vídeo",
    tool_presence: "Presencia de herramienta", tool_absence: "Ausencia de herramienta",
  })[value] || humanize(value || "observación");
  const sourceTypeLabel = value => ({
    camera: "Cámara", microphone: "Micrófono", document: "Documento", file: "Archivo",
    sensor: "Sensor", image: "Imagen", video: "Vídeo", audio: "Audio", text: "Texto",
  })[value] || humanize(value || "fuente");
  const environmentLabel = value => {
    const environment = state.environments.find(item => item.id === value);
    const label = environment?.name || value || "Ambiente no declarado";
    return label === value ? humanize(label) : label;
  };
  const confidenceLabel = value => value === null || value === undefined ? "sin confianza declarada" : `confianza ${Math.round(Number(value) * 100)}%`;
  const fmtShortInterval = item => {
    const startValue = item.start_time ?? item.interval?.start?.absolute;
    const endValue = item.end_time ?? item.interval?.end?.absolute;
    if (!startValue) return "Tiempo no declarado";
    const start = new Date(startValue); const end = endValue ? new Date(endValue) : null;
    const date = start.toLocaleDateString("es-ES", {day: "numeric", month: "short", year: "numeric"});
    const clock = value => value.toLocaleTimeString("es-ES", {hour: "2-digit", minute: "2-digit", second: "2-digit"});
    if (!end || start.getTime() === end.getTime()) return `${date} · ${clock(start)}`;
    return `${date} · ${clock(start)}–${clock(end)}`;
  };
  const basename = uri => {
    const clean = String(uri || "").replaceAll("\\", "/").split("?")[0];
    return decodeURIComponent(clean.split("/").filter(Boolean).pop() || "");
  };
  const assetLabel = (asset, source) => {
    if (asset.original_filename) return asset.original_filename;
    if (String(asset.uri || "").startsWith("phase6://")) return source?.name || "Fuente simulada";
    return basename(asset.uri) || source?.name || "Activo sin nombre de archivo";
  };
  const groupingRuleLabel = value => ({
    first_observation: "Inicio con la primera observación",
    first_microepisode: "Inicio con el primer microepisodio",
    end_of_input: "Fin de los datos disponibles",
    temporal_proximity: "Continuidad temporal",
    synchronized_sources: "Fuentes sincronizadas",
    entity_continuity: "Continuidad de entidad",
    action_compatibility: "Acción compatible",
    state_compatibility: "Estado compatible",
  })[value] || humanize(value);
  const timelineTitle = item => {
    const title = item.kind === "episode" && item.title?.startsWith("Episodio compuesto")
      ? `Episodio · ${item.environment_id || "ambiente sin declarar"}`
      : item.title;
    return item.external_id && item.external_id !== title ? `${title} · ${item.external_id}` : title;
  };

  const views = {
    episodes: ["MEMORIA CANÓNICA", "Explorador de episodios", "Dossier auditable: observaciones extraídas, eventos, microepisodios y evidencias originales."],
    timeline: ["CRONOLOGÍA MULTI-FUENTE", "Eje temporal continuo", "Visualización temporal por carriles: duración real de intervalos, simultaneidad y solapamientos."],
    ingest: ["RECORRIDO CANÓNICO", "Registro y custodia de activos", "El archivo original inicia la memoria; SHA-256 inmutable y procesamiento observable."],
    watch: ["ENTRADA AUTOMÁTICA", "Vigilancia y trabajos", "Lo que llega a las carpetas vigiladas se convierte en capturas: cada fichero leído, cada trabajo y su registro de ejecución."],
    search: ["CONSULTA Y AUDITORÍA", "Búsqueda híbrida y herramientas", "Recuperación semántica vectorial en Qdrant y herramientas tipadas de la Fase 8."],
    system: ["ARQUITECTURA Y ESTADO", "Proyección vectorial y runtime", "Separación estricta entre la verdad canónica (SQLite) y la proyección reconstruible (Qdrant)."],
  };

  $$(".nav-item").forEach(button => button.addEventListener("click", () => {
    location.hash = button.dataset.view;
  }));
  window.addEventListener("hashchange", () => {
    const name = location.hash.slice(1);
    switchView(views[name] ? name : "episodes");
  });
  function switchView(name, loadData = true) {
    $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === name));
    $$(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
    const [kicker, title, subtitle] = views[name] || views.episodes;
    $("#view-kicker").textContent = kicker; $("#view-title").textContent = title; $("#view-subtitle").textContent = subtitle;
    if (loadData && name === "episodes") loadEpisodes().catch(showEpisodeError);
    if (loadData && name === "timeline") loadTimeline().catch(showTimelineError);
    if (loadData && name === "ingest") Promise.all([loadAssets(), loadCaptures()]).catch(error => console.error(error));
    if (loadData && name === "search") loadToolsCatalog().catch(error => console.error(error));
    if (name === "watch") startWatchPolling(); else stopWatchPolling();
    if (loadData && name === "system") loadSystem().catch(error => console.error(error));
  }

  async function api(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { const body = await response.json(); message = body.detail || message; } catch (_) { /* no JSON */ }
      if (Array.isArray(message)) message = message.map(item => item.msg).join("; ");
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  async function loadEpisodeDetail(episodeId) {
    try {
      return await api(`/api/memory/episodes/${encodeURIComponent(episodeId)}`);
    } catch (error) {
      if (error.status !== 404) throw error;
      const legacy = await api(`/api/episodes/${encodeURIComponent(episodeId)}`);
      const observations = legacy.observations || [];
      const episode = {
        ...legacy.episode,
        observation_ids: legacy.episode.observation_ids || observations.map(item => item.id),
        microepisode_ids: legacy.episode.microepisode_ids || [],
        environment_ids: legacy.episode.environment_ids || (legacy.episode.location ? [legacy.episode.location] : []),
      };
      return {
        episode,
        microepisodes: [],
        ungrouped_observations: observations,
        events: [],
        evidence_references: [],
        assets: [],
        sources: [],
        environments: [],
        relations: [],
        hypotheses: [],
        vector_projection: {representations: [], indexed: false, point_ids: []},
        compatibility_mode: true,
      };
    }
  }

  async function loadHealth() {
    try {
      const health = await api("/api/health");
      $("#connection-light").classList.add("ok"); $("#connection-label").textContent = "Memoria disponible";
      $("#status-episodes").textContent = health.episodes_count;
      $("#status-points").textContent = health.qdrant_points_count;
      $("#status-backend").textContent = health.vector_backend;
      return health;
    } catch (error) {
      $("#connection-light").classList.remove("ok"); $("#connection-label").textContent = error.message;
      throw error;
    }
  }

  async function loadScope() {
    const [environments, sources, entities] = await Promise.all([
      api("/api/environments").catch(() => []),
      api("/api/sources").catch(() => []),
      api("/api/entities").catch(() => []),
    ]);
    state.environments = environments; state.sources = sources; state.entities = entities;
    [$("#timeline-environment"), $("#search-environment"), $("#episode-env-filter")].forEach(select => {
      if (!select) return;
      const first = select.options[0]?.outerHTML || '<option value="">Todos</option>';
      select.innerHTML = first + environments.map(item => `<option value="${esc(item.id)}">${esc(item.name || item.id)} · ${esc(item.id)}</option>`).join("");
    });
    [$("#timeline-source"), $("#search-source")].forEach(select => {
      if (!select) return;
      const first = select.options[0]?.outerHTML || '<option value="">Todas</option>';
      select.innerHTML = first + sources.map(item => `<option value="${esc(item.id)}">${esc(item.name || item.id)} · ${esc(item.source_type)}</option>`).join("");
    });
  }

  async function loadAssets() {
    const data = await api("/api/memory/assets");
    $("#asset-count").textContent = data.assets.length;
    const ledger = $("#asset-ledger");
    if (!data.assets.length) { ledger.innerHTML = '<div class="empty">No hay activos. Registra el primer archivo arriba.</div>'; return; }
    ledger.innerHTML = data.assets.map(({asset, storage, counts}) => `
      <div class="asset-row" data-asset-id="${esc(asset.id)}" tabindex="0">
        <div><strong>${esc(asset.original_filename || asset.id)}</strong><small>${esc(asset.environment_id)} / ${esc(asset.source_id)}</small></div>
        <span class="media-pill">${esc(asset.media_type)}</span>
        <code title="${esc(asset.content_hash)}">${esc(asset.content_hash)}</code>
        <div><strong>${counts.observations}</strong><small>observaciones</small></div>
        <div><strong>${counts.processing_windows}</strong><small>ventanas · ${counts.evidence_references} referencias</small></div>
        <span class="storage-state ${storage.available ? "ok" : ""}">${storage.available ? "DISPONIBLE" : "NO DISPONIBLE"}</span>
      </div>`).join("");
    $$(".asset-row").forEach(row => {
      row.addEventListener("click", () => openAsset(row.dataset.assetId));
      row.addEventListener("keydown", event => { if (event.key === "Enter") openAsset(row.dataset.assetId); });
    });
  }

  async function openAsset(assetId) {
    const data = await api(`/api/memory/assets/${encodeURIComponent(assetId)}`);
    const asset = data.asset;
    $("#asset-dialog-content").innerHTML = `<div class="dialog-body">
      <p class="kicker">ACTIVO ORIGINAL</p><h2>${esc(asset.original_filename || asset.id)}</h2>
      <div class="data-grid">
        <div><span>ID</span><code>${esc(asset.id)}</code></div><div><span>SHA‑256</span><code>${esc(asset.content_hash)}</code></div>
        <div><span>ALMACENAMIENTO</span><b>${esc(data.storage.mode)}</b></div><div><span>MIME</span><b>${esc(asset.mime_type)}</b></div>
        <div><span>TAMAÑO</span><b>${asset.size_bytes ?? "—"} bytes</b></div><div><span>DURACIÓN</span><b>${asset.duration_seconds ?? "—"} s</b></div>
        <div><span>AMBIENTE</span><b>${esc(asset.environment_id)}</b></div><div><span>FUENTE</span><b>${esc(asset.source_id)}</b></div>
        <div><span>DISPONIBLE</span><b>${data.storage.available ? "Sí" : "No"}</b></div>
      </div>
      <p><a class="button primary" href="/api/memory/assets/${encodeURIComponent(asset.id)}/file" target="_blank">Abrir original</a></p>
      <h3>Ventanas de procesamiento (${data.processing_windows.length})</h3>
      <pre class="json-block">${esc(JSON.stringify(data.processing_windows, null, 2))}</pre>
      <h3>Referencias de evidencia (${data.evidence_references.length})</h3>
      <pre class="json-block">${esc(JSON.stringify(data.evidence_references, null, 2))}</pre>
    </div>`;
    $("#asset-dialog").showModal();
  }
  $("[data-close-dialog]").addEventListener("click", () => $("#asset-dialog").close());

  $("#asset-file").addEventListener("change", event => {
    const names = [...event.target.files].map(file => file.name);
    $("#file-label").textContent = names.length
      ? (names.length === 1 ? names[0] : `${names.length} archivos: ${names.join(", ")}`)
      : "Seleccionar los archivos de la captura";
  });

  const captureStatusLabel = value => ({
    received: "Recibida", processing: "Procesando", ready: "Lista", failed: "Fallida",
  })[value] || value;
  const timeOriginLabel = value => ({
    declared: "hora declarada", media_metadata: "hora del vídeo", file_time: "hora del fichero",
    upload_time: "hora de subida (no declarada)",
  })[value] || value;
  const stageIcon = state => ({ completed: "✓", running: "…", failed: "✗" })[state] || "·";

  function renderJob(job, capture) {
    const output = $("#workflow-result");
    output.hidden = false;
    output.classList.toggle("error", job.state === "failed");
    const title = capture?.title ? esc(capture.title) : "Captura";
    const percent = Math.round((job.progress || 0) * 100);
    const stages = job.stages.map(stage => `<li class="${esc(stage.state)}"><span>${stageIcon(stage.state)}</span><b>${esc(stage.label)}</b><small>${esc(stage.detail || "")}</small></li>`).join("");
    const action = job.state === "completed" && job.result?.episode_id
      ? `<div class="job-actions"><button type="button" class="button primary" data-open-episode="${esc(job.result.episode_id)}">Abrir episodio</button></div>`
      : job.state === "failed" ? `<p>${esc(job.error || "El procesamiento falló")}</p>` : "";
    output.innerHTML = `<header><b>${title}</b><span>${job.state === "completed" ? "Completado" : job.state === "failed" ? "Fallido" : `${percent}%`}</span></header><ol>${stages}</ol>${action}`;
    output.querySelector("[data-open-episode]")?.addEventListener("click", button => openEpisode(button.currentTarget.dataset.openEpisode));
  }

  async function followJob(jobId, capture) {
    // El procesamiento ocurre en segundo plano: se consulta el progreso por etapas.
    while (true) {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      renderJob(job, capture);
      if (job.state === "completed" || job.state === "failed") return job;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  $("#ingest-form").addEventListener("submit", async event => {
    event.preventDefault();
    const submit = $("#ingest-submit"); const output = $("#workflow-result");
    const formData = new FormData(event.currentTarget);
    const captured = formData.get("captured_at");
    if (captured) formData.set("captured_at", new Date(captured).toISOString()); else formData.delete("captured_at");
    if (!formData.get("title")) formData.delete("title");
    submit.disabled = true; submit.textContent = "Subiendo…"; output.hidden = false; output.classList.remove("error");
    output.textContent = "Copiando los originales al almacén…";
    try {
      const capture = await api("/api/captures", { method: "POST", body: formData });
      submit.textContent = "Procesando…";
      await loadCaptures();
      await followJob(capture.job.id, capture);
      await Promise.all([loadHealth(), loadScope(), loadAssets(), loadCaptures()]);
    } catch (error) { output.classList.add("error"); output.textContent = `ERROR\n${error.message}`; }
    finally { submit.disabled = false; submit.textContent = "Subir captura y procesar"; }
  });

  async function loadCaptures() {
    const ledger = $("#capture-ledger");
    if (!ledger) return;
    const data = await api("/api/captures");
    $("#capture-count").textContent = data.captures.length;
    if (!data.captures.length) { ledger.innerHTML = '<div class="empty">Todavía no hay capturas. Sube la primera arriba.</div>'; return; }
    ledger.innerHTML = data.captures.map(capture => {
      const files = capture.assets.map(asset => `${asset.original_filename} · ${modalityLabel(asset.media_type)}`).join("<br>");
      const when = capture.captured_at ? `${fmtTime(capture.captured_at)} · ${timeOriginLabel(capture.captured_at_origin)}` : "Tiempo no declarado";
      const origin = originLabel(capture.metadata?.origin);
      const action = capture.episode_id
        ? `<button type="button" class="button-mini ghost" data-open-episode="${esc(capture.episode_id)}">Abrir episodio</button>`
        : capture.job && ["queued", "running"].includes(capture.job.state)
          ? `<button type="button" class="button-mini ghost" data-follow-job="${esc(capture.job.id)}">Ver progreso</button>` : "";
      return `<div class="capture-row"><div><strong>${esc(capture.title)}</strong><small>${esc(capture.environment_id)} · ${esc(when)} · ${esc(origin)}</small></div><small>${files}</small><span class="capture-status ${esc(capture.status)}">${esc(captureStatusLabel(capture.status))}</span><div>${action}</div></div>`;
    }).join("");
    ledger.querySelectorAll("[data-open-episode]").forEach(button => button.addEventListener("click", () => openEpisode(button.dataset.openEpisode)));
    ledger.querySelectorAll("[data-follow-job]").forEach(button => button.addEventListener("click", () => {
      const capture = data.captures.find(item => item.job?.id === button.dataset.followJob);
      followJob(button.dataset.followJob, capture).then(() => loadCaptures()).catch(error => console.error(error));
    }));
  }

  async function loadEpisodes(selectedId = state.activeEpisode, append = false, loadAll = false) {
    const list = $("#memory-map") || $("#episode-list");
    const loadMoreBtn = $("#load-more-episodes");
    const loadAllBtn = $("#load-all-episodes");
    if (loadMoreBtn) loadMoreBtn.disabled = true;
    if (loadAllBtn) loadAllBtn.disabled = true;
    const requestToken = ++state.episodesRequest;

    try {
      let cursor = append ? state.nextCursor : null;
      let episodesToKeep = append ? [...state.episodes] : [];

      do {
        const queryParams = new URLSearchParams({ limit: "50" });
        if (cursor) queryParams.set("cursor", cursor);
        const data = await api(`/api/episodes?${queryParams}`);
        const existingIds = new Set(episodesToKeep.map(e => e.id));
        const newItems = (data.episodes || []).filter(e => !existingIds.has(e.id));
        episodesToKeep.push(...newItems);
        cursor = data.next_cursor || null;
      } while (loadAll && cursor);

      if (requestToken !== state.episodesRequest) return;
      state.episodes = episodesToKeep;
      // Recarga completa: el detalle visible también se vuelve a pedir.
      if (!append) state.inspectedEpisode = null;
      state.nextCursor = cursor;
      $("#episode-count").textContent = state.episodes.length;
      updateEpisodeDeltaMetrics(state.episodes);
      renderFilteredEpisodes(selectedId);
    } finally {
      if (loadMoreBtn) loadMoreBtn.disabled = false;
      if (loadAllBtn) loadAllBtn.disabled = false;
    }
  }

  function updateEpisodeDeltaMetrics(episodes) {
    const statVolume = $("#stat-episode-volume");
    const statAvgDelta = $("#stat-avg-delta");
    const statTimeSpan = $("#stat-time-span");
    if (!statVolume) return;
    statVolume.textContent = `${episodes.length} cargados`;
    if (!episodes.length) {
      if (statAvgDelta) statAvgDelta.textContent = "Δt medio: —";
      if (statTimeSpan) statTimeSpan.textContent = "Rango: —";
      return;
    }
    const sorted = [...episodes].sort((a, b) => new Date(a.start_time || 0) - new Date(b.start_time || 0));
    if (sorted.length < 2) {
      if (statAvgDelta) statAvgDelta.textContent = `Δt: ${fmtDeltaSeconds(itemDurationSeconds(sorted[0]))}`;
      if (statTimeSpan) statTimeSpan.textContent = `Rango: ${fmtShortInterval(sorted[0])}`;
      return;
    }
    let totalDeltaSec = 0;
    let count = 0;
    for (let i = 0; i < sorted.length - 1; i++) {
      const t1 = new Date(sorted[i].start_time || 0).getTime();
      const t2 = new Date(sorted[i + 1].start_time || 0).getTime();
      const diffSec = (t2 - t1) / 1000;
      if (diffSec >= 0) {
        totalDeltaSec += diffSec;
        count++;
      }
    }
    const avgDelta = count > 0 ? totalDeltaSec / count : 0;
    if (statAvgDelta) statAvgDelta.textContent = `Δt medio: ${fmtDeltaSeconds(avgDelta)}`;
    const firstStart = new Date(sorted[0].start_time || 0).getTime();
    const lastEnd = new Date(sorted[sorted.length - 1].end_time || sorted[sorted.length - 1].start_time || 0).getTime();
    const totalSpanSec = (lastEnd - firstStart) / 1000;
    if (statTimeSpan) statTimeSpan.textContent = `Rango: ${fmtDeltaSeconds(totalSpanSec)}`;
  }

  function renderFilteredEpisodes(selectedId = state.activeEpisode) {
    const list = $("#memory-map") || $("#episode-list");
    if (!list) return;
    if (!state.episodes.length) {
      list.innerHTML = '<div class="empty">Todavía no hay episodios registrados en la memoria.</div>';
      updatePaginationControls(0, 0);
      return;
    }

    const query = ($("#episode-search-filter")?.value || "").toLowerCase().trim();
    const env = $("#episode-env-filter")?.value || "";
    const sortOrder = $("#episode-sort-order")?.value || "time_desc";
    const fromVal = $("#episode-filter-from")?.value;
    const toVal = $("#episode-filter-to")?.value;
    const fromTime = fromVal ? new Date(fromVal).getTime() : null;
    const toTime = toVal ? new Date(toVal).getTime() : null;

    let filtered = state.episodes.filter(ep => {
      if (env) {
        const envs = ep.environment_ids?.length ? ep.environment_ids : [ep.location].filter(Boolean);
        if (!envs.includes(env)) return false;
      }
      if (fromTime) {
        const t = new Date(ep.start_time || 0).getTime();
        if (t < fromTime) return false;
      }
      if (toTime) {
        const t = new Date(ep.end_time || ep.start_time || 0).getTime();
        if (t > toTime) return false;
      }
      if (query) {
        const title = (ep.title || "").toLowerCase();
        const loc = (ep.location || "").toLowerCase();
        const desc = (ep.description || "").toLowerCase();
        const id = (ep.id || "").toLowerCase();
        if (!title.includes(query) && !loc.includes(query) && !desc.includes(query) && !id.includes(query)) return false;
      }
      return true;
    });

    filtered.sort((a, b) => {
      if (sortOrder === "time_asc") {
        return new Date(a.start_time || 0) - new Date(b.start_time || 0);
      }
      if (sortOrder === "duration_desc") {
        return itemDurationSeconds(b) - itemDurationSeconds(a);
      }
      if (sortOrder === "observations_desc") {
        return (b.observation_ids?.length || 0) - (a.observation_ids?.length || 0);
      }
      return new Date(b.start_time || 0) - new Date(a.start_time || 0);
    });

    updatePaginationControls(filtered.length, state.episodes.length);

    if (!filtered.length) {
      list.innerHTML = '<div class="empty">No hay episodios que coincidan con los filtros.</div>';
      return;
    }

    // Un episodio pedido explícitamente se inspecciona aunque no esté en las
    // páginas cargadas: el detalle se obtiene por ID, no de la lista.
    const targetId = selectedId || state.activeEpisode || filtered[0].id;

    if (state.episodeGroupMode === "blocks") {
      const groups = new Map();
      filtered.forEach(episode => {
        const start = new Date(episode.start_time || 0);
        const groupKey = isNaN(start.getTime()) ? "Fecha no declarada" : start.toLocaleDateString("es-ES", {
          year: "numeric", month: "short", day: "numeric",
        });
        if (!groups.has(groupKey)) groups.set(groupKey, []);
        groups.get(groupKey).push(episode);
      });

      let html = "";
      for (const [groupName, groupItems] of groups.entries()) {
        const groupDuration = groupItems.reduce((acc, ep) => acc + itemDurationSeconds(ep), 0);
        html += `<div class="episode-block-header">
          <span>${esc(groupName)}</span>
          <small>${groupItems.length} ep · Δt total: ${fmtDeltaSeconds(groupDuration)}</small>
        </div>`;
        html += groupItems.map((episode, idx) => renderEpisodeItemCard(episode, targetId, idx > 0 ? groupItems[idx - 1] : null)).join("");
      }
      list.innerHTML = html;
    } else {
      list.innerHTML = filtered.map((episode, idx) => renderEpisodeItemCard(episode, targetId, idx > 0 ? filtered[idx - 1] : null)).join("");
    }

    $$(".episode-item").forEach(item => {
      item.addEventListener("click", () => {
        inspectEpisode(item.dataset.episodeId);
      });
    });

    // Filtrar u ordenar no debe volver a pedir el detalle ya mostrado.
    if (targetId && targetId !== state.inspectedEpisode) {
      inspectEpisode(targetId);
    }
  }

  function openEpisode(episodeId, focus = null) {
    state.activeEpisode = episodeId;
    state.episodeFocus = focus ? { episodeId, ...focus } : null;
    if (focus && state.inspectedEpisode === episodeId) state.inspectedEpisode = null;
    if (location.hash !== "#episodes") {
      // hashchange -> switchView -> loadEpisodes(state.activeEpisode): una sola carga.
      location.hash = "episodes";
    } else {
      loadEpisodes(episodeId).catch(showEpisodeError);
    }
  }

  function renderEpisodeItemCard(episode, targetId, prevEpisode) {
    const environmentIds = episode.environment_ids?.length ? episode.environment_ids : [episode.location].filter(Boolean);
    const envText = environmentIds.map(environmentLabel).join(", ") || "Ambiente no declarado";
    const obsCount = (episode.observation_ids || []).length;
    const microCount = (episode.microepisode_ids || []).length;
    const durSec = itemDurationSeconds(episode);
    const durText = fmtDeltaSeconds(durSec);
    let gapText = "";
    if (prevEpisode) {
      const prevEnd = new Date(prevEpisode.end_time || prevEpisode.start_time || 0).getTime();
      const currStart = new Date(episode.start_time || 0).getTime();
      const gapSec = Math.abs((currStart - prevEnd) / 1000);
      if (gapSec > 0) gapText = `<span class="item-gap-tag" title="Separación respecto al episodio adyacente">gap: ${fmtDeltaSeconds(gapSec)}</span>`;
    }
    return `<button class="episode-item ${episode.id === targetId ? "active" : ""}" data-episode-id="${esc(episode.id)}">
      <b>${esc(episodeTitle(episode))}</b>
      ${episode.external_id && episode.external_id !== episode.title ? `<code class="item-key">${esc(episode.external_id)}</code>` : ""}
      <div class="item-meta">
        <span>${esc(envText)}</span>
        <time>${esc(fmtShortInterval(episode))}</time>
      </div>
      <div class="item-badges">
        <span class="item-delta-tag">Δt ${esc(durText)}</span>
        ${obsCount ? `<span class="item-badge">${obsCount} obs</span>` : ""}
        ${microCount ? `<span class="item-badge">${microCount} micro</span>` : ""}
        ${gapText}
      </div>
    </button>`;
  }

  function updatePaginationControls(visibleCount, totalLoaded) {
    const status = $("#pagination-status");
    if (status) {
      status.textContent = `Mostrando ${visibleCount} de ${totalLoaded}${state.nextCursor ? " (hay más)" : ""}`;
    }
    const loadMoreBtn = $("#load-more-episodes");
    const loadAllBtn = $("#load-all-episodes");
    if (loadMoreBtn) loadMoreBtn.hidden = !state.nextCursor;
    if (loadAllBtn) loadAllBtn.hidden = !state.nextCursor;
  }

  function showEpisodeError(error) {
    $("#episode-count").textContent = "!";
    const list = $("#memory-map") || $("#episode-list");
    if (list) list.innerHTML = `<div class="empty">No se pudo leer la memoria:<br>${esc(error.message)}</div>`;
  }

  $("#episode-search-filter")?.addEventListener("input", () => renderFilteredEpisodes(state.activeEpisode));
  $("#episode-env-filter")?.addEventListener("change", () => renderFilteredEpisodes(state.activeEpisode));
  $("#episode-sort-order")?.addEventListener("change", () => renderFilteredEpisodes(state.activeEpisode));
  $("#episode-filter-from")?.addEventListener("change", () => renderFilteredEpisodes(state.activeEpisode));
  $("#episode-filter-to")?.addEventListener("change", () => renderFilteredEpisodes(state.activeEpisode));
  $("#episode-clear-dates")?.addEventListener("click", () => {
    if ($("#episode-filter-from")) $("#episode-filter-from").value = "";
    if ($("#episode-filter-to")) $("#episode-filter-to").value = "";
    renderFilteredEpisodes(state.activeEpisode);
  });
  $$(".group-pill").forEach(pill => {
    pill.addEventListener("click", () => {
      $$(".group-pill").forEach(p => p.classList.toggle("active", p === pill));
      state.episodeGroupMode = pill.dataset.group;
      renderFilteredEpisodes(state.activeEpisode);
    });
  });
  $("#load-more-episodes")?.addEventListener("click", () => loadEpisodes(state.activeEpisode, true));
  $("#load-all-episodes")?.addEventListener("click", () => loadEpisodes(state.activeEpisode, true, true));

  async function inspectEpisode(episodeId) {
    state.activeEpisode = episodeId;
    state.inspectedEpisode = episodeId;
    const requestToken = ++state.inspectRequest;
    $$(".episode-item").forEach(item => item.classList.toggle("active", item.dataset.episodeId === episodeId));
    const root = $("#episode-inspector"); root.innerHTML = '<div class="empty roomy">Reconstruyendo trazabilidad…</div>';
    try {
      const data = await loadEpisodeDetail(episodeId);
      // Una selección posterior ganó la carrera: no pintar un detalle obsoleto.
      if (requestToken !== state.inspectRequest) return;
      const episode = data.episode;
      const referencesById = new Map(data.evidence_references.map(reference => [reference.id, reference]));
      const assetsById = new Map(data.assets.map(asset => [asset.id, asset]));
      const sourcesById = new Map(data.sources.map(source => [source.id, source]));
      const evidenceFor = observation => (observation.evidence_reference_ids || observation.evidence_references || []).map(id => referencesById.get(id)).filter(Boolean);
      const allObservations = [...new Map([
        ...data.microepisodes.flatMap(item => item.observations),
        ...data.ungrouped_observations,
      ].map(item => [item.id, item])).values()];
      const renderObservation = (observation, index) => {
        const asset = assetsById.get(observation.asset_id);
        const source = sourcesById.get(observation.source_id || asset?.source_id);
        const linkedEvidence = evidenceFor(observation);
        const origin = asset ? assetLabel(asset, source) : source?.name || "Origen no enlazado";
        const extracted = observation.description || observation.content;
        const evidenceHtml = linkedEvidence.length
          ? linkedEvidence.map(reference => `<span>${esc(evidenceLocator(reference))}</span>`).join("")
          : '<span class="missing">Sin localización de evidencia</span>';
        return `<article class="extraction-card" data-observation-id="${esc(observation.id)}">
          <div class="extraction-number">${String(index + 1).padStart(2, "0")}</div>
          <div class="extraction-body"><div class="extraction-head"><span class="modality-tag ${esc(observation.modality)}">${esc(modalityLabel(observation.modality))}</span><b>${esc(observationLabel(observation.observation_type))}</b><em>${esc(confidenceLabel(observation.confidence))}</em></div>
          <div class="extracted-content">${extracted ? `<p>${esc(extracted)}</p>` : '<p class="no-content">El procesador no produjo contenido textual; se conserva la observación estructurada.</p>'}</div>
          <div class="observation-origin"><span>Procede de</span><b>${esc(origin)}</b>${source ? `<small>${esc(source.name)} · ${esc(sourceTypeLabel(source.source_type))}</small>` : ""}</div>
          <div class="evidence-locations"><b>Evidencia localizada</b>${evidenceHtml}</div>
          <details class="technical-details compact"><summary>Datos técnicos de la observación</summary><dl><dt>ID</dt><dd>${esc(observation.id)}</dd><dt>Productor</dt><dd>${esc(observation.producer || "—")}</dd><dt>Modelo</dt><dd>${esc(observation.model_version || "—")}</dd><dt>Estado</dt><dd>${esc(observation.validation_state || "—")}</dd></dl></details></div>
        </article>`;
      };
      const hasVideoEvents = data.events.some(item => item.event_type === "video_segment");
      const speechObservations = allObservations.filter(item => item.observation_type === "speech_segment");
      // Lo que ya se muestra como eventos o transcripción no se repite abajo;
      // los segmentos de audio técnicos sobran cuando hay transcripción.
      const hiddenTypes = new Set([
        ...(hasVideoEvents ? ["video_event"] : []),
        ...(speechObservations.length ? ["speech_segment", "audio_segment"] : []),
      ]);
      const contentObservations = allObservations.filter(item => !hiddenTypes.has(item.observation_type));
      const observationsHtml = contentObservations.map(renderObservation).join("");
      const originsHtml = data.assets.map(asset => {
        const source = sourcesById.get(asset.source_id);
        const references = data.evidence_references.filter(item => item.asset_id === asset.id);
        const isFixture = String(asset.uri || "").startsWith("phase6://");
        return `<article class="origin-card"><div class="origin-type"><span>${esc(modalityLabel(asset.media_type))}</span><small>${esc(asset.mime_type || "tipo MIME no declarado")}</small></div>
          <h4>${esc(assetLabel(asset, source))}</h4><p>${source ? `${esc(source.name)} · ${esc(sourceTypeLabel(source.source_type))}` : "Fuente no registrada"}</p>
          <div class="origin-evidence">${references.map(item => `<span>${esc(evidenceLocator(item))}</span>`).join("") || '<span>Sin referencias localizadas</span>'}</div>
          ${asset.available ? `<a href="/api/memory/assets/${encodeURIComponent(asset.id)}/file" target="_blank">Abrir archivo original</a>` : `<div class="origin-unavailable">${isFixture ? "Fuente simulada: no existe un fichero físico" : "El fichero original no está disponible"}</div>`}
        </article>`;
      }).join("");
      const orderedEvents = [...data.events].sort((a, b) =>
        (a.asset_id || "").localeCompare(b.asset_id || "") || (a.sequence_number || 0) - (b.sequence_number || 0));
      const videoAsset = data.assets.find(asset => asset.media_type === "video" && asset.available);
      const renderEvent = item => {
        const attributes = item.attributes || {};
        const generation = item.metadata?.generation || {};
        const hasOffsets = item.start_offset_seconds !== null && item.start_offset_seconds !== undefined;
        const span = hasOffsets ? `${fmtClock(item.start_offset_seconds)} – ${fmtClock(item.end_offset_seconds)}` : fmtInterval(item);
        const delta = hasOffsets ? fmtDeltaSeconds(item.end_offset_seconds - item.start_offset_seconds) : fmtDeltaSeconds(itemDurationSeconds(item));
        const frames = (item.keyframes || []).map((frame, index) => `<button type="button" class="keyframe" data-seek="${esc(frame.offset_seconds)}" title="Ver en el vídeo (${fmtClock(frame.offset_seconds)})"><img loading="lazy" alt="Fotograma ${fmtClock(frame.offset_seconds)}" src="/api/memory/events/${encodeURIComponent(item.id)}/keyframes/${index}"><span>${fmtClock(frame.offset_seconds)}</span></button>`).join("");
        const speech = attributes.speech || [];
        const speechHtml = speech.length ? `<div class="event-speech"><b>Lo que se dijo</b>${speech.map(line => `<p><button type="button" class="speech-time" data-seek="${esc(line.start_offset_seconds)}" title="Ver en el vídeo">${fmtClock(line.start_offset_seconds)}</button><span>${esc(line.text)}</span></p>`).join("")}</div>` : "";
        const chips = (label, values) => values?.length ? `<div class="event-chips"><b>${label}</b>${values.map(value => `<span>${esc(typeof value === "string" ? value : [value.label, ...(value.attributes || []), value.description].filter(Boolean).join(" · "))}</span>`).join("")}</div>` : "";
        return `<article class="event-card rich" data-event-start="${hasOffsets ? esc(item.start_offset_seconds) : ""}">
          <header><span class="event-seq">${String((item.sequence_number || 0) + 1).padStart(2, "0")}</span><div><strong>${esc(item.title || humanize(item.event_type))}</strong><small>${esc(span)} · Δt ${esc(delta)}</small></div>${hasOffsets && videoAsset ? `<button type="button" class="button-mini ghost" data-seek="${esc(item.start_offset_seconds)}">▶ ${fmtClock(item.start_offset_seconds)}</button>` : ""}</header>
          ${item.summary ? `<p class="event-summary">${esc(item.summary)}</p>` : ""}
          ${frames ? `<div class="keyframes">${frames}</div>` : ""}
          ${speechHtml}
          <details class="event-detail"><summary>Descripción detallada</summary><div class="event-detail-body">${paragraphs(item.description)}
            ${chips("Acciones", attributes.actions)}${chips("Personas", attributes.actors)}${chips("Objetos", attributes.objects)}${chips("Texto visible", attributes.visible_text)}
            ${attributes.scene ? `<p class="event-meta"><b>Escena:</b> ${esc(attributes.scene)}</p>` : ""}
            ${attributes.changes_from_previous ? `<p class="event-meta"><b>Cambio respecto al anterior:</b> ${esc(attributes.changes_from_previous)}</p>` : ""}
            ${attributes.uncertainty ? `<p class="event-meta uncertain"><b>Incertidumbre:</b> ${esc(attributes.uncertainty)}</p>` : ""}
          </div></details>
          ${generation.vision_model ? `<p class="event-provenance">Descripción generada por ${esc(generation.vision_model)}${speech[0]?.speech_model ? ` · habla transcrita por ${esc(speech[0].speech_model)}` : ""} · no verificada</p>` : ""}
        </article>`;
      };
      const playerHtml = videoAsset && orderedEvents.length
        ? `<div class="episode-player"><video id="episode-video" controls preload="metadata" src="/api/memory/assets/${encodeURIComponent(videoAsset.id)}/file"></video></div>` : "";
      const transcriptHtml = speechObservations.length ? (() => {
        const byAsset = new Map();
        speechObservations.forEach(item => byAsset.set(item.asset_id, [...(byAsset.get(item.asset_id) || []), item]));
        const blocks = [...byAsset.entries()].map(([assetId, lines]) => {
          const asset = data.assets.find(item => item.id === assetId);
          const seekable = videoAsset && asset?.id === videoAsset.id;
          const model = lines[0].attributes?.generation?.speech_model;
          const rows = lines
            .sort((a, b) => (a.attributes?.start_offset_seconds || 0) - (b.attributes?.start_offset_seconds || 0))
            .map(line => {
              const offset = line.attributes?.start_offset_seconds || 0;
              const time = seekable
                ? `<button type="button" class="speech-time" data-seek="${esc(offset)}" title="Ver en el vídeo">${fmtClock(offset)}</button>`
                : `<span class="speech-time">${fmtClock(offset)}</span>`;
              return `<p data-observation-id="${esc(line.id)}">${time}<span>${esc(line.description)}</span></p>`;
            }).join("");
          return `<div class="transcript-block"><h4>${esc(asset ? assetLabel(asset, sourcesById.get(asset.source_id)) : "Audio")}</h4>${rows}${model ? `<p class="event-provenance">Transcrito por ${esc(model)} · no verificado</p>` : ""}</div>`;
        }).join("");
        return `<section class="transcript-section"><div class="section-title"><p class="kicker">HABLA</p><h3>Transcripción</h3><p>Lo que se dijo, con su tiempo dentro de cada archivo.</p></div>${blocks}</section>`;
      })() : "";
      const eventsHtml = orderedEvents.length ? `<section class="event-section"><div class="section-title"><p class="kicker">EVENTOS</p><h3>Qué ocurrió, tramo a tramo</h3><p>Cada evento es un tramo del vídeo descrito por el modelo de visión, con lo que se dijo durante ese tramo. Pulsa un fotograma o un tiempo para verlo en el vídeo.</p></div>${playerHtml}${orderedEvents.map(renderEvent).join("")}</section>` : "";
      const microHtml = data.microepisodes.map(({microepisode, observations, events}, index) => {
        const rules = (microepisode.metadata?.grouping_decisions || []).flatMap(item => item.rules || []);
        return `<article class="micro-summary"><span>Microepisodio ${index + 1}</span><b>${observations.length} observación(es) · ${events.length} evento(s)</b><small>${fmtInterval(microepisode)}</small><p>${[...new Set(rules)].map(groupingRuleLabel).join(" · ") || "Sin reglas declaradas"}</p></article>`;
      }).join("");
      const structureHtml = microHtml || `<div class="not-grouped"><b>Aún no hay microepisodios</b><span>${allObservations.length ? `Las ${allObservations.length} observación(es) se conservan directamente asociadas a este episodio, pero todavía no han sido agrupadas en microepisodios.` : "Este episodio no tiene observaciones asociadas."}</span></div>`;
      const hypothesesHtml = data.hypotheses.length ? `<section class="hypothesis-section"><div class="section-title"><p class="kicker">NO SON HECHOS</p><h3>Hipótesis pendientes</h3></div>${data.hypotheses.map(item => `<div class="hypothesis-box"><p>${esc(item.statement)}</p></div>`).join("")}</section>` : "";
      const compatibilityNotice = data.compatibility_mode ? '<div class="compatibility-notice"><b>Vista de compatibilidad</b><span>Reinicia EpiGraph para recuperar la trazabilidad completa del servidor actualizado.</span></div>' : "";
      const environmentIds = episode.environment_ids?.length ? episode.environment_ids : [episode.location].filter(Boolean);
      const environment = environmentIds.map(environmentLabel).join(", ") || "Ambiente no declarado";
      root.innerHTML = `<header class="episode-hero"><div><p class="kicker">EPISODIO EN LA MEMORIA</p><h2>${esc(episodeTitle(episode))}</h2>${episode.external_id ? `<p class="episode-key">Captura <code>${esc(episode.external_id)}</code></p>` : ""}<p>${esc(fmtInterval(episode))}${episode.metadata?.captured_at_origin ? ` · ${esc(timeOriginLabel(episode.metadata.captured_at_origin))}` : ""}</p></div><span class="episode-place">${esc(environment)}</span></header>${compatibilityNotice}
        <div class="memory-facts"><div><span>CONTENIDO EXTRAÍDO</span><b>${allObservations.length} observación(es)</b></div><div><span>PROCEDENCIA</span><b>${data.assets.length} activo(s)</b></div><div><span>EVENTOS</span><b>${data.events.length || "Sin eventos"}</b></div><div><span>ESTRUCTURA</span><b>${data.microepisodes.length} microepisodio(s)</b></div></div>
        <div class="episode-readable"><main>${eventsHtml}${transcriptHtml}<section class="content-section"><div class="section-title"><p class="kicker">QUÉ CONTIENE</p><h3>${hasVideoEvents ? "Otro contenido extraído" : "Contenido extraído"}</h3><p>Texto o descripción producido por los procesadores. No sustituye al original.</p></div>${observationsHtml || '<div class="empty">No hay más contenido extraído en este episodio.</div>'}</section></main>
          <aside class="origin-section"><div class="section-title"><p class="kicker">DE DÓNDE PROCEDE</p><h3>Archivos y fuentes</h3><p>Cada observación debe poder volver a su evidencia original.</p></div>${originsHtml || '<div class="empty">No hay activos originales enlazados.</div>'}</aside></div>
        ${hypothesesHtml}
        <details class="episode-structure"><summary><span>Cómo se construyó este episodio</span><small>Reglas deterministas y microepisodios</small></summary><div class="structure-content">${structureHtml}</div></details>
        <details class="technical-details episode-technical"><summary>Identificadores y datos técnicos</summary><dl><dt>ID del episodio</dt><dd>${esc(episode.id)}</dd><dt>Productor</dt><dd>${esc(episode.producer || "—")}</dd><dt>Versión</dt><dd>${esc(episode.model_version || "—")}</dd><dt>Confianza</dt><dd>${episode.confidence ?? "—"}</dd><dt>Qdrant</dt><dd>${data.compatibility_mode ? "No consultado" : data.vector_projection.indexed ? "Indexado" : "Pendiente"}</dd><dt>Relaciones</dt><dd>${data.relations.length}</dd></dl></details>`;
      root.querySelectorAll("[data-seek]").forEach(button => button.addEventListener("click", () => {
        const video = root.querySelector("#episode-video");
        if (!video) return;
        video.currentTime = Number(button.dataset.seek) || 0;
        video.scrollIntoView({ behavior: "smooth", block: "center" });
        video.play().catch(() => {});
      }));
      applyEpisodeFocus(root, episodeId);
    } catch (error) {
      if (requestToken !== state.inspectRequest) return;
      state.inspectedEpisode = null;
      root.innerHTML = `<div class="empty roomy"><b>Error</b><span>${esc(error.message)}</span></div>`;
    }
  }

  function evidenceLocator(reference) {
    if (reference.frame_number !== null && reference.frame_number !== undefined) return `fotograma ${reference.frame_number}`;
    if (reference.page_number) return `página ${reference.page_number}`;
    if (reference.start_offset_seconds !== null && reference.start_offset_seconds !== undefined) return `${fmtClock(reference.start_offset_seconds)}–${fmtClock(reference.end_offset_seconds)}`;
    if (reference.text_start !== null && reference.text_start !== undefined) return `caracteres ${reference.text_start}–${reference.text_end}`;
    return reference.text_quote || "archivo completo";
  }

  async function buildFallbackTimeline(envFilter, sourceFilter) {
    const episodesData = await api("/api/episodes?limit=100");
    const sources = state.sources.length ? state.sources : await api("/api/sources");
    const episodes = episodesData.episodes || [];
    const lanes = [
      { id: "episodic", label: "Episodios Consolidados", kind: "episodic" },
      ...sources.map(s => ({ id: s.id, label: s.name, kind: s.source_type }))
    ];
    const items = episodes
      .filter(ep => {
        if (envFilter) {
          const envs = ep.environment_ids?.length ? ep.environment_ids : [ep.location].filter(Boolean);
          if (!envs.includes(envFilter)) return false;
        }
        if (sourceFilter) {
          if (!ep.source_ids?.includes(sourceFilter)) return false;
        }
        return true;
      })
      .map(ep => {
        const start = ep.start_time ?? ep.interval?.start?.absolute;
        const end = ep.end_time ?? ep.interval?.end?.absolute ?? start;
        return {
          id: ep.id,
          kind: "episode",
          title: episodeTitle(ep),
          start_time: start,
          end_time: end,
          lane_id: "episodic",
          environment_id: ep.environment_ids?.[0] || ep.location,
          evidence_reference_ids: ep.evidence_reference_ids || [],
        };
      })
      .filter(item => item.start_time);
    return { lanes, items };
  }

  async function loadTimeline() {
    const params = new URLSearchParams();
    if ($("#timeline-environment")?.value) params.set("environment_id", $("#timeline-environment").value);
    if ($("#timeline-source")?.value) params.set("source_id", $("#timeline-source").value);
    try {
      const data = await api(`/api/memory/timeline?${params}`);
      state.timelineRawData = data;
      renderTimeline(data);
    } catch (err) {
      if (err.status === 404) {
        const fallbackData = await buildFallbackTimeline($("#timeline-environment")?.value, $("#timeline-source")?.value);
        state.timelineRawData = fallbackData;
        renderTimeline(fallbackData);
      } else {
        showTimelineError(err);
      }
    }
  }

  function showTimelineError(error) {
    $("#timeline-canvas").innerHTML = `<div class="empty">No se pudo construir la línea temporal:<br>${esc(error.message)}</div>`;
    $("#timeline-ledger").innerHTML = "";
  }

  function renderTimeline(data) {
    if (!data) return;
    const showWindows = $("#show-windows")?.checked;
    const items = (data.items || []).filter(item => showWindows || item.kind !== "processing_window").filter(item => item.start_time);
    const canvas = $("#timeline-canvas");
    const ledger = $("#timeline-ledger");
    if (!items.length) {
      canvas.innerHTML = '<div class="empty">No hay intervalos temporales para los filtros seleccionados.</div>';
      ledger.innerHTML = "";
      if ($("#tl-metric-count")) $("#tl-metric-count").textContent = "0 elementos";
      if ($("#tl-metric-span")) $("#tl-metric-span").textContent = "Duración total: —";
      if ($("#tl-metric-min-delta")) $("#tl-metric-min-delta").textContent = "Δt mín: —";
      return;
    }

    const starts = items.map(item => new Date(item.start_time).getTime());
    const ends = items.map(item => new Date(item.end_time || item.start_time).getTime());
    let min = Math.min(...starts);
    let max = Math.max(...ends);
    const observedSpan = max - min;
    const minimumSpan = 60_000;
    if (observedSpan < minimumSpan) {
      const center = min + observedSpan / 2;
      min = center - minimumSpan / 2;
      max = center + minimumSpan / 2;
    } else {
      const padding = observedSpan * .04;
      min -= padding;
      max += padding;
    }
    const range = max - min;

    // Calcular cadencia y menor delta de tiempo entre evidencias/episodios
    let minDeltaMs = Infinity;
    items.forEach((item, idx) => {
      const st = new Date(item.start_time).getTime();
      const et = new Date(item.end_time || item.start_time).getTime();
      const dur = et - st;
      if (dur > 0 && dur < minDeltaMs) minDeltaMs = dur;
      if (idx > 0) {
        const prevSt = new Date(items[idx - 1].start_time).getTime();
        const gap = Math.abs(st - prevSt);
        if (gap > 0 && gap < minDeltaMs) minDeltaMs = gap;
      }
    });

    const metricCount = $("#tl-metric-count");
    const metricSpan = $("#tl-metric-span");
    const metricMinDelta = $("#tl-metric-min-delta");
    if (metricCount) metricCount.textContent = `${items.length} elementos`;
    if (metricSpan) metricSpan.textContent = `Duración: ${fmtDeltaSeconds(observedSpan / 1000)}`;
    if (metricMinDelta) metricMinDelta.textContent = `Δt mín: ${minDeltaMs < Infinity ? fmtDeltaSeconds(minDeltaMs / 1000) : "—"}`;

    const zoomFactor = state.timelineZoom || 1;
    const gridWidthPercent = Math.max(100, zoomFactor * 100);

    const numTicks = Math.min(30, Math.max(5, Math.round(5 * zoomFactor)));
    const ticks = Array.from({ length: numTicks }, (_, i) => i / (numTicks - 1)).map(ratio => {
      const instant = new Date(min + range * ratio);
      const timeStr = instant.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      return `<span style="left:${ratio * 100}%">${timeStr}</span>`;
    }).join("");

    const laneHtml = (data.lanes || []).map(lane => {
      const laneItems = items.filter(item => item.lane_id === lane.id || (lane.id === "episodic" && !data.lanes.some(candidate => candidate.id === item.lane_id)));
      const blocks = laneItems.map(item => {
        const start = new Date(item.start_time).getTime();
        const end = new Date(item.end_time || item.start_time).getTime();
        const left = ((start - min) / range) * 100;
        const duration = end - start;
        const width = Math.max((duration / range) * 100, 0.4 / zoomFactor);
        const instantClass = duration <= 0 ? " instant" : "";
        const durText = fmtDeltaSeconds(duration / 1000);
        return `<button class="time-block ${esc(item.kind)}${instantClass}" style="left:${left}%;width:${width}%" data-kind="${esc(item.kind)}" data-id="${esc(item.id)}" title="${esc(kindLabel(item.kind))} · ${esc(timelineTitle(item))} · Δt: ${durText} · ${esc(fmtInterval(item))}"><span>${esc(timelineTitle(item))}</span></button>`;
      }).join("");
      return `<div class="timeline-lane"><div class="lane-label">${esc(lane.label)}<small>${esc(lane.kind)}</small></div><div class="lane-track">${blocks}</div></div>`;
    }).join("");

    canvas.innerHTML = `<div class="timeline-grid" style="width:${gridWidthPercent}%;"><div class="timeline-axis"><div></div><div class="axis-labels">${ticks}</div></div>${laneHtml}</div>`;

    ledger.innerHTML = `<div class="timeline-ledger-head"><h3>Registro temporal</h3><span>${items.length} elementos visibles</span></div>${items.sort((a, b) => new Date(a.start_time) - new Date(b.start_time)).map(item => {
      const durSec = itemDurationSeconds(item);
      return `<button class="timeline-record" data-kind="${esc(item.kind)}" data-id="${esc(item.id)}"><span class="record-kind ${esc(item.kind)}">${esc(kindLabel(item.kind))}</span><b>${esc(timelineTitle(item))}</b><time>${esc(fmtInterval(item))} (Δt ${fmtDeltaSeconds(durSec)})</time><small>${(item.evidence_reference_ids || []).length} evidencia(s)</small></button>`;
    }).join("")}`;

    $$(".time-block, .timeline-record").forEach(element => element.addEventListener("click", () => {
      if (element.dataset.kind !== "episode") return;
      openEpisode(element.dataset.id);
    }));
  }

  [$("#timeline-environment"), $("#timeline-source"), $("#show-windows")].forEach(control => control?.addEventListener("change", loadTimeline));

  const zoomLevels = [1, 1.5, 2, 3, 5, 8, 12, 16];
  $("#zoom-in")?.addEventListener("click", () => {
    const curr = state.timelineZoom || 1;
    const next = zoomLevels.find(z => z > curr) || 16;
    state.timelineZoom = next;
    const zoomText = $("#zoom-level-text");
    if (zoomText) zoomText.textContent = `${next}x`;
    if (state.timelineRawData) renderTimeline(state.timelineRawData);
  });
  $("#zoom-out")?.addEventListener("click", () => {
    const curr = state.timelineZoom || 1;
    const prev = [...zoomLevels].reverse().find(z => z < curr) || 1;
    state.timelineZoom = prev;
    const zoomText = $("#zoom-level-text");
    if (zoomText) zoomText.textContent = `${prev}x`;
    if (state.timelineRawData) renderTimeline(state.timelineRawData);
  });
  $("#zoom-fit")?.addEventListener("click", () => {
    state.timelineZoom = 1;
    const zoomText = $("#zoom-level-text");
    if (zoomText) zoomText.textContent = "1x";
    if (state.timelineRawData) renderTimeline(state.timelineRawData);
  });

  // Pestañas del módulo de consultas
  $$(".search-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.searchTab;
      $$(".search-tab-btn").forEach(b => b.classList.toggle("active", b.dataset.searchTab === tab));
      const isHybrid = (tab === "hybrid");
      const hybridEl = $("#search-tab-hybrid-content");
      const toolsEl = $("#search-tab-tools-content");
      if (hybridEl) { hybridEl.classList.toggle("active", isHybrid); hybridEl.hidden = !isHybrid; }
      if (toolsEl) { toolsEl.classList.toggle("active", !isHybrid); toolsEl.hidden = isHybrid; }
      if (tab === "tools") loadToolsCatalog();
    });
  });

  const TOOL_DEFS = [
    {
      name: "get_last_confirmed_observation",
      label: "get_last_confirmed_observation · Última observación confirmada",
      desc: "Devuelve la última observación con estado 'validated' para una identidad de entidad.",
      fields: [{ name: "entity_id", label: "ID de la Entidad", type: "text", required: true, placeholder: "ej. drill_north_track" }]
    },
    {
      name: "get_entity_timeline",
      label: "get_entity_timeline · Línea temporal de entidad",
      desc: "Observaciones cronológicas asociadas a una identidad validada y sus alias.",
      fields: [
        { name: "entity_id", label: "ID de la Entidad", type: "text", required: true, placeholder: "ej. drill_north_track" },
        { name: "limit", label: "Límite", type: "number", value: 20 }
      ]
    },
    {
      name: "search_observations",
      label: "search_observations · Buscar observaciones estructuradas",
      desc: "Observaciones filtradas o semánticamente relacionadas.",
      fields: [
        { name: "query", label: "Consulta", type: "text", placeholder: "ej. taladro, pipeta, inspección" },
        { name: "modality", label: "Modalidad", type: "select", options: ["", "video", "audio", "image", "document", "text"] },
        { name: "limit", label: "Límite", type: "number", value: 10 }
      ]
    },
    {
      name: "search_documents",
      label: "search_documents · Fragmentos documentales",
      desc: "Búsqueda de fragmentos documentales con número de página y cita textual.",
      fields: [
        { name: "query", label: "Término en documentos", type: "text", required: true, placeholder: "ej. número de serie, calibración" },
        { name: "limit", label: "Límite", type: "number", value: 5 }
      ]
    },
    {
      name: "find_contradictions",
      label: "find_contradictions · Detectar contradicciones",
      desc: "Encuentra relaciones CONTRADICTS, extremos en conflicto y evidencias asociadas.",
      fields: [
        { name: "entity_id", label: "Entidad (opcional)", type: "text", placeholder: "ej. drill_north_track" },
        { name: "environment_id", label: "Ambiente (opcional)", type: "text", placeholder: "ej. workshop_north" }
      ]
    },
    {
      name: "compare_entities",
      label: "compare_entities · Comparar entidades",
      desc: "Compara dos identidades (validated_same, hypothesized_same o undetermined) sin crear decisiones arbitrarias.",
      fields: [
        { name: "entity_id_a", label: "Entidad A", type: "text", required: true, placeholder: "ej. drill_north_track" },
        { name: "entity_id_b", label: "Entidad B", type: "text", required: true, placeholder: "ej. drill_south_track" }
      ]
    },
    {
      name: "get_environment_topology",
      label: "get_environment_topology · Topología de ambiente",
      desc: "Ambientes, fuentes, entidades presentes, episodios y vínculos espaciales.",
      fields: [
        { name: "include_inferred", label: "Incluir inferencias", type: "checkbox", checked: true },
        { name: "include_hypotheses", label: "Incluir hipótesis", type: "checkbox", checked: true }
      ]
    },
    {
      name: "expand_relations",
      label: "expand_relations · Recorrido lógico",
      desc: "Recorrido determinista acotado por las relaciones del grafo lógico.",
      fields: [
        { name: "origin_id", label: "ID del nodo origen", type: "text", required: true, placeholder: "ej. episode_south_repair" },
        { name: "max_depth", label: "Profundidad máxima (1-5)", type: "number", value: 2, min: 1, max: 5 }
      ]
    },
    {
      name: "get_supporting_evidence",
      label: "get_supporting_evidence · Evidencias de respaldo",
      desc: "Evidencias verificables de observación, episodio, relación, hipótesis, entidad o activo.",
      fields: [
        { name: "target_kind", label: "Tipo de objetivo", type: "select", options: ["observation", "episode", "relation", "hypothesis", "entity", "asset"] },
        { name: "target_id", label: "ID del objetivo", type: "text", required: true, placeholder: "ej. observation_drill_south" }
      ]
    },
    {
      name: "get_episode",
      label: "get_episode · Detalle canónico de episodio",
      desc: "Recupera el detalle canónico completo de un episodio.",
      fields: [{ name: "episode_id", label: "ID del Episodio", type: "text", required: true, placeholder: "ej. episode_south_repair" }]
    },
    {
      name: "search_episodes",
      label: "search_episodes · Recuperación híbrida",
      desc: "Episodios rehidratados con observaciones y evidencias.",
      fields: [
        { name: "query", label: "Texto de consulta", type: "text", required: true, placeholder: "ej. mantenimiento taller" },
        { name: "limit", label: "Límite", type: "number", value: 5 }
      ]
    }
  ];

  let toolsLoaded = false;
  async function loadToolsCatalog() {
    if (toolsLoaded) return;
    const select = $("#tool-select");
    if (!select) return;

    select.innerHTML = TOOL_DEFS.map(t => `<option value="${esc(t.name)}">${esc(t.label)}</option>`).join("");
    select.addEventListener("change", () => renderToolForm(select.value));
    renderToolForm(TOOL_DEFS[0].name);
    toolsLoaded = true;
  }

  function renderToolForm(toolName) {
    const def = TOOL_DEFS.find(t => t.name === toolName) || TOOL_DEFS[0];
    const desc = $("#tool-description");
    if (desc) desc.textContent = def.desc;

    const fieldsContainer = $("#tool-fields");
    if (!fieldsContainer) return;

    fieldsContainer.innerHTML = def.fields.map(f => {
      if (f.type === "select") {
        const opts = (f.options || []).map(opt => `<option value="${esc(opt)}">${opt ? esc(opt) : "(Cualquiera)"}</option>`).join("");
        return `<label>${esc(f.label)}<select name="${esc(f.name)}">${opts}</select></label>`;
      }
      if (f.type === "checkbox") {
        return `<label class="switch"><input type="checkbox" name="${esc(f.name)}" ${f.checked ? "checked" : ""}><span></span>${esc(f.label)}</label>`;
      }
      return `<label>${esc(f.label)}
        <input type="${f.type || "text"}" name="${esc(f.name)}" ${f.value !== undefined ? `value="${esc(f.value)}"` : ""}
          ${f.placeholder ? `placeholder="${esc(f.placeholder)}"` : ""} ${f.required ? "required" : ""}
          ${f.min !== undefined ? `min="${f.min}"` : ""} ${f.max !== undefined ? `max="${f.max}"` : ""}>
      </label>`;
    }).join("");
  }

  $("#tool-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const submit = $("#tool-submit");
    const root = $("#tool-results");
    const toolName = $("#tool-select").value;
    const formData = new FormData(event.currentTarget);
    const payload = {};
    const def = TOOL_DEFS.find(t => t.name === toolName);
    const fieldTypes = new Map((def?.fields || []).map(f => [f.name, f.type]));

    for (const [key, value] of formData.entries()) {
      if (value === "") continue;
      const type = fieldTypes.get(key);
      // Solo los campos numéricos se convierten: un ID "123" sigue siendo texto.
      if (type === "checkbox") payload[key] = true;
      else if (type === "number") payload[key] = Number(value);
      else payload[key] = value;
    }
    if (def) {
      def.fields.filter(f => f.type === "checkbox").forEach(f => {
        if (!(f.name in payload)) payload[f.name] = false;
      });
    }

    submit.disabled = true;
    submit.textContent = "Consultando...";
    root.innerHTML = '<div class="empty roomy">Ejecutando herramienta contra la memoria canónica…</div>';

    try {
      const output = await api(`/api/tools/${encodeURIComponent(toolName)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      renderToolOutput(output);
    } catch (err) {
      root.innerHTML = `<div class="empty roomy"><b>Error ejecutando ${esc(toolName)}</b><span>${esc(err.message)}</span></div>`;
    } finally {
      submit.disabled = false;
      submit.textContent = "Ejecutar herramienta";
    }
  });

  function renderToolOutput(out) {
    const root = $("#tool-results");
    const obsHtml = (out.observations || []).map(obs => `
      <article class="extraction-card">
        <div class="extraction-number">OBS</div>
        <div class="extraction-body">
          <div class="extraction-head">
            <span class="modality-tag ${esc(obs.modality)}">${esc(modalityLabel(obs.modality))}</span>
            <b>${esc(observationLabel(obs.observation_type))}</b>
            <em>${esc(confidenceLabel(obs.confidence))} · ${esc(obs.validation_state || "observado")}</em>
          </div>
          <div class="extracted-content"><p>${esc(obs.description || obs.content || "Sin descripción")}</p></div>
          <div class="observation-origin">
            <span>Episodio</span><b>${esc(obs.episode_id)}</b>
            ${obs.start_time ? `<small>${fmtInterval(obs)}</small>` : ""}
          </div>
        </div>
      </article>`).join("");

    const relsHtml = (out.relations || []).map(rel => {
      const isContra = rel.relation_type === "CONTRADICTS";
      return `
      <article class="event-card ${isContra ? "contradiction-card" : ""}">
        <div class="extraction-head">
          <span class="modality-tag ${isContra ? "contradiction-badge" : "document"}">${esc(rel.relation_type)}</span>
          <b>${esc(rel.origin_id)} → ${esc(rel.destination_id)}</b>
          <em>${esc(rel.validation_state || "relación")}</em>
        </div>
        <p>Método: <code>${esc(rel.method || "—")}</code></p>
      </article>`;
    }).join("");

    const docsHtml = (out.documents || []).map(doc => `
      <article class="origin-card">
        <div class="origin-type"><span>Documento</span><small>Página ${doc.page_number || "—"}</small></div>
        <h4>${esc(doc.snippet || doc.text_quote || "Fragmento")}</h4>
        <div class="origin-evidence"><span>Asset: ${esc(doc.asset_id)}</span></div>
      </article>`).join("");

    const compsHtml = (out.comparisons || []).map(comp => `
      <article class="event-card">
        <b>Comparación: ${esc(comp.entity_id_a)} vs ${esc(comp.entity_id_b)}</b>
        <p>Resultado: <strong>${esc(comp.status)}</strong></p>
        <small>${esc(comp.explanation || "")}</small>
      </article>`).join("");

    const resultCount = (out.result_ids || []).length;

    root.innerHTML = `
      <div class="panel">
        <div class="tool-output-head">
          <div><b>Herramienta: ${esc(out.tool)}</b> <span>${resultCount} ID(s) devuelto(s)</span></div>
          <span>Confianza: ${out.confidence !== null && out.confidence !== undefined ? `${Math.round(out.confidence * 100)}%` : "—"}</span>
        </div>
        <div style="padding: 22px;">
          ${obsHtml ? `<div class="tool-card-section"><h4>Observaciones verificadas (${(out.observations || []).length})</h4>${obsHtml}</div>` : ""}
          ${relsHtml ? `<div class="tool-card-section"><h4>Relaciones y contradicciones (${(out.relations || []).length})</h4>${relsHtml}</div>` : ""}
          ${docsHtml ? `<div class="tool-card-section"><h4>Citas documentales (${(out.documents || []).length})</h4>${docsHtml}</div>` : ""}
          ${compsHtml ? `<div class="tool-card-section"><h4>Comparaciones (${(out.comparisons || []).length})</h4>${compsHtml}</div>` : ""}
          ${!obsHtml && !relsHtml && !docsHtml && !compsHtml ? `<div class="empty">La consulta finalizó con éxito pero no devolvió registros coincidentes.</div>` : ""}
        </div>
      </div>`;
  }

  // Foco al abrir un episodio desde un resultado: el momento exacto.
  function applyEpisodeFocus(root, episodeId) {
    const focus = state.episodeFocus;
    if (!focus || focus.episodeId !== episodeId) return;
    state.episodeFocus = null;
    let target = focus.observationId ? root.querySelector(`[data-observation-id="${CSS.escape(focus.observationId)}"]`) : null;
    if (!target && focus.seek != null) {
      const cards = [...root.querySelectorAll("[data-event-start]")].filter(card => card.dataset.eventStart !== "");
      target = cards.filter(card => Number(card.dataset.eventStart) <= focus.seek + 0.01).pop() || null;
    }
    const video = root.querySelector("#episode-video");
    if (video && focus.seek != null) video.currentTime = focus.seek;
    const element = target || (focus.seek != null ? video : null);
    if (!element) return;
    element.classList.add("focus-flash");
    setTimeout(() => element.scrollIntoView({ behavior: "smooth", block: "center" }), 60);
    setTimeout(() => element.classList.remove("focus-flash"), 2600);
  }

  // Umbrales de la búsqueda por pasajes: los de .env, visibles y ajustables por consulta.
  let passageSettings = null;
  const fillThresholds = () => {
    if (!passageSettings) return;
    $("#input-min-score").value = passageSettings.rerank_min_score ?? "";
    $("#input-agreement").value = passageSettings.agreement_rank ?? 0;
  };
  async function loadPassageSettings() {
    try { passageSettings = await api("/api/passage-search/settings"); } catch { return; }
    state.indexStatus = await api("/api/index/status").catch(() => null);
    fillThresholds();
    const s = passageSettings;
    $("#passage-settings").textContent = `.env: umbral ${s.rerank_min_score ?? "ninguno"} · acuerdo top ${s.agreement_rank} · ${s.candidates} candidatos por vía · ${s.rerank_candidates} al reordenador (${s.rerank_model || "sin modelo"}) · ${s.indexed_passages} pasajes indexados`;
  }
  $("#reset-thresholds")?.addEventListener("click", fillThresholds);
  loadPassageSettings();
  const thresholdOverrides = () => {
    const minScore = $("#input-min-score").value.trim();
    const agreement = $("#input-agreement").value.trim();
    return { rerank_min_score: minScore === "" ? null : Number(minScore), agreement_rank: agreement === "" ? 0 : Number(agreement) };
  };

  const searchFilters = () => ({
    environment_filter: $("#search-environment").value ? [$("#search-environment").value] : null,
    source_filter: $("#search-source").value ? [$("#search-source").value] : null,
    modality_filter: $("#search-modality").value ? [$("#search-modality").value] : null,
  });
  $("#search-form")?.addEventListener("submit", async event => {
    event.preventDefault(); const root = $("#search-results"); root.innerHTML = '<div class="empty roomy">Buscando por significado y por palabras en cada pasaje…</div>';
    const text = $("#search-query").value.trim();
    const json = body => ({ method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
    // El estado del índice se carga con la vista y tras reindexar, no en cada consulta.
    if (!state.indexStatus) state.indexStatus = await api("/api/index/status").catch(() => null);
    try { renderPassageSearch(await api("/api/passage-search", json({ text_query: text, ...searchFilters(), rerank: $("#input-rerank").checked, ...thresholdOverrides() }))); }
    catch (error) { root.innerHTML = `<div class="empty roomy"><b>Error de recuperación</b><span>${esc(error.message)}</span></div>`; }
  });

  const passageTypeLabel = hit => ({ video_event: "Evento", speech_segment: "Habla", document_text_fragment: "Documento", text_fragment: "Texto" })[hit.kind] || observationLabel(hit.kind);
  // El índice marca los términos encontrados con los caracteres de control 2 y 3.
  const marked = text => esc(text).replace(/\u0002/g, "<mark>").replace(/\u0003/g, "</mark>");
  const clip = (text, size = 320) => text.length > size ? `${text.slice(0, size).trimEnd()}…` : text;

  function renderPassageSearch(data) {
    const root = $("#search-results");
    const rerank = data.rerank || {};
    const rules = `umbral ${rerank.min_score ?? "ninguno"} · acuerdo top ${rerank.agreement_rank || "no"}`;
    const discarded = rerank.discarded_low_relevance ? ` · ${rerank.discarded_low_relevance} descartado(s)` : "";
    const rerankText = rerank.applied ? `reordenado con ${rerank.model} (${rules})${discarded}` : rerank.error ? `sin reordenar: ${rerank.error}` : "sin reordenar";
    const summary = `<div class="passage-summary"><b>${data.passage_count} momento(s) en ${data.groups.length} episodio(s)</b>
      <span>significado: ${data.dense_candidates} candidatos</span><span>palabras: ${data.lexical_candidates}${data.lexical_query ? ` (${esc(data.lexical_query)})` : ""}</span>
      <span class="${rerank.error ? "warn" : ""}">${esc(rerankText)}</span><span>${Math.round(data.took_ms)} ms</span></div>`;
    const renderGroups = list => list.map((group, index) => {
      const episode = group.episode;
      const hits = group.passages.map(hit => {
        const seekable = hit.start_offset_seconds != null && (hit.kind === "video_event" || hit.modality === "video");
        const where = [hit.start_time ? fmtTime(hit.start_time) : "", hit.start_offset_seconds != null ? `${fmtClock(hit.start_offset_seconds)}${hit.end_offset_seconds != null ? `–${fmtClock(hit.end_offset_seconds)}` : ""} en el ${hit.kind === "video_event" ? "vídeo" : "archivo"}` : "", hit.page_number ? `pág. ${hit.page_number}` : ""].filter(Boolean).join(" · ");
        const seekAttrs = `data-passage-episode="${esc(hit.episode_id)}" data-passage-observation="${esc(hit.observation_id || "")}" ${seekable ? `data-passage-seek="${esc(hit.start_offset_seconds)}"` : ""}`;
        const thumb = hit.kind === "video_event" && hit.event_id && hit.keyframes > 0
          ? `<button type="button" class="passage-thumb" ${seekAttrs} title="Ver en el vídeo"><img loading="lazy" alt="" src="/api/memory/events/${encodeURIComponent(hit.event_id)}/keyframes/0"></button>`
          : "";
        const why = [
          hit.scores.dense_rank ? `<span class="dense" title="Similitud ${Number(hit.scores.dense).toFixed(3)}">Significado #${hit.scores.dense_rank}</span>` : "",
          hit.scores.lexical_rank ? `<span class="lexical" title="BM25 ${Number(hit.scores.lexical).toFixed(2)}">Palabras #${hit.scores.lexical_rank}</span>` : "",
          hit.scores.rerank != null ? `<span class="rerank">Reordenado ${Number(hit.scores.rerank).toFixed(2)}</span>` : "",
          hit.kept_by === "agreement" ? `<span class="agreement" title="Bajo el umbral, pero significado y palabras coinciden entre sus ${rerank.agreement_rank} primeros">Ambas vías coinciden</span>` : "",
          hit.model ? `<span title="Contenido generado por un modelo; no verificado">${esc(hit.model)}</span>` : "",
        ].join("");
        return `<article class="passage-hit${thumb ? " has-thumb" : ""}">${thumb}<div class="passage-body">
          <header><span class="modality-tag ${esc(hit.modality || "")}">${esc(passageTypeLabel(hit))}</span>${hit.title ? `<b>${esc(hit.title)}</b>` : ""}<time>${esc(where)}</time></header>
          <p class="passage-text">${hit.snippet ? marked(hit.snippet) : esc(clip(hit.text))}</p>
          <footer><span class="passage-why">${why}</span><button type="button" class="button-mini" ${seekAttrs}>Ver en el episodio${seekable ? ` ▶ ${fmtClock(hit.start_offset_seconds)}` : ""}</button></footer>
          ${hit.text.length > 320 || hit.snippet ? `<details><summary>Texto completo</summary>${paragraphs(hit.text)}</details>` : ""}
        </div></article>`;
      }).join("");
      const env = (episode.environment_ids || []).map(environmentLabel).join(", ");
      return `<section class="passage-group"><header><span class="result-rank">${index + 1}</span><div><h3>${esc(episode.title || episode.external_id || episode.id)}</h3><small>${esc([env, fmtInterval(episode)].filter(Boolean).join(" · "))}</small></div><button type="button" class="button ghost" data-passage-episode="${esc(episode.id)}">Abrir episodio</button></header>${hits}</section>`;
    }).join("");
    const groups = renderGroups(data.groups);
    const discardedCount = (data.discarded_groups || []).reduce((total, group) => total + group.passages.length, 0);
    const discardedHtml = discardedCount
      ? `<details class="discarded-results"><summary>Descartados por relevancia baja (${discardedCount}) · ${esc(rules)}</summary><div class="search-results">${renderGroups(data.discarded_groups)}</div></details>`
      : "";
    const index = state.indexStatus;
    const indexWarning = index?.job
      ? `<div class="empty roomy index-warning"><b>Reindexando la memoria</b><span>${esc(index.reasons.join("; ") || "pedido a mano")}. Los resultados pueden estar incompletos hasta que termine; el progreso se ve en Vigilancia.</span></div>`
      : data.indexed_passages === 0
      ? `<div class="empty roomy index-warning"><b>No hay pasajes indexados</b><span>${index && !index.up_to_date ? esc(index.reasons.join("; ")) + ". " : ""}Se reindexa solo al arrancar el servidor; también puede lanzarlo con «Reindexar» en Vigilancia.</span></div>`
      : "";
    const emptyText = data.indexed_passages === 0 ? ""
      : rerank.discarded_low_relevance ? '<div class="empty roomy">Hay candidatos, pero ninguno llega al umbral. Revísalos abajo, baja el umbral o desactiva «Reordenar».</div>'
      : '<div class="empty roomy">Ningún pasaje coincide. Prueba otras palabras o quita filtros; si acabas de cargar capturas, espera a que terminen de indexarse.</div>';
    root.innerHTML = `${indexWarning}${summary}${data.groups.length ? groups : emptyText}${discardedHtml}`;
    root.querySelectorAll("[data-passage-episode]").forEach(button => button.addEventListener("click", () => {
      const seek = button.dataset.passageSeek;
      openEpisode(button.dataset.passageEpisode, button.dataset.passageObservation
        ? { observationId: button.dataset.passageObservation, seek: seek === undefined ? null : Number(seek) }
        : null);
    }));
  }

  // -- Vigilancia: carpetas, ficheros leídos, trabajos y registro ----------

  const originLabel = origin => {
    if (!origin || !origin.kind) return "subida";
    if (origin.kind === "watch") return `carpeta «${origin.source}»`;
    return ({ upload: "subida desde el front", cli: "línea de comandos" })[origin.kind] || origin.kind;
  };
  const inboxStateLabel = value => ({
    pending: "Copiándose", ready: "Listo", captured: "Capturado", duplicate: "Duplicado",
    ignored: "Ignorado", failed: "Fallido", vanished: "Desaparecido",
  })[value] || value;
  const jobStateLabel = value => ({
    queued: "En cola", running: "En curso", completed: "Completado", failed: "Fallido",
  })[value] || value;
  const groupingLabel = source => ({
    manifest: "Cada subcarpeta con su manifiesto epigraph.capture/v1",
    folder: "Cada subcarpeta es una captura",
    file: "Cada fichero es una captura",
    time_gap: `Ficheros a menos de ${fmtDeltaSeconds(source.max_gap_seconds)} entre sí`,
  })[source.grouping] || source.grouping;
  const closeRuleLabel = source => {
    if (source.grouping === "manifest") {
      return `al aparecer «${source.manifest_file}» con todos sus ficheros completos (espera hasta ${fmtDeltaSeconds(source.missing_files_timeout_seconds)} por los que falten)`;
    }
    if (source.grouping !== "folder") return "se captura en cuanto está completo";
    return source.ready_marker
      ? `se cierra al aparecer «${source.ready_marker}»`
      : `se cierra tras ${fmtDeltaSeconds(source.close_after_seconds)} sin cambios`;
  };
  const fmtBytes = size => {
    let value = Number(size || 0);
    for (const unit of ["B", "KB", "MB", "GB"]) {
      if (value < 1024 || unit === "GB") return unit === "B" ? `${value} B` : `${value.toFixed(1)} ${unit}`;
      value /= 1024;
    }
    return `${size} B`;
  };
  const fmtClockTime = value => value ? new Date(value).toLocaleTimeString("es-ES") : "—";

  function startWatchPolling() {
    stopWatchPolling();
    refreshWatch(true);
    state.watchTimer = setInterval(() => { if (!document.hidden) refreshWatch(false); }, 2000);
  }
  function stopWatchPolling() {
    if (state.watchTimer) clearInterval(state.watchTimer);
    state.watchTimer = null;
  }

  async function refreshWatch(full) {
    if (state.watchBusy) return;
    state.watchBusy = true;
    try {
      const fileState = $("#watch-file-state").value;
      const filesUrl = `/api/watch/files?limit=300${fileState ? `&state=${encodeURIComponent(fileState)}` : ""}`;
      const [status, jobs, files, index] = await Promise.all([api("/api/watch"), api("/api/jobs?limit=30"), api(filesUrl), api("/api/index/status").catch(() => null)]);
      renderIndexStatus(index);
      renderWatchStatus(status);
      renderWatchJobs(jobs);
      renderWatchFiles(files.files);
      await loadActivity(full);
    } catch (error) {
      // Sin respuesta útil, ningún panel debe quedarse en «Cargando…».
      const message = error.status === 404
        ? "Este servidor no tiene la API de vigilancia: se arrancó con una versión anterior. Reinicie «epigraph serve»."
        : `No se pudo consultar la vigilancia: ${error.message}`;
      $("#watch-state").className = "watch-state error";
      $("#watch-state b").textContent = error.status === 404 ? "Servidor desactualizado" : "Error";
      $("#watch-summary").textContent = message;
      $("#watch-scan-now").disabled = true;
      ["#watch-sources", "#watch-jobs", "#watch-files"].forEach(selector => {
        $(selector).innerHTML = `<div class="empty">${esc(message)}</div>`;
      });
      $("#activity-log").innerHTML = `<div class="log-empty">${esc(message)}</div>`;
      state.logFilterKey = null;
    } finally {
      state.watchBusy = false;
    }
  }

  function renderWatchStatus(status) {
    const light = $("#watch-state");
    const label = light.querySelector("b");
    const lease = status.lease;
    const other = lease && lease.active && !lease.this_process;
    let kind = "idle"; let text; let summary;
    if (status.configured && status.running && status.has_lease) {
      kind = "ok"; text = "Vigilando";
      summary = `Cada ${fmtDeltaSeconds(status.interval_seconds)} · un fichero está completo tras ${fmtDeltaSeconds(status.settle_seconds)} sin cambios · pase ${status.pass_count || 0} a las ${fmtClockTime(status.last_scan_at)}`;
    } else if (status.configured && status.running) {
      kind = "warn"; text = "En espera";
      summary = `Otro proceso vigila estas carpetas${lease ? ` (${lease.owner})` : ""}; este toma el relevo si se detiene.`;
    } else if (other) {
      kind = "ok"; text = "Vigilando desde otro proceso";
      summary = `Este servidor no vigila, pero ${lease.owner} sí: sus ficheros, trabajos y registro aparecen aquí igualmente.`;
    } else if (status.configured) {
      text = "Detenida";
      summary = "La configuración existe, pero este servidor se arrancó sin vigilancia (--no-watch).";
    } else {
      text = "Sin vigilancia";
      summary = "Defina EPIGRAPH_WATCH_CONFIG con un fichero de carpetas (ver watch.example.toml) y reinicie, o ejecute «epigraph watch --config …».";
    }
    const awaitingTotal = status.sources.reduce((total, source) => total + (source.awaiting_manifest?.length || 0), 0);
    if (awaitingTotal) { kind = kind === "ok" ? "warn" : kind; summary += ` · ${awaitingTotal} carpeta(s) completas sin manifiesto: no se procesarán hasta tenerlo`; }
    state.awaitingGroups = new Set(status.sources.flatMap(source => (source.awaiting_manifest || []).map(item => `${source.name}\u0000${item.group}`)));
    if (status.last_error) { kind = "error"; summary += ` · Último error: ${status.last_error}`; }
    light.className = `watch-state ${kind}`;
    label.textContent = text;
    $("#watch-summary").textContent = status.config_path ? `${summary} · ${status.config_path}` : summary;
    $("#watch-scan-now").disabled = !(status.running && status.has_lease);

    const container = $("#watch-sources");
    if (!status.sources.length) { container.innerHTML = '<div class="empty">No hay carpetas configuradas en este servidor.</div>'; return; }
    container.innerHTML = status.sources.map(source => {
      const counts = source.counts || {};
      const chips = ["pending", "ready", "captured", "duplicate", "ignored", "failed"]
        .filter(key => counts[key])
        .map(key => `<span class="state-chip ${key}">${esc(inboxStateLabel(key))} ${counts[key]}</span>`).join("") || '<span class="state-chip">Sin ficheros todavía</span>';
      const availability = source.available === false ? `<span class="source-flag error">NO DISPONIBLE</span>`
        : source.enabled === false ? `<span class="source-flag">DESACTIVADA</span>`
        : source.available ? `<span class="source-flag ok">${source.files_on_disk} EN CARPETA</span>` : "";
      const after = source.after === "move" ? `mueve los originales a ${source.archive_dir}` : "conserva los originales en su sitio";
      return `<div class="source-card">
        <header><h3>${esc(source.name)}</h3>${availability}</header>
        <code>${esc(source.path)}</code>
        <dl>
          <dt>Ambiente</dt><dd>${esc(source.environment_id || "lo declara cada manifiesto")}</dd>
          <dt>Agrupación</dt><dd>${esc(groupingLabel(source))}</dd>
          <dt>Cierre</dt><dd>${esc(closeRuleLabel(source))}</dd>
          <dt>Después</dt><dd>${esc(after)}</dd>
        </dl>
        <div class="state-chips">${chips}</div>
        ${awaitingManifestHtml(source)}
        ${source.error && source.available !== false ? `<p class="source-error">${esc(source.error)}</p>` : ""}
      </div>`;
    }).join("");
    container.querySelectorAll("[data-draft-group]").forEach(button => button.addEventListener("click", () => createManifests(button.dataset.draftSource, [button.dataset.draftGroup])));
    container.querySelectorAll("[data-draft-all]").forEach(button => button.addEventListener("click", () => {
      const source = status.sources.find(item => item.name === button.dataset.draftAll);
      createManifests(source.name, source.awaiting_manifest.filter(item => item.draftable).map(item => item.group));
    }));
  }

  function awaitingManifestHtml(source) {
    const awaiting = source.awaiting_manifest || [];
    if (!awaiting.length) return "";
    const draftable = awaiting.filter(item => item.draftable).length;
    const rows = awaiting.map(item => `<div class="awaiting-row">
        <code>${esc(item.folder)}</code>
        ${item.draftable ? `<button type="button" class="button-mini" data-draft-source="${esc(source.name)}" data-draft-group="${esc(item.group)}">Crear manifiesto</button>` : "<span></span>"}
        <small>${item.files} fichero(s) completos · esperando desde ${esc(fmtClockTime(item.since))}</small>
        ${item.draft_error ? `<small class="awaiting-error">${esc(item.draft_error)}</small>` : ""}
      </div>`).join("");
    return `<div class="awaiting">
      <header><h4>Sin ${esc(source.manifest_file)} (${awaiting.length})</h4>
        ${draftable > 1 ? `<button type="button" class="button-mini" data-draft-all="${esc(source.name)}">Crear los ${draftable}</button>` : ""}</header>
      <p>La herramienta de captura escribe el manifiesto al terminar. Si la carga es manual, EpiGraph puede proponerlo a partir del nombre <code>&lt;entorno&gt;_&lt;epoch&gt;</code>: un dispositivo por fichero.</p>
      ${rows}
    </div>`;
  }

  // Carga manual: se pide el borrador, se enseña y solo se escribe si se confirma.
  async function createManifests(source, groups) {
    if (!groups.length) return;
    const drafts = [];
    for (const group of groups) {
      const body = new FormData();
      body.set("source", source); body.set("group_key", group);
      try { drafts.push({group, ...(await api("/api/watch/manifest", {method: "POST", body}))}); }
      catch (error) { alert(`${group}: ${error.message}`); return; }
    }
    const describe = draft => {
      const manifest = draft.manifest;
      const when = new Date(manifest.captured_at * 1000).toISOString().replace(".000Z", "Z");
      const devices = manifest.devices.map(device => `   · ${device.id} (${device.kind}): ${device.files.join(", ")}`).join("\n");
      return `${draft.group}\n   entorno ${manifest.environment_id} · captured_at ${manifest.captured_at} (${when})\n${devices}`;
    };
    const text = `Se escribirá ${drafts.length === 1 ? "este manifiesto" : `${drafts.length} manifiestos`} (capture.json), deducido(s) del nombre de la carpeta y de sus ficheros:\n\n${drafts.map(describe).join("\n\n")}\n\nRevise el entorno, el instante y los dispositivos. ¿Continuar?`;
    if (!confirm(text)) return;
    for (const draft of drafts) {
      const body = new FormData();
      body.set("source", source); body.set("group_key", draft.group); body.set("write", "true");
      try { await api("/api/watch/manifest", {method: "POST", body}); }
      catch (error) { alert(`${draft.group}: ${error.message}`); break; }
    }
    await refreshWatch(false);
  }

  // Estado del índice: al día, reindexándose (trabajo en la cola) o desfasado.
  function renderIndexStatus(index) {
    const box = $("#index-status");
    if (!box || !index) return;
    const running = index.job;
    const counts = `${index.episodes} episodio(s) · ${index.points} puntos en Qdrant · ${index.indexed_passages} pasajes`;
    const text = running
      ? `Índice: reindexando (${jobStateLabel(running.state).toLowerCase()}): ${index.reasons.join("; ") || "pedido a mano"}`
      : index.up_to_date ? `Índice al día · ${counts}` : `Índice desfasado: ${index.reasons.join("; ")}`;
    box.className = `index-status${index.up_to_date && !running ? "" : " stale"}`;
    $("#index-status-text").textContent = text;
    $("#index-reindex").disabled = Boolean(running);
  }
  $("#index-reindex")?.addEventListener("click", async event => {
    if (!confirm("Se reconstruirán los vectores y los pasajes de toda la memoria en segundo plano. La búsqueda sigue funcionando mientras tanto. ¿Continuar?")) return;
    event.currentTarget.disabled = true;
    try { await api("/api/index/reindex", {method: "POST"}); state.indexStatus = null; setTimeout(() => refreshWatch(false), 400); }
    catch (error) { alert(error.message); }
  });

  function renderWatchJobs(data) {
    $("#watch-jobs-active").textContent = data.active_count ? `${data.active_count} activo(s)` : "En reposo";
    const container = $("#watch-jobs");
    if (!data.jobs.length) { container.innerHTML = '<div class="empty">Todavía no hay trabajos.</div>'; return; }
    container.innerHTML = data.jobs.map(job => {
      const capture = job.capture;
      const percent = Math.round((job.progress || 0) * 100);
      const current = job.stages.find(stage => stage.state === "running") || job.stages.find(stage => stage.state === "failed");
      const stages = job.stages.map(stage => `<span class="stage-pill ${esc(stage.state)}" title="${esc(stage.label)}${stage.detail ? `: ${esc(stage.detail)}` : ""}">${stageIcon(stage.state)} ${esc(stage.label)}</span>`).join("");
      const action = capture?.episode_id && job.state === "completed"
        ? `<button type="button" class="button-mini" data-open-episode="${esc(capture.episode_id)}">Abrir episodio</button>` : "";
      return `<div class="job-card ${esc(job.state)}">
        <header>
          <div><strong>${esc(capture?.title || job.title || job.target_id)}</strong>
            <small>${esc(job.job_type === "reindex" ? `pedido ${job.target_id === "manual" ? "a mano" : "al arrancar"}` : originLabel(capture?.origin))} · ${capture ? `${capture.asset_count} fichero(s) · ` : ""}${fmtClockTime(job.created_at)}${job.attempts > 1 ? ` · intento ${job.attempts}` : ""}</small></div>
          <span class="capture-status ${job.state === "completed" ? "ready" : job.state === "failed" ? "failed" : "processing"}">${esc(jobStateLabel(job.state))}${job.state === "running" ? ` ${percent}%` : ""}</span>
        </header>
        <div class="job-bar"><i style="width:${percent}%"></i></div>
        <div class="stage-pills">${stages}</div>
        ${current?.detail ? `<p class="job-detail">${esc(current.label)}: ${esc(current.detail)}</p>` : ""}
        ${job.error ? `<p class="job-error">${esc(job.error)}</p>` : ""}
        ${action ? `<div class="job-card-actions">${action}</div>` : ""}
      </div>`;
    }).join("");
    container.querySelectorAll("[data-open-episode]").forEach(button => button.addEventListener("click", () => openEpisode(button.dataset.openEpisode)));
  }

  function renderWatchFiles(files) {
    const container = $("#watch-files");
    if (!files.length) { container.innerHTML = '<div class="empty">Ningún fichero con este estado.</div>'; return; }
    const retryable = new Set();
    container.innerHTML = files.map(file => {
      const groupId = `${file.source_name}\u0000${file.group_key}`;
      const retry = file.state === "failed" && file.present && !retryable.has(groupId);
      if (retry) retryable.add(groupId);
      const archivedTail = file.archived_to ? file.archived_to.split(/[\\/]/).slice(-3).join("/") : "";
      const where = file.archived_to ? `archivado en …/${archivedTail}` : file.present ? "" : "ya no está en la carpeta";
      const waiting = file.state === "ready" && !file.capture_id && state.awaitingGroups?.has(groupId) ? "esperando el manifiesto de su carpeta" : "";
      const note = [file.error, waiting, where].filter(Boolean).join(" · ");
      return `<div class="inbox-row">
        <span class="state-chip ${esc(file.state)}">${esc(inboxStateLabel(file.state))}</span>
        <div><strong title="${esc(file.rel_path)}">${esc(file.rel_path)}</strong>
          <small>${esc(file.source_name)}${file.group_key ? ` · grupo ${esc(file.group_key)}` : ""}${file.media_type ? ` · ${esc(modalityLabel(file.media_type))}` : ""} · ${fmtBytes(file.size_bytes)} · ${fmtClockTime(file.updated_at)}</small>
          ${note ? `<small class="inbox-note" title="${esc(file.archived_to || "")}">${esc(note)}</small>` : ""}</div>
        <div class="inbox-actions">
          ${file.capture_id ? `<button type="button" class="button-mini" data-open-capture="${esc(file.capture_id)}">Episodio</button>` : ""}
          ${retry ? `<button type="button" class="button-mini" data-retry-source="${esc(file.source_name)}" data-retry-group="${esc(file.group_key)}">Reintentar grupo</button>` : ""}
        </div>
      </div>`;
    }).join("");
    container.querySelectorAll("[data-open-capture]").forEach(button => button.addEventListener("click", async () => {
      const capture = await api(`/api/captures/${encodeURIComponent(button.dataset.openCapture)}`);
      if (capture.episode_id) openEpisode(capture.episode_id);
      else alert("La captura aún se está procesando: su episodio aparecerá al terminar el trabajo.");
    }));
    container.querySelectorAll("[data-retry-group]").forEach(button => button.addEventListener("click", async () => {
      const body = new FormData();
      body.set("source", button.dataset.retrySource); body.set("group_key", button.dataset.retryGroup);
      button.disabled = true;
      try { await api("/api/watch/retry", {method: "POST", body}); await refreshWatch(false); }
      catch (error) { alert(error.message); button.disabled = false; }
    }));
  }

  async function loadActivity(full) {
    const component = $("#log-component").value;
    const levels = $("#log-level").value;
    const filterKey = `${component}|${levels}`;
    const log = $("#activity-log");
    const reset = full || filterKey !== state.logFilterKey;
    const params = new URLSearchParams({limit: reset ? "300" : "500"});
    if (!reset) params.set("after_id", String(state.logLastId));
    if (component) params.set("component", component);
    levels.split(",").filter(Boolean).forEach(level => params.append("level", level));
    const data = await api(`/api/activity?${params}`);
    if (reset) { log.innerHTML = ""; state.logFilterKey = filterKey; }
    state.logLastId = data.last_id;
    if (!data.entries.length) {
      if (!log.children.length) log.innerHTML = '<div class="log-empty">Sin actividad registrada todavía.</div>';
      return;
    }
    log.querySelector(".log-empty")?.remove();
    data.entries.forEach(entry => appendLogLine(log, entry));
    while (log.children.length > 1000) log.firstElementChild.remove();
    if ($("#log-follow").checked) log.scrollTop = log.scrollHeight;
  }

  // Cada pase del vigilante es una línea; los pases seguidos sin novedades
  // sobre la misma carpeta se funden en la última, que avanza su número y su
  // hora y cuenta las repeticiones, para que lo demás no se pierda de vista.
  function appendLogLine(log, entry) {
    const data = entry.data || {};
    const idlePass = entry.level === "debug" && data.idle && data.summary;
    const last = log.lastElementChild;
    if (idlePass && last?.dataset.idleSummary === data.summary) {
      const repeats = Number(last.dataset.repeats || 1) + 1;
      last.dataset.repeats = String(repeats);
      last.title = fmtTime(entry.at);
      last.querySelector("time").textContent = fmtClockTime(entry.at);
      last.querySelector(".log-msg").innerHTML = `${esc(entry.message)}<span class="log-repeat">×${repeats} desde ${esc(last.dataset.since)}</span>`;
      return;
    }
    const component = entry.component === "watch" ? "VIGILANTE" : entry.component === "jobs" ? "TRABAJO" : entry.component.toUpperCase();
    const classes = ["log-line", entry.level];
    if (entry.level === "debug" && !data.idle) classes.push("busy");
    if (data.step) classes.push("step");
    log.insertAdjacentHTML("beforeend", `<div class="${esc(classes.join(" "))}" title="${esc(fmtTime(entry.at))}"><time>${esc(fmtClockTime(entry.at))}</time><span class="log-comp">${esc(component)}</span><span class="log-msg">${esc(entry.message)}</span></div>`);
    if (idlePass) {
      const line = log.lastElementChild;
      line.dataset.idleSummary = data.summary;
      line.dataset.since = fmtClockTime(entry.at);
    }
  }

  $("#watch-scan-now").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await api("/api/watch/scan", {method: "POST"}); setTimeout(() => refreshWatch(false), 600); }
    catch (error) { alert(error.message); }
  });
  $("#watch-file-state").addEventListener("change", () => refreshWatch(false));
  [$("#log-component"), $("#log-level")].forEach(select => select.addEventListener("change", () => loadActivity(true).catch(error => console.error(error))));

  async function loadSystem() {
    const [health, indexing, project] = await Promise.all([loadHealth(), api("/api/indexing/summary"), api("/api/project-status")]);
    $("#runtime-details").innerHTML = `<dt>Punto de control</dt><dd>${esc(project.checkpoint)}</dd><dt>Backend</dt><dd>${esc(health.vector_backend)}</dd><dt>Colección</dt><dd>${esc(health.collection_name)}</dd><dt>Generación</dt><dd>${esc(health.active_generation || "—")}</dd><dt>Puntos activos</dt><dd>${health.qdrant_points_count}</dd><dt>Indexadas</dt><dd>${indexing.indexed}</dd><dt>Pendientes</dt><dd>${indexing.pending}</dd><dt>Fallidas</dt><dd>${indexing.failed}</dd>`;
  }
  $("#rebuild-index").addEventListener("click", async event => {
    // currentTarget vale null tras el primer await: se conserva la referencia.
    const button = event.currentTarget;
    button.disabled = true; button.textContent = "Reconstruyendo…";
    try { await api("/api/rebuild-index", {method: "POST"}); await loadSystem(); } catch (error) { alert(error.message); }
    finally { button.disabled = false; button.textContent = "Reconstruir Qdrant desde SQLite"; }
  });

  $("#refresh-all").addEventListener("click", async () => { await Promise.all([loadHealth(), loadScope(), loadAssets(), loadCaptures()]); if ($("#view-timeline").classList.contains("active")) loadTimeline(); });
  const requestedView = views[location.hash.slice(1)] ? location.hash.slice(1) : null;
  const initialView = requestedView || "episodes";
  switchView(initialView, true);
  Promise.all([loadHealth(), loadScope(), loadAssets()]).catch(error => console.error(error));
});
