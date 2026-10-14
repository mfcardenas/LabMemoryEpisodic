"""Migraciones de base de datos SQLite para el sistema episódico.

Aplica migraciones versionadas de forma secuencial y atómica.
Activa WAL mode y claves foráneas en cada conexión.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import List, Tuple

logger = logging.getLogger(__name__)

MIGRATION_V1 = """
-- 1. Tablas de soporte
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- 2. Fuentes de captura
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    clock_source TEXT NOT NULL DEFAULT 'system',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

-- 3. Sesiones de captura
CREATE TABLE IF NOT EXISTS capture_sessions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_id TEXT,
    started_at_json TEXT,
    ended_at_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_source ON capture_sessions(source_id);

-- 4. Evidencias (blobs verificables con hash)
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES capture_sessions(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL,
    original_filename TEXT,
    internal_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    capture_time_json TEXT,
    ingested_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    external_capture_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence(session_id);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id);
CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_idempotency 
    ON evidence(source_id, session_id, external_capture_id) 
    WHERE external_capture_id IS NOT NULL;

-- 5. Episodios
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    external_id TEXT UNIQUE,
    interval_json TEXT,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    location TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    observation_ids_json TEXT NOT NULL DEFAULT '[]',
    indexable_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    is_visible INTEGER NOT NULL DEFAULT 1,
    session_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_episodes_visible ON episodes(is_visible);
CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_episodes_external_id ON episodes(external_id);

-- 6. Observaciones
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    provenance_kind TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    tool_version TEXT,
    confidence REAL,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    entity_id TEXT,
    capture_time_json TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_obs_episode ON observations(episode_id);
CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_obs_created ON observations(created_at);

-- 7. Anotaciones (enriquecimientos versionados)
CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL DEFAULT 1,
    content TEXT NOT NULL,
    provenance_kind TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    tool_version TEXT,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    supersedes_id TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ann_episode ON annotations(episode_id);
CREATE INDEX IF NOT EXISTS idx_ann_revision ON annotations(episode_id, revision_number);

-- 8. Entidades
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    provenance_kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_entities_label ON entities(label);

