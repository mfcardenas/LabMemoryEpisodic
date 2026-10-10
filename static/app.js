// LabMemory Episodic - Client Application Logic
document.addEventListener("DOMContentLoaded", () => {
    // State
    const state = {
        currentTab: "tab-agent",
        queryMode: "agent", // "agent" | "hybrid"
        sourceMode: "file", // "file" | "text"
        collections: [],
        status: null,
    };

    // DOM Elements - Navigation
    const navTabs = document.querySelectorAll(".nav-tab");
    const tabPanes = document.querySelectorAll(".tab-pane");

    // DOM Elements - Status
    const qdrantDot = document.getElementById("qdrant-status-dot");
    const ollamaDot = document.getElementById("ollama-status-dot");
    const activeLlmLabel = document.getElementById("active-llm-label");

    // DOM Elements - Selectors
    const queryDomainSelect = document.getElementById("query-domain-select");
    const indexDomainSelect = document.getElementById("index-domain-select");

    // DOM Elements - Agent / Query
    const modeAgentBtn = document.getElementById("mode-agent-btn");
    const modeHybridBtn = document.getElementById("mode-hybrid-btn");
    const agentQueryInput = document.getElementById("agent-query-input");
    const executeQueryBtn = document.getElementById("execute-query-btn");
    const queryLimitSelect = document.getElementById("query-limit-select");
    const filterKeyInput = document.getElementById("filter-key-input");
    const filterValInput = document.getElementById("filter-val-input");
    const answerCard = document.getElementById("answer-card");
    const agentAnswerText = document.getElementById("agent-answer-text");
    const sourcesSection = document.getElementById("sources-section");
    const sourcesList = document.getElementById("sources-list");
    const modelBadge = document.getElementById("model-badge");

    // DOM Elements - Indexer
    const sourceFilePill = document.getElementById("source-file-pill");
    const sourceTextPill = document.getElementById("source-text-pill");
    const fileDropzone = document.getElementById("file-dropzone");
    const fileUploadInput = document.getElementById("file-upload-input");
    const dropzoneFilename = document.getElementById("dropzone-filename");
    const rawTextArea = document.getElementById("raw-text-area");
    const directTextInput = document.getElementById("direct-text-input");
    const addMetaRowBtn = document.getElementById("add-meta-row-btn");
    const metadataContainer = document.getElementById("metadata-rows-container");
    const startIndexingBtn = document.getElementById("start-indexing-btn");
    const indexFeedbackCard = document.getElementById("index-feedback-card");
    const feedbackDetails = document.getElementById("feedback-details");

    // DOM Elements - Domains
    const newDomainInput = document.getElementById("new-domain-input");
    const createDomainBtn = document.getElementById("create-domain-btn");
    const refreshDomainsBtn = document.getElementById("refresh-domains-btn");
    const collectionsList = document.getElementById("collections-list");
    const toast = document.getElementById("app-toast");

    // --- Tab Navigation ---
    navTabs.forEach((tab) => {
        tab.addEventListener("click", () => {
            const targetId = tab.getAttribute("data-tab");
            navTabs.forEach((t) => t.classList.remove("active"));
            tabPanes.forEach((p) => p.classList.remove("active"));

            tab.classList.add("active");
            const targetPane = document.getElementById(targetId);
            if (targetPane) targetPane.classList.add("active");
            state.currentTab = targetId;
        });
    });

    // --- Query Mode Toggle ---
    modeAgentBtn.addEventListener("click", () => {
        state.queryMode = "agent";
        modeAgentBtn.classList.add("active");
        modeHybridBtn.classList.remove("active");
        executeQueryBtn.querySelector(".btn-text").textContent = "Consultar Agente";
    });

    modeHybridBtn.addEventListener("click", () => {
        state.queryMode = "hybrid";
        modeHybridBtn.classList.add("active");
        modeAgentBtn.classList.remove("active");
        executeQueryBtn.querySelector(".btn-text").textContent = "Búsqueda Híbrida RRF";
    });

    // --- Source Mode Toggle (File vs Direct Text) ---
    sourceFilePill.addEventListener("click", () => {
        state.sourceMode = "file";
        sourceFilePill.classList.add("active");
        sourceTextPill.classList.remove("active");
        fileDropzone.classList.remove("hidden");
        rawTextArea.classList.add("hidden");
    });

    sourceTextPill.addEventListener("click", () => {
        state.sourceMode = "text";
        sourceTextPill.classList.add("active");
        sourceFilePill.classList.remove("active");
        fileDropzone.classList.add("hidden");
        rawTextArea.classList.remove("hidden");
    });

    // File selection UI update
    fileUploadInput.addEventListener("change", () => {
        if (fileUploadInput.files && fileUploadInput.files[0]) {
            dropzoneFilename.textContent = `Archivo seleccionado: ${fileUploadInput.files[0].name} (${(fileUploadInput.files[0].size / 1024).toFixed(1)} KB)`;
        }
    });

    // --- Dynamic Metadata Row Management ---
    addMetaRowBtn.addEventListener("click", () => {
        const row = document.createElement("div");
        row.className = "meta-row";
        row.innerHTML = `
            <input type="text" class="meta-key text-input-sm" placeholder="Clave">
            <input type="text" class="meta-val text-input-sm" placeholder="Valor">
            <button type="button" class="btn-remove-row">&times;</button>
        `;
        row.querySelector(".btn-remove-row").addEventListener("click", () => row.remove());
        metadataContainer.appendChild(row);
    });

    metadataContainer.addEventListener("click", (e) => {
        if (e.target.classList.contains("btn-remove-row")) {
            e.target.closest(".meta-row").remove();
        }
    });

    function collectMetadata() {
        const metadata = {};
        const rows = metadataContainer.querySelectorAll(".meta-row");
        rows.forEach((row) => {
            const key = row.querySelector(".meta-key").value.trim();
            const val = row.querySelector(".meta-val").value.trim();
            if (key) {
                // Parse number or boolean if applicable
                if (!isNaN(val) && val !== "") {
                    metadata[key] = Number(val);
                } else if (val.toLowerCase() === "true") {
                    metadata[key] = true;
                } else if (val.toLowerCase() === "false") {
                    metadata[key] = false;
                } else {
                    metadata[key] = val;
                }
            }
        });
        return metadata;
    }

    // --- Toast Notification ---
    function showToast(message, isError = false) {
        toast.textContent = message;
        toast.style.borderColor = isError ? "var(--danger)" : "var(--border-accent)";
        toast.classList.remove("hidden");
        setTimeout(() => toast.classList.add("hidden"), 4000);
    }

    // --- API Calls: Status & Collections ---
    async function loadStatus() {
        try {
            const res = await fetch("/api/status");
            const data = await res.json();
            state.status = data;

            if (data.qdrant.connected) {
                qdrantDot.classList.add("online");
            } else {
                qdrantDot.classList.remove("online");
            }

            if (data.ollama.connected) {
                ollamaDot.classList.add("online");
                activeLlmLabel.textContent = `LLM: ${data.ollama.active_llm}`;
                modelBadge.textContent = data.ollama.active_llm;
            } else {
                ollamaDot.classList.remove("online");
                activeLlmLabel.textContent = "Ollama: Desconectado";
            }
        } catch (err) {
            console.error("Error al cargar estado del sistema:", err);
        }
    }

    // Modal Elements
    const chunksModal = document.getElementById("chunks-modal");
    const modalBackdrop = document.getElementById("modal-backdrop");
    const btnCloseModal = document.getElementById("btn-close-modal");
    const modalTitle = document.getElementById("modal-title");
    const modalChunksBody = document.getElementById("modal-chunks-body");

    function closeModal() {
        if (chunksModal) chunksModal.classList.add("hidden");
    }
    if (btnCloseModal) btnCloseModal.addEventListener("click", closeModal);
    if (modalBackdrop) modalBackdrop.addEventListener("click", closeModal);

    async function inspectCollectionChunks(colName) {
        if (!chunksModal) return;
        modalTitle.textContent = `Chunks en Colección: ${colName}`;
        modalChunksBody.innerHTML = "<p class='hint'>Cargando fragmentos desde Qdrant...</p>";
        chunksModal.classList.remove("hidden");

        try {
            const res = await fetch(`/api/collections/${colName}/points?limit=25`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error al recuperar fragmentos");

            if (!data.points || data.points.length === 0) {
                modalChunksBody.innerHTML = `<p class='hint'>La colección '${colName}' no tiene chunks indexados todavía.</p>`;
                return;
            }

            modalChunksBody.innerHTML = "";
            data.points.forEach((p, idx) => {
                const item = document.createElement("div");
                item.className = "source-item";
                const payload = p.payload || {};
                const text = payload.text || "(Sin texto)";
                const metaTags = Object.entries(payload)
                    .filter(([k]) => k !== "text")
                    .map(([k, v]) => `<span class="meta-tag">${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}</span>`)
                    .join(" ");

                item.innerHTML = `
                    <div class="source-header">
                        <strong>Chunk #${idx + 1}</strong>
                        <span class="badge badge-accent">ID: ${p.id.slice(0, 8)}...</span>
                    </div>
                    <div class="source-meta-tags">${metaTags}</div>
                    <div class="source-text">${text}</div>
                `;
                modalChunksBody.appendChild(item);
            });
        } catch (err) {
            modalChunksBody.innerHTML = `<p class='hint' style='color:var(--danger)'>${err.message}</p>`;
        }
    }

    async function loadCollections(preferredDomain = null) {
        try {
            const res = await fetch("/api/collections");
            const data = await res.json();
            state.collections = data.collections || [];

            const prevQueryVal = preferredDomain || queryDomainSelect.value;
            const prevIndexVal = preferredDomain || indexDomainSelect.value;

            // Reset selectors
            queryDomainSelect.innerHTML = "";
            indexDomainSelect.innerHTML = "";
            collectionsList.innerHTML = "";

            if (state.collections.length === 0) {
                const opt = new Option("No hay dominios creados", "");
                queryDomainSelect.add(opt);
                indexDomainSelect.add(opt.cloneNode(true));
                collectionsList.innerHTML = "<p class='hint'>No se encontraron colecciones en Qdrant.</p>";
                return;
            }

            let foundQueryMatch = false;
            let foundIndexMatch = false;

            state.collections.forEach((colObj) => {
                const colName = typeof colObj === "object" ? colObj.name : colObj;
                const pointsCount = typeof colObj === "object" ? colObj.points_count : 0;
                const label = `${colName} (${pointsCount} chunks)`;

                const optQuery = new Option(label, colName);
                if (colName === prevQueryVal) {
                    optQuery.selected = true;
                    foundQueryMatch = true;
                }
                queryDomainSelect.add(optQuery);

                const optIndex = new Option(label, colName);
                if (colName === prevIndexVal) {
                    optIndex.selected = true;
                    foundIndexMatch = true;
                }
                indexDomainSelect.add(optIndex);

                const item = document.createElement("div");
                item.className = "collection-badge-item";
                item.innerHTML = `
                    <div class="col-main">
                        <span class="collection-name">${colName}</span>
                        <span class="badge badge-score">${pointsCount} chunks</span>
                    </div>
                    <div class="col-actions">
                        <button class="btn-ghost-sm btn-index-here" data-col="${colName}">📥 Indexar aquí</button>
                        <button class="btn-ghost-sm btn-select-col" data-col="${colName}">🔍 Consultar</button>
                        <button class="btn-ghost-sm btn-view-points" data-col="${colName}">👁️ Ver Chunks</button>
                    </div>
                `;

                item.querySelector(".btn-index-here").addEventListener("click", () => {
                    indexDomainSelect.value = colName;
                    document.getElementById("tab-btn-indexer").click();
                    showToast(`Colección '${colName}' seleccionada para indexación`);
                });

                item.querySelector(".btn-select-col").addEventListener("click", () => {
                    queryDomainSelect.value = colName;
                    document.getElementById("tab-btn-agent").click();
                });

                item.querySelector(".btn-view-points").addEventListener("click", () => {
                    inspectCollectionChunks(colName);
                });

                collectionsList.appendChild(item);
            });

            // Si no se encontró match, dejar el primero seleccionado explícitamente
            if (!foundQueryMatch && queryDomainSelect.options.length > 0) {
                queryDomainSelect.selectedIndex = 0;
            }
            if (!foundIndexMatch && indexDomainSelect.options.length > 0) {
                indexDomainSelect.selectedIndex = 0;
            }

        } catch (err) {
            showToast("Error al cargar dominios de Qdrant", true);
        }
    }

    // --- Action: Create Collection ---
    createDomainBtn.addEventListener("click", async () => {
        const name = newDomainInput.value.trim();
        if (!name) {
            showToast("Introduce un nombre de colección válido", true);
            return;
        }

        createDomainBtn.classList.add("loading");
        try {
            const res = await fetch("/api/collections", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ collection_name: name }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error al crear colección");

            showToast(data.message);
            newDomainInput.value = "";
            // Recargar y preseleccionar la nueva colección automáticamente
            await loadCollections(data.collection_name);
        } catch (err) {
            showToast(err.message, true);
        } finally {
            createDomainBtn.classList.remove("loading");
        }
    });

    refreshDomainsBtn.addEventListener("click", () => loadCollections());

    // --- Action: Index Content ---
    startIndexingBtn.addEventListener("click", async () => {
        const targetCollection = indexDomainSelect.value;
        if (!targetCollection) {
            showToast("Selecciona una colección de destino", true);
            return;
        }

        const metadata = collectMetadata();
        startIndexingBtn.classList.add("loading");
        indexFeedbackCard.classList.add("hidden");

        try {
            let res, data;
            if (state.sourceMode === "file") {
                const file = fileUploadInput.files[0];
                if (!file) throw new Error("Por favor selecciona un archivo.");

                const formData = new FormData();
                formData.append("file", file);
                formData.append("collection_name", targetCollection);
                formData.append("metadata_json", JSON.stringify(metadata));

                res = await fetch("/api/index/file", {
                    method: "POST",
                    body: formData,
                });
            } else {
                const text = directTextInput.value.trim();
                if (!text) throw new Error("Por favor ingresa texto para indexar.");

                res = await fetch("/api/index/text", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        collection_name: targetCollection,
                        content: text,
                        metadata: metadata,
                    }),
                });
            }

            data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error en la indexación");

            showToast(`¡Indexación completada! ${data.chunks_indexed} fragmentos añadidos.`);
            indexFeedbackCard.classList.remove("hidden");
            feedbackDetails.innerHTML = `
                <p><strong>Colección Destino:</strong> <span class="badge badge-accent">${data.collection}</span></p>
                <p><strong>Nuevos Chunks Indexados:</strong> ${data.chunks_indexed}</p>
                <p><strong>Total Chunks en Qdrant:</strong> <span class="badge badge-score">${data.total_in_collection || 'Actualizado'}</span></p>
                <div class="mt-3">
                    <button type="button" class="btn btn-secondary btn-block" id="btn-goto-query-now">
                        🔍 Ir a Consultar esta Colección
                    </button>
                </div>
            `;

            document.getElementById("btn-goto-query-now").addEventListener("click", () => {
                queryDomainSelect.value = data.collection;
                document.getElementById("tab-btn-agent").click();
            });

            // Actualizar conteos globales en los dropdowns
            await loadCollections(targetCollection);

        } catch (err) {
            showToast(err.message, true);
        } finally {
            startIndexingBtn.classList.remove("loading");
        }
    });

    // --- Action: Execute Query / Agent ---
    executeQueryBtn.addEventListener("click", async () => {
        const query = agentQueryInput.value.trim();
        const collection = queryDomainSelect.value;
        const limit = parseInt(queryLimitSelect.value, 10);

        if (!query) {
            showToast("Por favor escribe una consulta", true);
            return;
        }
        if (!collection) {
            showToast("Selecciona un dominio/colección para consultar", true);
            return;
        }

        // Build metadata filter if provided
        let metadataFilter = null;
        const filterKey = filterKeyInput.value.trim();
        const filterVal = filterValInput.value.trim();
        if (filterKey && filterVal) {
            metadataFilter = { [filterKey]: filterVal };
        }

        executeQueryBtn.classList.add("loading");
        answerCard.classList.add("hidden");
        sourcesSection.classList.add("hidden");
        sourcesList.innerHTML = "";

        try {
            if (state.queryMode === "agent") {
                // Call Agent
                const res = await fetch("/api/agent/ask", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        question: query,
                        collection_name: collection,
                        metadata_filter: metadataFilter,
                        limit: limit,
                    }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Error en la consulta del agente");

                agentAnswerText.textContent = data.answer;
                answerCard.classList.remove("hidden");

                renderSources(data.sources);
            } else {
                // Pure Hybrid Search
                const res = await fetch("/api/search", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        query: query,
                        collection_name: collection,
                        metadata_filter: metadataFilter,
                        limit: limit,
                    }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Error en la búsqueda híbrida");

                const mappedSources = data.results.map((r) => ({
                    chunk_id: r.chunk_id,
                    score_rrf: r.score,
                    metadata: r.metadata,
                    text_snippet: r.text,
                }));
                renderSources(mappedSources);
            }
        } catch (err) {
            showToast(err.message, true);
        } finally {
            executeQueryBtn.classList.remove("loading");
        }
    });

    function renderSources(sources) {
        if (!sources || sources.length === 0) {
            sourcesList.innerHTML = "<p class='hint'>No se recuperaron fragmentos relevantes.</p>";
            sourcesSection.classList.remove("hidden");
            return;
        }

        sourcesList.innerHTML = "";
        sources.forEach((src, idx) => {
            const item = document.createElement("div");
            item.className = "source-item";

            // Metadata tags
            const metaBadges = Object.entries(src.metadata || {})
                .map(([k, v]) => `<span class="meta-tag">${k}: ${v}</span>`)
                .join(" ");

            item.innerHTML = `
                <div class="source-header">
                    <strong>Fuente #${idx + 1}</strong>
                    <span class="badge badge-score">Score RRF: ${src.score_rrf}</span>
                </div>
                <div class="source-meta-tags">${metaBadges}</div>
                <div class="source-text">${src.text_snippet || ""}</div>
            `;
            sourcesList.appendChild(item);
        });
        sourcesSection.classList.remove("hidden");
    }

    // Initial Bootstrap
    loadStatus();
    loadCollections();
});
