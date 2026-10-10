document.addEventListener("DOMContentLoaded", () => {
  const state = {
    environments: [],
    sources: [],
    entities: [],
    episodes: [],
    nextCursor: null,
    activeEpisode: null,
    episodeGroupMode: "blocks",
    timelineZoom: 1,
    timelineRawData: null,
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
    audio_segment: "Segmento de audio", visual_observation: "Observación visual",
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
  const timelineTitle = item => item.kind === "episode" && item.title?.startsWith("Episodio compuesto")
    ? `Episodio · ${item.environment_id || "ambiente sin declarar"}`
    : item.title;

  const views = {
    episodes: ["MEMORIA CANÓNICA", "Explorador de episodios", "Dossier auditable: observaciones extraídas, eventos, microepisodios y evidencias originales."],
    timeline: ["CRONOLOGÍA MULTI-FUENTE", "Eje temporal continuo", "Visualización temporal por carriles: duración real de intervalos, simultaneidad y solapamientos."],
    ingest: ["RECORRIDO CANÓNICO", "Registro y custodia de activos", "El archivo original inicia la memoria; SHA-256 inmutable y procesamiento observable."],
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
    if (loadData && name === "ingest") loadAssets().catch(error => console.error(error));
    if (loadData && name === "search") loadToolsCatalog().catch(error => console.error(error));
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
    $("#file-label").textContent = event.target.files[0]?.name || "Seleccionar archivo";
  });
  $("#ingest-form").addEventListener("submit", async event => {
    event.preventDefault();
    const submit = $("#ingest-submit"); const output = $("#workflow-result");
    const formData = new FormData(event.currentTarget);
    const captured = formData.get("captured_at");
    if (captured) formData.set("captured_at", new Date(captured).toISOString()); else formData.delete("captured_at");
    submit.disabled = true; submit.textContent = "Procesando memoria…"; output.hidden = false; output.classList.remove("error");
    output.textContent = "Copiando el original y ejecutando el recorrido canónico…";
    try {
      const data = await api("/api/workflow/ingest-and-process", { method: "POST", body: formData });
      const flow = data.workflow;
      output.textContent = [
        `ESTADO        ${data.status}`, `ASSET         ${flow.asset_id}`, `VENTANAS      ${flow.processing_window_ids.length}`,
        `OBSERVACIONES ${flow.observation_ids.length}`, `MICROEPISODIOS ${flow.microepisode_ids.length}`,
        `EPISODIOS     ${flow.episode_ids.length}`, `QDRANT        ${flow.indexed_points} puntos`,
        ...(flow.processor_failures.length ? [`ERRORES       ${flow.processor_failures.join(" | ")}`] : []),
      ].join("\n");
      await Promise.all([loadHealth(), loadScope(), loadAssets()]);
    } catch (error) { output.classList.add("error"); output.textContent = `ERROR\n${error.message}`; }
    finally { submit.disabled = false; submit.textContent = "Conservar y procesar"; }
  });

  async function loadEpisodes(selectedId = state.activeEpisode, append = false, loadAll = false) {
    const list = $("#memory-map") || $("#episode-list");
    const loadMoreBtn = $("#load-more-episodes");
    const loadAllBtn = $("#load-all-episodes");
    if (loadMoreBtn) loadMoreBtn.disabled = true;
    if (loadAllBtn) loadAllBtn.disabled = true;

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

      state.episodes = episodesToKeep;
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

    const targetId = filtered.some(item => item.id === selectedId)
      ? selectedId
      : (filtered.some(item => item.id === state.activeEpisode) ? state.activeEpisode : filtered[0].id);

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

    if (targetId) {
      inspectEpisode(targetId);
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
    $$(".episode-item").forEach(item => item.classList.toggle("active", item.dataset.episodeId === episodeId));
    const root = $("#episode-inspector"); root.innerHTML = '<div class="empty roomy">Reconstruyendo trazabilidad…</div>';
    try {
      const data = await loadEpisodeDetail(episodeId);
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
        return `<article class="extraction-card">
          <div class="extraction-number">${String(index + 1).padStart(2, "0")}</div>
          <div class="extraction-body"><div class="extraction-head"><span class="modality-tag ${esc(observation.modality)}">${esc(modalityLabel(observation.modality))}</span><b>${esc(observationLabel(observation.observation_type))}</b><em>${esc(confidenceLabel(observation.confidence))}</em></div>
          <div class="extracted-content">${extracted ? `<p>${esc(extracted)}</p>` : '<p class="no-content">El procesador no produjo contenido textual; se conserva la observación estructurada.</p>'}</div>
          <div class="observation-origin"><span>Procede de</span><b>${esc(origin)}</b>${source ? `<small>${esc(source.name)} · ${esc(sourceTypeLabel(source.source_type))}</small>` : ""}</div>
          <div class="evidence-locations"><b>Evidencia localizada</b>${evidenceHtml}</div>
          <details class="technical-details compact"><summary>Datos técnicos de la observación</summary><dl><dt>ID</dt><dd>${esc(observation.id)}</dd><dt>Productor</dt><dd>${esc(observation.producer || "—")}</dd><dt>Modelo</dt><dd>${esc(observation.model_version || "—")}</dd><dt>Estado</dt><dd>${esc(observation.validation_state || "—")}</dd></dl></details></div>
        </article>`;
      };
      const observationsHtml = allObservations.map(renderObservation).join("");
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
      const eventsHtml = data.events.length ? `<section class="event-section"><div class="section-title"><p class="kicker">EVENTOS</p><h3>Acciones o cambios detectados</h3></div>${data.events.map(item => `<article class="event-card"><strong>${esc(humanize(item.event_type))}</strong><p>${esc(item.description)}</p><small>${fmtInterval(item)}</small></article>`).join("")}</section>` : "";
      const microHtml = data.microepisodes.map(({microepisode, observations, events}, index) => {
        const rules = (microepisode.metadata?.grouping_decisions || []).flatMap(item => item.rules || []);
        return `<article class="micro-summary"><span>Microepisodio ${index + 1}</span><b>${observations.length} observación(es) · ${events.length} evento(s)</b><small>${fmtInterval(microepisode)}</small><p>${[...new Set(rules)].map(groupingRuleLabel).join(" · ") || "Sin reglas declaradas"}</p></article>`;
      }).join("");
      const structureHtml = microHtml || `<div class="not-grouped"><b>Aún no hay microepisodios</b><span>${allObservations.length ? `Las ${allObservations.length} observación(es) se conservan directamente asociadas a este episodio, pero todavía no han sido agrupadas en microepisodios.` : "Este episodio no tiene observaciones asociadas."}</span></div>`;
      const hypothesesHtml = data.hypotheses.length ? `<section class="hypothesis-section"><div class="section-title"><p class="kicker">NO SON HECHOS</p><h3>Hipótesis pendientes</h3></div>${data.hypotheses.map(item => `<div class="hypothesis-box"><p>${esc(item.statement)}</p></div>`).join("")}</section>` : "";
      const compatibilityNotice = data.compatibility_mode ? '<div class="compatibility-notice"><b>Vista de compatibilidad</b><span>Reinicia EpiGraph para recuperar la trazabilidad completa del servidor actualizado.</span></div>' : "";
      const environmentIds = episode.environment_ids?.length ? episode.environment_ids : [episode.location].filter(Boolean);
      const environment = environmentIds.map(environmentLabel).join(", ") || "Ambiente no declarado";
      root.innerHTML = `<header class="episode-hero"><div><p class="kicker">EPISODIO EN LA MEMORIA</p><h2>${esc(episodeTitle(episode))}</h2><p>${esc(fmtInterval(episode))}</p></div><span class="episode-place">${esc(environment)}</span></header>${compatibilityNotice}
        <div class="memory-facts"><div><span>CONTENIDO EXTRAÍDO</span><b>${allObservations.length} observación(es)</b></div><div><span>PROCEDENCIA</span><b>${data.assets.length} activo(s)</b></div><div><span>EVENTOS</span><b>${data.events.length}</b></div><div><span>ESTRUCTURA</span><b>${data.microepisodes.length} microepisodio(s)</b></div></div>
        <div class="episode-readable"><main><section class="content-section"><div class="section-title"><p class="kicker">QUÉ CONTIENE</p><h3>Contenido extraído</h3><p>Texto o descripción producido por los procesadores. No sustituye al original.</p></div>${observationsHtml || '<div class="empty">No hay contenido extraído asociado a este episodio.</div>'}</section>${eventsHtml}</main>
          <aside class="origin-section"><div class="section-title"><p class="kicker">DE DÓNDE PROCEDE</p><h3>Archivos y fuentes</h3><p>Cada observación debe poder volver a su evidencia original.</p></div>${originsHtml || '<div class="empty">No hay activos originales enlazados.</div>'}</aside></div>
        ${hypothesesHtml}
        <details class="episode-structure"><summary><span>Cómo se construyó este episodio</span><small>Reglas deterministas y microepisodios</small></summary><div class="structure-content">${structureHtml}</div></details>
        <details class="technical-details episode-technical"><summary>Identificadores y datos técnicos</summary><dl><dt>ID del episodio</dt><dd>${esc(episode.id)}</dd><dt>Productor</dt><dd>${esc(episode.producer || "—")}</dd><dt>Versión</dt><dd>${esc(episode.model_version || "—")}</dd><dt>Confianza</dt><dd>${episode.confidence ?? "—"}</dd><dt>Qdrant</dt><dd>${data.compatibility_mode ? "No consultado" : data.vector_projection.indexed ? "Indexado" : "Pendiente"}</dd><dt>Relaciones</dt><dd>${data.relations.length}</dd></dl></details>`;
    } catch (error) { root.innerHTML = `<div class="empty roomy"><b>Error</b><span>${esc(error.message)}</span></div>`; }
  }

  function evidenceLocator(reference) {
    if (reference.frame_number !== null && reference.frame_number !== undefined) return `fotograma ${reference.frame_number}`;
    if (reference.page_number) return `página ${reference.page_number}`;
    if (reference.start_offset_seconds !== null && reference.start_offset_seconds !== undefined) return `${reference.start_offset_seconds}s–${reference.end_offset_seconds}s`;
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
      location.hash = "episodes";
      loadEpisodes(element.dataset.id);
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

    for (const [key, value] of formData.entries()) {
      if (value === "") continue;
      if (value === "on") { payload[key] = true; continue; }
      if (!isNaN(value) && value.trim() !== "") {
        payload[key] = Number(value);
      } else {
        payload[key] = value;
      }
    }
    const def = TOOL_DEFS.find(t => t.name === toolName);
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

  $("#search-form")?.addEventListener("submit", async event => {
    event.preventDefault(); const root = $("#search-results"); root.innerHTML = '<div class="empty roomy">Consultando Qdrant y rehidratando desde SQLite…</div>';
    const payload = {
      text_query: $("#search-query").value.trim(), intent: "semantic_search",
      environment_filter: $("#search-environment").value ? [$("#search-environment").value] : null,
      source_filter: $("#search-source").value ? [$("#search-source").value] : null,
      modality_filter: $("#search-modality").value ? [$("#search-modality").value] : null,
      require_unique_resolution: $("#input-require-unique").checked, expand_relations: true, limit: 10,
    };
    try { renderSearch(await api("/api/hybrid-search", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload) })); }
    catch (error) { root.innerHTML = `<div class="empty roomy"><b>Error de recuperación</b><span>${esc(error.message)}</span></div>`; }
  });
  function renderSearch(data) {
    const root = $("#search-results");
    if (!data.results.length) { root.innerHTML = '<div class="empty roomy">Sin candidatos. Comprueba que los episodios estén indexados.</div>'; return; }
    root.innerHTML = data.results.map(item => `<article class="result-card"><div class="result-head"><span class="result-rank">${item.rank}</span><div><h3>${esc(item.episode.title)}</h3><small>${esc(item.match_kinds.join(" · "))}</small></div><div class="score-stack">${score("Semántica", item.scores.semantic, "semantic")}${score("Estructura", item.scores.structured)}${score("Tiempo", item.scores.temporal)}${score("Relación", item.scores.relational)}</div></div>
      <div class="result-body"><div><b>Observaciones canónicas</b><ul>${item.observations.map(obs => `<li>${esc(obs.observation_type)} · ${esc(obs.description || obs.content)}</li>`).join("") || "<li>Sin observaciones</li>"}</ul></div><div class="evidence-list"><b>EVIDENCIAS</b><br>${item.evidence_references.map(ref => `${esc(ref.reference_kind)} · ${esc(shortId(ref.id))}`).join("<br>") || "Sin evidencia"}<br><br><button class="button ghost result-open" data-episode-id="${esc(item.episode.id)}">Inspeccionar episodio</button></div></div></article>`).join("");
    $$(".result-open").forEach(button => button.addEventListener("click", () => { switchView("episodes"); loadEpisodes(button.dataset.episodeId); }));
  }
  function score(label, value, cls = "") { return value === null || value === undefined ? "" : `<span class="score ${cls}">${label} ${Number(value).toFixed(3)}</span>`; }

  async function loadSystem() {
    const [health, indexing, project] = await Promise.all([loadHealth(), api("/api/indexing/summary"), api("/api/project-status")]);
    $("#runtime-details").innerHTML = `<dt>Punto de control</dt><dd>${esc(project.checkpoint)}</dd><dt>Backend</dt><dd>${esc(health.vector_backend)}</dd><dt>Colección</dt><dd>${esc(health.collection_name)}</dd><dt>Generación</dt><dd>${esc(health.active_generation || "—")}</dd><dt>Puntos activos</dt><dd>${health.qdrant_points_count}</dd><dt>Indexadas</dt><dd>${indexing.indexed}</dd><dt>Pendientes</dt><dd>${indexing.pending}</dd><dt>Fallidas</dt><dd>${indexing.failed}</dd>`;
  }
  $("#rebuild-index").addEventListener("click", async event => {
    event.currentTarget.disabled = true; event.currentTarget.textContent = "Reconstruyendo…";
    try { await api("/api/rebuild-index", {method: "POST"}); await loadSystem(); } catch (error) { alert(error.message); }
    finally { event.currentTarget.disabled = false; event.currentTarget.textContent = "Reconstruir Qdrant desde SQLite"; }
  });

  $("#refresh-all").addEventListener("click", async () => { await Promise.all([loadHealth(), loadScope(), loadAssets()]); if ($("#view-timeline").classList.contains("active")) loadTimeline(); });
  const requestedView = views[location.hash.slice(1)] ? location.hash.slice(1) : null;
  const initialView = requestedView || "episodes";
  switchView(initialView, true);
  Promise.all([loadHealth(), loadScope(), loadAssets()]).catch(error => console.error(error));
});