-- 9. Representaciones (tabla outbox para indexación vectorial)
CREATE TABLE IF NOT EXISTS representations (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    space TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    indexing_state TEXT NOT NULL DEFAULT 'pending',
    qdrant_point_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rep_state ON representations(indexing_state);
CREATE INDEX IF NOT EXISTS idx_rep_target ON representations(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_rep_space ON representations(space);

-- 10. Query runs (auditoría y registro de consultas)
CREATE TABLE IF NOT EXISTS query_runs (
    id TEXT PRIMARY KEY,
    intent TEXT NOT NULL,
    text_query TEXT,
    image_query_evidence_id TEXT,
    source_filter_json TEXT,
    interval_filter_json TEXT,
    entity_filter TEXT,
    limit_val INTEGER NOT NULL DEFAULT 10,
    results_json TEXT NOT NULL DEFAULT '[]',
    overall_verdict TEXT NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    executed_at TEXT NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0.0,
    config_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_query_runs_exec ON query_runs(executed_at);
"""


MIGRATION_V2 = """
-- Fase 2: procedencia primaria y modelo episódico completo.
CREATE TABLE environments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    producer TEXT NOT NULL,
    model_version TEXT,
    confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO environments (
    id, name, description, producer, model_version, confidence, created_at, metadata_json
) VALUES (
    'env-legacy-default',
    'Entorno legado sin clasificar',
    'Compatibilidad para fuentes importadas antes de la Fase 2',
    'migration-v2',
    '2',
    NULL,
    strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
    '{"migration":"v2"}'
);

ALTER TABLE sources ADD COLUMN environment_id TEXT;
ALTER TABLE sources ADD COLUMN producer TEXT NOT NULL DEFAULT 'ingest';
ALTER TABLE sources ADD COLUMN model_version TEXT;
ALTER TABLE sources ADD COLUMN confidence REAL;
UPDATE sources SET environment_id = 'env-legacy-default' WHERE environment_id IS NULL;
CREATE INDEX idx_sources_environment ON sources(environment_id);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    uri TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    captured_at TEXT,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_assets_environment ON assets(environment_id);
CREATE INDEX idx_assets_source ON assets(source_id);
CREATE INDEX idx_assets_hash ON assets(content_hash);

CREATE TABLE processing_runs (
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE RESTRICT,
    source_id TEXT REFERENCES sources(id) ON DELETE RESTRICT,
    state TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_processing_runs_environment ON processing_runs(environment_id);

CREATE TABLE processing_windows (
    id TEXT PRIMARY KEY,
    processing_run_id TEXT NOT NULL REFERENCES processing_runs(id) ON DELETE CASCADE,
    environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_processing_windows_run ON processing_windows(processing_run_id);
CREATE INDEX idx_processing_windows_asset ON processing_windows(asset_id);
CREATE INDEX idx_processing_windows_time ON processing_windows(start_time, end_time);

CREATE TABLE evidence_references (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    reference_kind TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_evidence_refs_asset ON evidence_references(asset_id);

-- Observation deja de depender obligatoriamente de Episode.
CREATE TABLE observations_v2 (
    id TEXT PRIMARY KEY,
    episode_id TEXT REFERENCES episodes(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    provenance_kind TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    tool_version TEXT,
    confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    entity_id TEXT,
    capture_time_json TEXT,
    environment_id TEXT REFERENCES environments(id) ON DELETE RESTRICT,
    source_id TEXT REFERENCES sources(id) ON DELETE RESTRICT,
    asset_id TEXT REFERENCES assets(id) ON DELETE RESTRICT,
    processing_run_id TEXT REFERENCES processing_runs(id) ON DELETE SET NULL,
    processing_window_id TEXT REFERENCES processing_windows(id) ON DELETE SET NULL,
    evidence_reference_ids_json TEXT NOT NULL DEFAULT '[]',
    start_time TEXT,
    end_time TEXT,
    producer TEXT NOT NULL DEFAULT '',
    model_version TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO observations_v2 (
    id, episode_id, content, provenance_kind, author, tool_version,
    confidence, evidence_ids_json, entity_id, capture_time_json,
    environment_id, source_id, asset_id, processing_run_id,
    processing_window_id, evidence_reference_ids_json, start_time, end_time,
    producer, model_version, created_at, metadata_json
)
SELECT
    id, episode_id, content, provenance_kind, author, tool_version,
    confidence, evidence_ids_json, entity_id, capture_time_json,
    NULL, NULL, NULL, NULL, NULL, '[]',
    json_extract(capture_time_json, '$.absolute'),
    json_extract(capture_time_json, '$.absolute'),
    author, tool_version, created_at, metadata_json
FROM observations;

DROP TABLE observations;
ALTER TABLE observations_v2 RENAME TO observations;
CREATE INDEX idx_obs_episode ON observations(episode_id);
CREATE INDEX idx_obs_entity ON observations(entity_id);
CREATE INDEX idx_obs_window ON observations(processing_window_id);
CREATE INDEX idx_obs_asset ON observations(asset_id);
CREATE INDEX idx_obs_created ON observations(created_at);

ALTER TABLE entities ADD COLUMN environment_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE entities ADD COLUMN producer TEXT NOT NULL DEFAULT 'human';
ALTER TABLE entities ADD COLUMN model_version TEXT;
ALTER TABLE entities ADD COLUMN confidence REAL;

ALTER TABLE episodes ADD COLUMN environment_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE episodes ADD COLUMN microepisode_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE episodes ADD COLUMN start_time TEXT;
ALTER TABLE episodes ADD COLUMN end_time TEXT;
ALTER TABLE episodes ADD COLUMN producer TEXT NOT NULL DEFAULT 'legacy_composer';
ALTER TABLE episodes ADD COLUMN model_version TEXT DEFAULT 'v1';
ALTER TABLE episodes ADD COLUMN confidence REAL;
UPDATE episodes SET
    start_time = json_extract(interval_json, '$.start.absolute'),
    end_time = json_extract(interval_json, '$.end.absolute')
WHERE interval_json IS NOT NULL;
CREATE INDEX idx_episodes_time ON episodes(start_time, end_time);

CREATE TABLE events (
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE RESTRICT,
    source_id TEXT REFERENCES sources(id) ON DELETE RESTRICT,
    processing_run_id TEXT REFERENCES processing_runs(id) ON DELETE SET NULL,
    processing_window_id TEXT REFERENCES processing_windows(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_events_environment ON events(environment_id);
CREATE INDEX idx_events_time ON events(start_time, end_time);

CREATE TABLE microepisodes (
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_microepisodes_environment ON microepisodes(environment_id);
CREATE INDEX idx_microepisodes_time ON microepisodes(start_time, end_time);

CREATE TABLE relations (
    id TEXT PRIMARY KEY,
    relation_type TEXT NOT NULL,
    origin_type TEXT NOT NULL,
    origin_id TEXT NOT NULL,
    destination_type TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    validation_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_relations_origin ON relations(origin_type, origin_id);
CREATE INDEX idx_relations_destination ON relations(destination_type, destination_id);
CREATE INDEX idx_relations_state ON relations(validation_state);

CREATE TABLE hypotheses (
    id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_hypotheses_subject ON hypotheses(subject_type, subject_id);
CREATE INDEX idx_hypotheses_state ON hypotheses(state);
"""

MIGRATION_V3 = """
-- Metadatos inspeccionados de activos originales.
ALTER TABLE assets ADD COLUMN mime_type TEXT NOT NULL DEFAULT 'application/octet-stream';
ALTER TABLE assets ADD COLUMN size_bytes INTEGER;
ALTER TABLE assets ADD COLUMN duration_seconds REAL;
UPDATE assets SET
    mime_type = COALESCE(json_extract(payload_json, '$.mime_type'), mime_type),
    size_bytes = json_extract(payload_json, '$.size_bytes'),
    duration_seconds = json_extract(payload_json, '$.duration_seconds');

-- DeltaTime y solapamiento pertenecen exclusivamente a ProcessingWindow.
ALTER TABLE processing_windows ADD COLUMN start_offset_seconds REAL NOT NULL DEFAULT 0.0;
ALTER TABLE processing_windows ADD COLUMN end_offset_seconds REAL NOT NULL DEFAULT 0.0;
ALTER TABLE processing_windows ADD COLUMN delta_time_seconds REAL NOT NULL DEFAULT 0.0;
ALTER TABLE processing_windows ADD COLUMN overlap_seconds REAL NOT NULL DEFAULT 0.0;
UPDATE processing_windows SET
    start_offset_seconds = COALESCE(json_extract(payload_json, '$.start_offset_seconds'), 0.0),
    end_offset_seconds = COALESCE(
        json_extract(payload_json, '$.end_offset_seconds'),
        (julianday(end_time) - julianday(start_time)) * 86400.0
    ),
    delta_time_seconds = COALESCE(
        json_extract(payload_json, '$.delta_time_seconds'),
        (julianday(end_time) - julianday(start_time)) * 86400.0
    ),
    overlap_seconds = COALESCE(json_extract(payload_json, '$.overlap_seconds'), 0.0);
CREATE INDEX idx_assets_mime ON assets(mime_type);
"""

MIGRATION_V4 = """
-- Contrato normalizado de observaciones de Fase 4.
ALTER TABLE observations ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE observations ADD COLUMN modality TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE observations ADD COLUMN observation_type TEXT NOT NULL DEFAULT 'legacy_observation';
ALTER TABLE observations ADD COLUMN attributes_json TEXT NOT NULL DEFAULT '{}';
UPDATE observations SET description = content WHERE description = '';
CREATE INDEX idx_observations_asset_type
    ON observations(asset_id, modality, observation_type);
"""

MIGRATION_V5 = """
-- Estado explícito para consultas de observación confirmada (Fase 6).
ALTER TABLE observations ADD COLUMN validation_state TEXT NOT NULL DEFAULT 'proposed';
CREATE INDEX idx_observations_validation_entity_time
    ON observations(entity_id, validation_state, start_time, end_time);
UPDATE relations SET
    relation_type = upper(relation_type),
    payload_json = json_set(payload_json, '$.relation_type', upper(relation_type));
"""

MIGRATION_V6 = """
-- Capturas subidas en conjunto (una captura = un episodio) y cola durable.
CREATE TABLE captures (
    id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    episode_id TEXT,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_captures_environment ON captures(environment_id, created_at);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_jobs_state ON jobs(state, created_at);
CREATE INDEX idx_jobs_target ON jobs(target_id, created_at);
"""

MIGRATION_V7 = """
-- Entrada automática: ficheros de las carpetas vigiladas, registro de
-- ejecución visible en el front y concesión del vigilante entre procesos.
CREATE TABLE inbox_files (
    id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    group_key TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    capture_id TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_inbox_files_live ON inbox_files(source_name, archived, rel_path);
CREATE INDEX idx_inbox_files_group ON inbox_files(source_name, group_key);
CREATE INDEX idx_inbox_files_updated ON inbox_files(updated_at);

CREATE TABLE activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    job_id TEXT,
    capture_id TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_activity_component ON activity_log(component, id);

CREATE TABLE leases (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""

MIGRATION_V9 = """
-- Estado del sistema que no es dominio: p. ej. la huella con la que se
-- indexó (modelos, prefijos, formato), para reindexar solo si cambia.
CREATE TABLE system_state (
    name TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

MIGRATION_V8 = """
-- Búsqueda por pasajes: índice de texto completo (BM25) de cada observación
-- con contenido propio. Es una proyección, como Qdrant: la escribe la
-- indexación y se puede reconstruir desde las observaciones.
CREATE VIRTUAL TABLE passage_index USING fts5(
    text,
    title,
    observation_id UNINDEXED,
    episode_id UNINDEXED,
    observation_type UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

MIGRATION_V10 = """
-- El evento como unidad de consulta: columnas propias (episodio, activo,
-- orden, posición, título, revisión) rellenadas desde el payload existente.
-- El episodio se deduce de la observación de la que nació el evento.
ALTER TABLE events ADD COLUMN episode_id TEXT REFERENCES episodes(id) ON DELETE SET NULL;
ALTER TABLE events ADD COLUMN asset_id TEXT;
ALTER TABLE events ADD COLUMN sequence_number INTEGER NOT NULL DEFAULT 0;
ALTER TABLE events ADD COLUMN start_offset_seconds REAL;
ALTER TABLE events ADD COLUMN end_offset_seconds REAL;
ALTER TABLE events ADD COLUMN title TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
UPDATE events SET
    asset_id = json_extract(payload_json, '$.asset_id'),
    sequence_number = coalesce(json_extract(payload_json, '$.sequence_number'), 0),
    start_offset_seconds = json_extract(payload_json, '$.start_offset_seconds'),
    end_offset_seconds = json_extract(payload_json, '$.end_offset_seconds'),
    title = coalesce(json_extract(payload_json, '$.title'), ''),
    revision = coalesce(json_extract(payload_json, '$.revision'), 1),
    episode_id = (
        SELECT o.episode_id FROM observations o
        WHERE o.id = json_extract(events.payload_json, '$.metadata.observation_ids[0]')
    );
UPDATE events SET payload_json = json_set(payload_json, '$.episode_id', episode_id)
WHERE episode_id IS NOT NULL;
CREATE INDEX idx_events_episode ON events(episode_id, sequence_number);
CREATE INDEX idx_events_asset ON events(asset_id, start_offset_seconds);

-- Pasajes como tabla propia: lo que se indexa y lo que se muestra, con sus
-- filtros en columnas; el índice de texto completo es de contenido externo
-- sobre ella y se mantiene con disparadores. Sigue siendo una proyección:
-- la escribe la indexación y se reconstruye desde eventos y observaciones.
DROP TABLE passage_index;
CREATE TABLE passages (
    pid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    environment_id TEXT,
    source_id TEXT,
    modality TEXT,
    asset_id TEXT,
    start_time TEXT,
    end_time TEXT,
    start_ms INTEGER,
    end_ms INTEGER,
    start_offset_seconds REAL,
    end_offset_seconds REAL,
    page_number INTEGER,
    sequence_number INTEGER,
    event_id TEXT,
    observation_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    model TEXT,
    keyframes INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_passages_episode ON passages(episode_id);
CREATE INDEX idx_passages_env_time ON passages(environment_id, start_ms);
CREATE VIRTUAL TABLE passage_index USING fts5(
    text,
    title,
    content = 'passages',
    content_rowid = 'pid',
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER passages_ai AFTER INSERT ON passages BEGIN
    INSERT INTO passage_index(rowid, text, title) VALUES (new.pid, new.text, new.title);
END;
CREATE TRIGGER passages_ad AFTER DELETE ON passages BEGIN
    INSERT INTO passage_index(passage_index, rowid, text, title)
    VALUES ('delete', old.pid, old.text, old.title);
END;
CREATE TRIGGER passages_au AFTER UPDATE ON passages BEGIN
    INSERT INTO passage_index(passage_index, rowid, text, title)
    VALUES ('delete', old.pid, old.text, old.title);
    INSERT INTO passage_index(rowid, text, title) VALUES (new.pid, new.text, new.title);
END;
"""

MIGRATION_V11 = """
-- Resúmenes jerárquicos generados por el modelo de texto (episodio, día,
-- entorno), con revisiones: regenerar añade una fila y la anterior deja de
-- ser la vigente. Nunca se borran.
CREATE TABLE summaries (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    revision INTEGER NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    narrative TEXT NOT NULL DEFAULT '',
    highlights_json TEXT NOT NULL DEFAULT '[]',
    open_questions_json TEXT NOT NULL DEFAULT '[]',
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (scope, scope_key, revision)
);
CREATE INDEX idx_summaries_current ON summaries(scope, scope_key, is_current);
"""

MIGRATION_V12 = """
-- Auditoría de las respuestas del agente: pregunta, pasos (herramientas y
-- argumentos), citas verificadas, modelo, tokens y latencia.
CREATE TABLE answer_runs (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '',
    abstained INTEGER NOT NULL DEFAULT 0,
    citations_json TEXT NOT NULL DEFAULT '[]',
    steps_json TEXT NOT NULL DEFAULT '[]',
    model_id TEXT NOT NULL DEFAULT '',
    usage_json TEXT NOT NULL DEFAULT '{}',
    took_ms REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_answer_runs_created ON answer_runs(created_at);
"""

MIGRATIONS: List[Tuple[int, str]] = [
    (1, MIGRATION_V1),
    (2, MIGRATION_V2),
    (3, MIGRATION_V3),
    (4, MIGRATION_V4),
    (5, MIGRATION_V5),
    (6, MIGRATION_V6),
    (7, MIGRATION_V7),
    (8, MIGRATION_V8),
    (9, MIGRATION_V9),
    (10, MIGRATION_V10),
    (11, MIGRATION_V11),
    (12, MIGRATION_V12),
]


def init_connection(conn: sqlite3.Connection) -> None:
    """Configura los pragmas estándar para cada conexión SQLite."""
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.row_factory = sqlite3.Row


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Aplica todas las migraciones pendientes en orden."""
    init_connection(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    cur = conn.cursor()
    cur.execute("SELECT version FROM schema_migrations ORDER BY version")
    applied = {row[0] for row in cur.fetchall()}

    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        logger.info("Applying migration v%d", version)
        migration_script = f"""
        BEGIN IMMEDIATE;
        {sql}
        INSERT INTO schema_migrations (version, applied_at)
        VALUES ({version}, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));
        COMMIT;
        """
        try:
            conn.executescript(migration_script)
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
