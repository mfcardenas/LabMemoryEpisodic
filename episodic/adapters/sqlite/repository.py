"""Implementación SQLite del repositorio episódico (EpisodicRepository).

Proporciona la fuente de verdad canónica y persistente para todas las entidades.
Garantiza idempotencia, transaccionalidad, paginación con cursor y tipado fuerte.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from episodic.adapters.sqlite.migrations import apply_migrations, init_connection
from episodic.adapters.sqlite.serialized import SerializedConnection
from episodic.domain.captures import Capture, Job, JobState
from episodic.domain.watch import ActivityEntry, ActivityLevel, InboxFile, InboxFileState
from episodic.domain.entities import (
    Annotation,
    CaptureSession,
    Entity,
    Episode,
    Evidence,
    Observation,
    QueryResult,
    QueryRun,
    Representation,
    Source,
)
from episodic.domain.episodes import MicroEpisode
from episodic.domain.processing import Event, ProcessingRun, ProcessingWindow
from episodic.domain.provenance import Asset, Environment, EvidenceReference
from episodic.domain.relations import Hypothesis, Relation
from episodic.domain.times import Interval, TimeSpec, utc_now
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceState,
    IndexingState,
    ProvenanceKind,
    RepresentationSpace,
    SearchIntent,
    SourceType,
    ValidationState,
    Verdict,
)

logger = logging.getLogger(__name__)


def _to_json(val: Any) -> str:
    """Serializa estructuras de datos u objetos a JSON string."""
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return json.dumps(val, ensure_ascii=False)


def _from_json(val: Optional[str], default: Any = None) -> Any:
    """Deserializa JSON string o devuelve el valor por defecto."""
    if not val:
        return default
    try:
        return json.loads(val)
    except Exception:
        return default


def _parse_iso(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


class SqliteEpisodicRepository:
    """Repositorio SQLite que implementa el protocolo EpisodicRepository."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # Autocommit por defecto para manejar BEGIN/COMMIT explícitos
        )
        init_connection(raw)
        apply_migrations(raw)
        # Las rutas HTTP comparten esta conexión desde varios hilos.
        self._conn = SerializedConnection(raw)
        self._transaction_owner: int | None = None

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    # -----------------------------------------------------------------------
    # Transacciones
    # -----------------------------------------------------------------------
    # La transacción pertenece al hilo que la abre: mantiene el lock de la
    # conexión hasta COMMIT/ROLLBACK, de modo que otro hilo no puede ni
    # intercalar sentencias en ella ni confirmarla. Las llamadas anidadas del
    # mismo hilo se unen a la transacción en curso, como antes.

    @property
    def _in_transaction(self) -> bool:
        return self._transaction_owner == threading.get_ident()

    def begin_transaction(self) -> None:
        if self._in_transaction:
            return
        self._conn.lock.acquire()
        try:
            self._conn.execute("BEGIN IMMEDIATE;")
        except Exception:
            self._conn.lock.release()
            raise
        self._transaction_owner = threading.get_ident()

    def commit_transaction(self) -> None:
        if not self._in_transaction:
            return
        try:
            self._conn.execute("COMMIT;")
        finally:
            self._transaction_owner = None
            self._conn.lock.release()

    def rollback_transaction(self) -> None:
        if not self._in_transaction:
            return
        try:
            self._conn.execute("ROLLBACK;")
        finally:
            self._transaction_owner = None
            self._conn.lock.release()

    def _require_existing(self, table: str, ids: List[str], field_name: str) -> None:
        """Valida referencias para tablas internas conocidas."""
        allowed = {
            "assets",
            "entities",
            "environments",
            "episodes",
            "events",
            "evidence_references",
            "microepisodes",
            "observations",
            "processing_runs",
            "processing_windows",
            "hypotheses",
            "relations",
            "sources",
        }
        if table not in allowed:
            raise ValueError(f"Tabla de referencia no permitida: {table}")
        missing = [
            item_id
            for item_id in ids
            if self._conn.execute(
                f"SELECT 1 FROM {table} WHERE id = ?", (item_id,)
            ).fetchone()
            is None
        ]
        if missing:
            raise ValueError(f"{field_name} contiene referencias inexistentes: {missing}")

    # -----------------------------------------------------------------------
    # Sources
    # -----------------------------------------------------------------------

    def save_source(self, source: Source) -> None:
        self._require_existing("environments", [source.environment_id], "environment_id")
        sql = """
        INSERT INTO sources (
            id, environment_id, name, source_type, scope, clock_source,
            metadata_json, producer, model_version, confidence, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            environment_id=excluded.environment_id,
            name=excluded.name,
            source_type=excluded.source_type,
            scope=excluded.scope,
            clock_source=excluded.clock_source,
            metadata_json=excluded.metadata_json,
            producer=excluded.producer,
            model_version=excluded.model_version,
            confidence=excluded.confidence;
        """
        self._conn.execute(
            sql,
            (
                source.id,
                source.environment_id,
                source.name,
                source.source_type.value,
                source.scope,
                source.clock_source,
                _to_json(source.metadata),
                source.producer,
                source.model_version,
                source.confidence,
                source.created_at.isoformat(),
            ),
        )

    def get_source(self, source_id: str) -> Optional[Source]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
        row = cur.fetchone()
        if not row:
            return None
        return Source(
            id=row["id"],
            environment_id=row["environment_id"] or "env-legacy-default",
            name=row["name"],
            source_type=SourceType(row["source_type"]),
            scope=row["scope"],
            clock_source=row["clock_source"],
            metadata=_from_json(row["metadata_json"], {}),
            producer=row["producer"],
            model_version=row["model_version"],
            confidence=row["confidence"],
            created_at=_parse_iso(row["created_at"]) or utc_now(),
        )

    def list_sources(self) -> List[Source]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM sources ORDER BY created_at ASC")
        return [
            Source(
                id=row["id"],
                environment_id=row["environment_id"] or "env-legacy-default",
                name=row["name"],
                source_type=SourceType(row["source_type"]),
                scope=row["scope"],
                clock_source=row["clock_source"],
                metadata=_from_json(row["metadata_json"], {}),
                producer=row["producer"],
                model_version=row["model_version"],
                confidence=row["confidence"],
                created_at=_parse_iso(row["created_at"]) or utc_now(),
            )
            for row in cur.fetchall()
        ]

    # -----------------------------------------------------------------------
    # Sessions
    # -----------------------------------------------------------------------

    def save_session(self, session: CaptureSession) -> None:
        sql = """
        INSERT INTO capture_sessions (id, source_id, external_id, started_at_json, ended_at_json, metadata_json, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_id=excluded.source_id,
            external_id=excluded.external_id,
            started_at_json=excluded.started_at_json,
            ended_at_json=excluded.ended_at_json,
            metadata_json=excluded.metadata_json;
        """
        self._conn.execute(
            sql,
            (
                session.id,
                session.source_id,
                session.external_id,
                _to_json(session.started_at.to_dict()) if session.started_at else None,
                _to_json(session.ended_at.to_dict()) if session.ended_at else None,
                _to_json(session.metadata),
                session.ingested_at.isoformat(),
            ),
        )

    def get_session(self, session_id: str) -> Optional[CaptureSession]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM capture_sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        if not row:
            return None
        return CaptureSession(
            id=row["id"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            started_at=TimeSpec.from_dict(_from_json(row["started_at_json"])),
            ended_at=TimeSpec.from_dict(_from_json(row["ended_at_json"])),
            metadata=_from_json(row["metadata_json"], {}),
            ingested_at=_parse_iso(row["ingested_at"]) or utc_now(),
        )

    # -----------------------------------------------------------------------
    # Evidence
    # -----------------------------------------------------------------------

    def save_evidence(self, evidence: Evidence) -> None:
        sql = """
        INSERT INTO evidence (
            id, session_id, source_id, media_type, original_filename,
            internal_uri, content_hash, file_size_bytes, capture_time_json,
            ingested_at, state, external_capture_id, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            state=excluded.state,
            internal_uri=excluded.internal_uri,
            metadata_json=excluded.metadata_json;
        """
        self._conn.execute(
            sql,
            (
                evidence.id,
                evidence.session_id,
                evidence.source_id,
                evidence.media_type.value,
                evidence.original_filename,
                evidence.internal_uri,
                evidence.content_hash,
                evidence.file_size_bytes,
                _to_json(evidence.capture_time.to_dict()) if evidence.capture_time else None,
                evidence.ingested_at.isoformat(),
                evidence.state.value,
                evidence.external_capture_id,
                _to_json(evidence.metadata),
            ),
        )

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_evidence(row)

    def find_evidence_by_key(
        self, source_id: str, session_id: str, external_capture_id: str
    ) -> Optional[Evidence]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM evidence WHERE source_id = ? AND session_id = ? AND external_capture_id = ?",
            (source_id, session_id, external_capture_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_evidence(row)

    def update_evidence_state(self, evidence_id: str, state: EvidenceState) -> None:
        self._conn.execute(
            "UPDATE evidence SET state = ? WHERE id = ?",
            (state.value, evidence_id),
        )

    def list_evidence_for_episode(self, episode_id: str) -> List[Evidence]:
        ep = self.get_episode(episode_id)
        if not ep or not ep.evidence_ids:
            return []
        placeholders = ",".join("?" for _ in ep.evidence_ids)
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM evidence WHERE id IN ({placeholders})",
            ep.evidence_ids,
        )
        return [self._row_to_evidence(r) for r in cur.fetchall()]

    def list_episode_ids_for_evidence(self, evidence_id: str) -> List[str]:
        """Obtiene episodios visibles que enlazan una evidencia concreta."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id FROM episodes
            WHERE is_visible = 1 AND evidence_ids_json LIKE ?
            ORDER BY created_at ASC
            """,
            (f'%"{evidence_id}"%',),
        )
        return [row["id"] for row in cur.fetchall()]

    def count_active_references_for_hash(self, content_hash: str) -> int:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM evidence WHERE content_hash = ? AND state != ?",
            (content_hash, EvidenceState.DELETED.value),
        )
        return cur.fetchone()[0]

    def _row_to_evidence(self, row: sqlite3.Row) -> Evidence:
        return Evidence(
            id=row["id"],
            session_id=row["session_id"],
            source_id=row["source_id"],
            media_type=EvidenceMediaType(row["media_type"]),
            original_filename=row["original_filename"],
            internal_uri=row["internal_uri"],
            content_hash=row["content_hash"],
            file_size_bytes=row["file_size_bytes"],
            capture_time=TimeSpec.from_dict(_from_json(row["capture_time_json"])),
            ingested_at=_parse_iso(row["ingested_at"]) or utc_now(),
            state=EvidenceState(row["state"]),
            external_capture_id=row["external_capture_id"],
            metadata=_from_json(row["metadata_json"], {}),
        )

    # -----------------------------------------------------------------------
    # Observations
    # -----------------------------------------------------------------------

    def save_observation(self, obs: Observation) -> None:
        self._require_existing(
            "evidence_references",
            obs.evidence_reference_ids,
            "evidence_reference_ids",
        )
        sql = """
        INSERT INTO observations (
            id, episode_id, content, description, modality, observation_type,
            validation_state,
            attributes_json, provenance_kind, author, tool_version,
            confidence, evidence_ids_json, entity_id, capture_time_json,
            environment_id, source_id, asset_id, processing_run_id,
            processing_window_id, evidence_reference_ids_json,
            start_time, end_time, producer, model_version, created_at,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            episode_id=excluded.episode_id,
            content=excluded.content,
            description=excluded.description,
            modality=excluded.modality,
            observation_type=excluded.observation_type,
            validation_state=excluded.validation_state,
            attributes_json=excluded.attributes_json,
            provenance_kind=excluded.provenance_kind,
            confidence=excluded.confidence,
            environment_id=excluded.environment_id,
            source_id=excluded.source_id,
            asset_id=excluded.asset_id,
            processing_run_id=excluded.processing_run_id,
            processing_window_id=excluded.processing_window_id,
            evidence_reference_ids_json=excluded.evidence_reference_ids_json,
            start_time=excluded.start_time,
            end_time=excluded.end_time,
            metadata_json=excluded.metadata_json;
        """
        self._conn.execute(
            sql,
            (
                obs.id,
                obs.episode_id,
                obs.content,
                obs.description,
                obs.modality,
                obs.observation_type,
                obs.validation_state.value,
                _to_json(obs.attributes),
                obs.provenance_kind.value,
                obs.author,
                obs.tool_version,
                obs.confidence,
                _to_json(obs.evidence_ids),
                obs.entity_id,
                _to_json(obs.capture_time.to_dict()) if obs.capture_time else None,
                obs.environment_id,
                obs.source_id,
                obs.asset_id,
                obs.processing_run_id,
                obs.processing_window_id,
                _to_json(obs.evidence_reference_ids),
                obs.start_time.isoformat() if obs.start_time else None,
                obs.end_time.isoformat() if obs.end_time else None,
                obs.producer,
                obs.model_version,
                obs.created_at.isoformat(),
                _to_json(obs.metadata),
            ),
        )

    def get_observation(self, observation_id: str) -> Optional[Observation]:
        row = self._conn.execute(
            "SELECT * FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
        return self._row_to_observation(row) if row else None

    def list_observations_for_episode(self, episode_id: str) -> List[Observation]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM observations WHERE episode_id = ? ORDER BY created_at ASC",
            (episode_id,),
        )
        return [self._row_to_observation(r) for r in cur.fetchall()]

    def list_observations_for_entity(
        self, entity_id: str, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[List[Observation], Optional[str]]:
        """Paginación basada en cursor compuesto (created_at, id) para evitar scroll() de tamaño arbitrario."""
        cur = self._conn.cursor()
        if cursor and "|" in cursor:
            c_time, c_id = cursor.split("|", 1)
            cur.execute(
                """
                SELECT * FROM observations 
                WHERE entity_id = ? AND (created_at < ? OR (created_at = ? AND id < ?))
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (entity_id, c_time, c_time, c_id, limit + 1),
            )
        elif cursor:
            cur.execute(
                """
                SELECT * FROM observations 
                WHERE entity_id = ? AND created_at < ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (entity_id, cursor, limit + 1),
            )
        else:
            cur.execute(
                """
                SELECT * FROM observations 
                WHERE entity_id = ? 
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (entity_id, limit + 1),
            )
        rows = cur.fetchall()
        next_cursor = None
        if len(rows) > limit:
            last_row = rows[limit - 1]
            next_cursor = f"{last_row['created_at']}|{last_row['id']}"
            rows = rows[:limit]
        return [self._row_to_observation(r) for r in rows], next_cursor

    def list_observations_for_asset(self, asset_id: str) -> List[Observation]:
        rows = self._conn.execute(
            """
            SELECT * FROM observations
            WHERE asset_id = ? ORDER BY start_time, created_at, id
            """,
            (asset_id,),
        ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def list_observations(
        self, environment_id: Optional[str] = None
    ) -> List[Observation]:
        if environment_id is None:
            rows = self._conn.execute(
                """
                SELECT * FROM observations
                ORDER BY COALESCE(start_time, created_at),
                         COALESCE(end_time, start_time, created_at), id
                """
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM observations WHERE environment_id = ?
                ORDER BY COALESCE(start_time, created_at),
                         COALESCE(end_time, start_time, created_at), id
                """,
                (environment_id,),
            ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def _row_to_observation(self, row: sqlite3.Row) -> Observation:
        return Observation(
            id=row["id"],
            episode_id=row["episode_id"],
            content=row["content"],
            description=row["description"],
            modality=row["modality"],
            observation_type=row["observation_type"],
            validation_state=ValidationState(row["validation_state"]),
            attributes=_from_json(row["attributes_json"], {}),
            provenance_kind=ProvenanceKind(row["provenance_kind"]),
            author=row["author"],
            tool_version=row["tool_version"],
            confidence=row["confidence"],
            evidence_ids=_from_json(row["evidence_ids_json"], []),
            entity_id=row["entity_id"],
            capture_time=TimeSpec.from_dict(_from_json(row["capture_time_json"])),
            environment_id=row["environment_id"],
            source_id=row["source_id"],
            asset_id=row["asset_id"],
            processing_run_id=row["processing_run_id"],
            processing_window_id=row["processing_window_id"],
            evidence_reference_ids=_from_json(row["evidence_reference_ids_json"], []),
            start_time=_parse_iso(row["start_time"]),
            end_time=_parse_iso(row["end_time"]),
            producer=row["producer"],
            model_version=row["model_version"],
            created_at=_parse_iso(row["created_at"]) or utc_now(),
            metadata=_from_json(row["metadata_json"], {}),
        )

    # -----------------------------------------------------------------------
    # Episodes
    # -----------------------------------------------------------------------

    def save_episode(self, episode: Episode) -> None:
        self._require_existing("environments", episode.environment_ids, "environment_ids")
        self._require_existing("microepisodes", episode.microepisode_ids, "microepisode_ids")
        sql = """
        INSERT INTO episodes (
            id, title, external_id, interval_json, source_ids_json, location,
            tags_json, evidence_ids_json, observation_ids_json, indexable_text,
            created_at, is_visible, session_id, metadata_json,
            environment_ids_json, microepisode_ids_json, start_time, end_time,
            producer, model_version, confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            interval_json=excluded.interval_json,
            source_ids_json=excluded.source_ids_json,
            location=excluded.location,
            tags_json=excluded.tags_json,
            evidence_ids_json=excluded.evidence_ids_json,
            observation_ids_json=excluded.observation_ids_json,
            indexable_text=excluded.indexable_text,
            environment_ids_json=excluded.environment_ids_json,
            microepisode_ids_json=excluded.microepisode_ids_json,
            start_time=excluded.start_time,
            end_time=excluded.end_time,
            producer=excluded.producer,
            model_version=excluded.model_version,
            confidence=excluded.confidence,
            is_visible=excluded.is_visible,
            metadata_json=excluded.metadata_json;
        """
        self._conn.execute(
            sql,
            (
                episode.id,
                episode.title,
                episode.external_id,
                _to_json(episode.interval.to_dict()) if episode.interval else None,
                _to_json(episode.source_ids),
                episode.location,
                _to_json(episode.tags),
                _to_json(episode.evidence_ids),
                _to_json(episode.observation_ids),
                episode.indexable_text,
                episode.created_at.isoformat(),
                1 if episode.is_visible else 0,
                episode.session_id,
                _to_json(episode.metadata),
                _to_json(episode.environment_ids),
                _to_json(episode.microepisode_ids),
                episode.start_time.isoformat() if episode.start_time else None,
                episode.end_time.isoformat() if episode.end_time else None,
                episode.producer,
                episode.model_version,
                episode.confidence,
            ),
        )

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_episode(row)

    def find_episode_by_external_id(self, external_id: str) -> Optional[Episode]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE external_id = ?", (external_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_episode(row)

    def list_episodes(
        self,
        source_id: Optional[str] = None,
        interval: Optional[Interval] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Episode], Optional[str]]:
        conditions = ["is_visible = 1"]
        params: List[Any] = []

        if source_id:
            # En SQLite usamos LIKE para buscar dentro de source_ids_json
            conditions.append("source_ids_json LIKE ?")
            params.append(f'%"{source_id}"%')

        if cursor:
            conditions.append("created_at < ?")
            params.append(cursor)

        where_clause = " AND ".join(conditions)
        query = f"SELECT * FROM episodes WHERE {where_clause} ORDER BY created_at DESC LIMIT ?"
        params.append(limit + 1)

        cur = self._conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()

        next_cursor = None
        if len(rows) > limit:
            next_cursor = rows[limit - 1]["created_at"]
            rows = rows[:limit]

        episodes = [self._row_to_episode(r) for r in rows]

        # Si se especificó intervalo, se filtra en memoria con semántica precisa
        if interval:
            filtered = []
            for ep in episodes:
                if ep.interval and ep.interval.overlaps(interval) is not False:
                    filtered.append(ep)
            episodes = filtered

        return episodes, next_cursor

    def set_episode_visibility(self, episode_id: str, visible: bool) -> None:
        self._conn.execute(
            "UPDATE episodes SET is_visible = ? WHERE id = ?",
            (1 if visible else 0, episode_id),
        )

    def list_episodes_for_environment(self, environment_id: str) -> List[Episode]:
        rows = self._conn.execute(
            """
            SELECT * FROM episodes
            WHERE EXISTS (
                SELECT 1 FROM json_each(episodes.environment_ids_json)
                WHERE json_each.value = ?
            )
            ORDER BY COALESCE(start_time, created_at), id
            """,
            (environment_id,),
        ).fetchall()
        return [self._row_to_episode(row) for row in rows]

    def delete_episode_record(self, episode_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
        return cursor.rowcount > 0

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        return Episode(
            id=row["id"],
            title=row["title"],
            external_id=row["external_id"],
            interval=Interval.from_dict(_from_json(row["interval_json"])),
            source_ids=_from_json(row["source_ids_json"], []),
            location=row["location"],
            tags=_from_json(row["tags_json"], []),
            evidence_ids=_from_json(row["evidence_ids_json"], []),
            observation_ids=_from_json(row["observation_ids_json"], []),
            indexable_text=row["indexable_text"],
            created_at=_parse_iso(row["created_at"]) or utc_now(),
            is_visible=bool(row["is_visible"]),
            session_id=row["session_id"],
            environment_ids=_from_json(row["environment_ids_json"], []),
            microepisode_ids=_from_json(row["microepisode_ids_json"], []),
            start_time=_parse_iso(row["start_time"]),
            end_time=_parse_iso(row["end_time"]),
            producer=row["producer"],
            model_version=row["model_version"],
            confidence=row["confidence"],
            metadata=_from_json(row["metadata_json"], {}),
        )

    # -----------------------------------------------------------------------
    # Annotations
    # -----------------------------------------------------------------------

    def save_annotation(self, ann: Annotation) -> None:
        sql = """
        INSERT INTO annotations (
            id, episode_id, revision_number, content, provenance_kind,
            author, tool_version, evidence_ids_json, supersedes_id,
            created_at, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            content=excluded.content,
            metadata_json=excluded.metadata_json;
        """
        self._conn.execute(
            sql,
            (
                ann.id,
                ann.episode_id,
                ann.revision_number,
                ann.content,
                ann.provenance_kind.value,
                ann.author,
                ann.tool_version,
                _to_json(ann.evidence_ids),
                ann.supersedes_id,
                ann.created_at.isoformat(),
                _to_json(ann.metadata),
            ),
        )

    def list_annotations_for_episode(self, episode_id: str) -> List[Annotation]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM annotations WHERE episode_id = ? ORDER BY revision_number ASC",
            (episode_id,),
        )
        return [self._row_to_annotation(r) for r in cur.fetchall()]

    def get_latest_annotation(self, episode_id: str) -> Optional[Annotation]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM annotations WHERE episode_id = ? ORDER BY revision_number DESC LIMIT 1",
            (episode_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_annotation(row)

    def _row_to_annotation(self, row: sqlite3.Row) -> Annotation:
        return Annotation(
            id=row["id"],
            episode_id=row["episode_id"],
            revision_number=row["revision_number"],
            content=row["content"],
            provenance_kind=ProvenanceKind(row["provenance_kind"]),
            author=row["author"],
            tool_version=row["tool_version"],
            evidence_ids=_from_json(row["evidence_ids_json"], []),
            supersedes_id=row["supersedes_id"],
            created_at=_parse_iso(row["created_at"]) or utc_now(),
            metadata=_from_json(row["metadata_json"], {}),
        )

    # -----------------------------------------------------------------------
    # Entities
    # -----------------------------------------------------------------------

    def save_entity(self, entity: Entity) -> None:
        self._require_existing("environments", entity.environment_ids, "environment_ids")
        sql = """
        INSERT INTO entities (
            id, label, entity_type, description, created_by, provenance_kind,
            created_at, metadata_json, environment_ids_json, producer,
            model_version, confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            label=excluded.label,
            entity_type=excluded.entity_type,
            description=excluded.description,
            environment_ids_json=excluded.environment_ids_json,
            producer=excluded.producer,
            model_version=excluded.model_version,
            confidence=excluded.confidence,
            metadata_json=excluded.metadata_json;
        """
        self._conn.execute(
            sql,
            (
                entity.id,
                entity.label,
                entity.entity_type,
                entity.description,
                entity.created_by,
                entity.provenance_kind.value,
                entity.created_at.isoformat(),
                _to_json(entity.metadata),
                _to_json(entity.environment_ids),
                entity.producer,
                entity.model_version,
                entity.confidence,
            ),
        )

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
        row = cur.fetchone()
        if not row:
            return None
        return Entity(
            id=row["id"],
            label=row["label"],
            entity_type=row["entity_type"],
            description=row["description"],
            created_by=row["created_by"],
            provenance_kind=ProvenanceKind(row["provenance_kind"]),
            environment_ids=_from_json(row["environment_ids_json"], []),
            producer=row["producer"],
            model_version=row["model_version"],
            confidence=row["confidence"],
            created_at=_parse_iso(row["created_at"]) or utc_now(),
            metadata=_from_json(row["metadata_json"], {}),
        )

    def list_entities(self) -> List[Entity]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM entities ORDER BY label ASC")
        return [
            Entity(
                id=row["id"],
                label=row["label"],
                entity_type=row["entity_type"],
                description=row["description"],
                created_by=row["created_by"],
                provenance_kind=ProvenanceKind(row["provenance_kind"]),
                environment_ids=_from_json(row["environment_ids_json"], []),
                producer=row["producer"],
                model_version=row["model_version"],
                confidence=row["confidence"],
                created_at=_parse_iso(row["created_at"]) or utc_now(),
                metadata=_from_json(row["metadata_json"], {}),
            )
            for row in cur.fetchall()
        ]

    # -----------------------------------------------------------------------
    # Fase 2: procedencia, procesamiento, composición y relaciones
    # -----------------------------------------------------------------------

    def save_environment(self, environment: Environment) -> None:
        self._conn.execute(
            """
            INSERT INTO environments (
                id, name, description, producer, model_version, confidence,
                created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                producer=excluded.producer,
                model_version=excluded.model_version,
                confidence=excluded.confidence,
                metadata_json=excluded.metadata_json
            """,
            (
                environment.id,
                environment.name,
                environment.description,
                environment.producer,
                environment.model_version,
                environment.confidence,
                environment.created_at.isoformat(),
                _to_json(environment.metadata),
            ),
        )

    def get_environment(self, environment_id: str) -> Optional[Environment]:
        row = self._conn.execute(
            "SELECT * FROM environments WHERE id = ?", (environment_id,)
        ).fetchone()
        if not row:
            return None
        return Environment(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            producer=row["producer"],
            model_version=row["model_version"],
            confidence=row["confidence"],
            created_at=_parse_iso(row["created_at"]) or utc_now(),
            metadata=_from_json(row["metadata_json"], {}),
        )

    def list_environments(self) -> List[Environment]:
        rows = self._conn.execute(
            "SELECT * FROM environments ORDER BY name, id"
        ).fetchall()
        return [self.get_environment(row["id"]) for row in rows]  # type: ignore[misc]

    def save_asset(self, asset: Asset) -> None:
        self._require_existing("environments", [asset.environment_id], "environment_id")
        self._require_existing("sources", [asset.source_id], "source_id")
        existing = self.get_asset(asset.id)
        if existing is not None:
            immutable_fields = (
                "environment_id",
                "source_id",
                "uri",
                "media_type",
                "content_hash",
                "original_filename",
                "captured_at",
            )
            if any(getattr(existing, name) != getattr(asset, name) for name in immutable_fields):
                raise ValueError(f"Asset inmutable ya existe con contenido distinto: {asset.id}")
            self._conn.execute(
                """
                UPDATE assets SET mime_type = ?, size_bytes = ?, duration_seconds = ?,
                    payload_json = ? WHERE id = ?
                """,
                (
                    asset.mime_type,
                    asset.size_bytes,
                    asset.duration_seconds,
                    _to_json(asset.to_dict()),
                    asset.id,
                ),
            )
            return
        self._conn.execute(
            """
            INSERT INTO assets (
                id, environment_id, source_id, uri, media_type, content_hash,
                mime_type, size_bytes, duration_seconds, captured_at, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                asset.id,
                asset.environment_id,
                asset.source_id,
                asset.uri,
                asset.media_type.value,
                asset.content_hash,
                asset.mime_type,
                asset.size_bytes,
                asset.duration_seconds,
                asset.captured_at.isoformat() if asset.captured_at else None,
                asset.created_at.isoformat(),
                _to_json(asset.to_dict()),
            ),
        )

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        row = self._conn.execute(
            "SELECT payload_json FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        return Asset.from_dict(_from_json(row["payload_json"])) if row else None

    def find_asset_by_hash(self, content_hash: str) -> Optional[Asset]:
        row = self._conn.execute(
            "SELECT payload_json FROM assets WHERE content_hash = ? ORDER BY created_at, id LIMIT 1",
            (content_hash,),
        ).fetchone()
        return Asset.from_dict(_from_json(row["payload_json"])) if row else None

    def list_assets(self, environment_id: Optional[str] = None) -> List[Asset]:
        if environment_id:
            rows = self._conn.execute(
                "SELECT payload_json FROM assets WHERE environment_id = ? ORDER BY created_at, id",
                (environment_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload_json FROM assets ORDER BY created_at, id"
            ).fetchall()
        return [Asset.from_dict(_from_json(row["payload_json"])) for row in rows]

    def save_processing_run(self, run: ProcessingRun) -> None:
        self._require_existing("environments", [run.environment_id], "environment_id")
        self._require_existing("sources", [run.source_id] if run.source_id else [], "source_id")
        self._require_existing("assets", run.asset_ids, "asset_ids")
        self._conn.execute(
            """
            INSERT INTO processing_runs (
                id, environment_id, source_id, state, start_time, end_time,
                created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state=excluded.state,
                end_time=excluded.end_time,
                payload_json=excluded.payload_json
            """,
            (
                run.id,
                run.environment_id,
                run.source_id,
                run.state.value,
                run.start_time.isoformat() if run.start_time else None,
                run.end_time.isoformat() if run.end_time else None,
                run.created_at.isoformat(),
                _to_json(run.to_dict()),
            ),
        )

    def get_processing_run(self, run_id: str) -> Optional[ProcessingRun]:
        row = self._conn.execute(
            "SELECT payload_json FROM processing_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return ProcessingRun.from_dict(_from_json(row["payload_json"])) if row else None

    def save_processing_window(self, window: ProcessingWindow) -> None:
        self._require_existing("processing_runs", [window.processing_run_id], "processing_run_id")
        self._require_existing("environments", [window.environment_id], "environment_id")
        self._require_existing("sources", [window.source_id], "source_id")
        self._require_existing("assets", [window.asset_id], "asset_id")
        self._conn.execute(
            """
            INSERT INTO processing_windows (
                id, processing_run_id, environment_id, source_id, asset_id,
                start_time, end_time, start_offset_seconds, end_offset_seconds,
                delta_time_seconds, overlap_seconds, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                start_offset_seconds=excluded.start_offset_seconds,
                end_offset_seconds=excluded.end_offset_seconds,
                delta_time_seconds=excluded.delta_time_seconds,
                overlap_seconds=excluded.overlap_seconds,
                payload_json=excluded.payload_json
            """,
            (
                window.id,
                window.processing_run_id,
                window.environment_id,
                window.source_id,
                window.asset_id,
                window.start_time.isoformat(),  # type: ignore[union-attr]
                window.end_time.isoformat(),  # type: ignore[union-attr]
                window.start_offset_seconds,
                window.end_offset_seconds,
                window.delta_time_seconds,
                window.overlap_seconds,
                window.created_at.isoformat(),
                _to_json(window.to_dict()),
            ),
        )

    def get_processing_window(self, window_id: str) -> Optional[ProcessingWindow]:
        row = self._conn.execute(
            "SELECT payload_json FROM processing_windows WHERE id = ?", (window_id,)
        ).fetchone()
        return ProcessingWindow.from_dict(_from_json(row["payload_json"])) if row else None

    def list_processing_windows_for_asset(self, asset_id: str) -> List[ProcessingWindow]:
        rows = self._conn.execute(
            """
            SELECT payload_json FROM processing_windows
            WHERE asset_id = ? ORDER BY start_offset_seconds, id
            """,
            (asset_id,),
        ).fetchall()
        return [ProcessingWindow.from_dict(_from_json(row["payload_json"])) for row in rows]

    def save_evidence_reference(self, reference: EvidenceReference) -> None:
        self._require_existing("assets", [reference.asset_id], "asset_id")
        self._conn.execute(
            """
            INSERT INTO evidence_references (
                id, asset_id, reference_kind, start_time, end_time,
                created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
            """,
            (
                reference.id,
                reference.asset_id,
                reference.reference_kind.value,
                reference.start_time.isoformat() if reference.start_time else None,
                reference.end_time.isoformat() if reference.end_time else None,
                reference.created_at.isoformat(),
                _to_json(reference.to_dict()),
            ),
        )

    def get_evidence_reference(self, reference_id: str) -> Optional[EvidenceReference]:
        row = self._conn.execute(
            "SELECT payload_json FROM evidence_references WHERE id = ?", (reference_id,)
        ).fetchone()
        return EvidenceReference.from_dict(_from_json(row["payload_json"])) if row else None

    def list_evidence_references_for_asset(self, asset_id: str) -> List[EvidenceReference]:
        rows = self._conn.execute(
            "SELECT payload_json FROM evidence_references WHERE asset_id = ? ORDER BY created_at, id",
            (asset_id,),
        ).fetchall()
        return [EvidenceReference.from_dict(_from_json(row["payload_json"])) for row in rows]

    def save_event(self, event: Event) -> None:
        self._require_existing("environments", [event.environment_id], "environment_id")
        self._require_existing("sources", [event.source_id] if event.source_id else [], "source_id")
        self._require_existing("entities", event.entity_ids, "entity_ids")
        self._require_existing(
            "evidence_references", event.evidence_reference_ids, "evidence_reference_ids"
        )
        self._conn.execute(
            """
            INSERT INTO events (
                id, environment_id, source_id, processing_run_id,
                processing_window_id, event_type, start_time, end_time,
                created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
            """,
            (
                event.id,
                event.environment_id,
                event.source_id,
                event.processing_run_id,
                event.processing_window_id,
                event.event_type,
                event.start_time.isoformat(),  # type: ignore[union-attr]
                event.end_time.isoformat(),  # type: ignore[union-attr]
                event.created_at.isoformat(),
                _to_json(event.to_dict()),
            ),
        )

    def get_event(self, event_id: str) -> Optional[Event]:
        row = self._conn.execute(
            "SELECT payload_json FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return Event.from_dict(_from_json(row["payload_json"])) if row else None

    def save_microepisode(self, microepisode: MicroEpisode) -> None:
        self._require_existing("environments", [microepisode.environment_id], "environment_id")
        self._require_existing("sources", microepisode.source_ids, "source_ids")
        self._require_existing("observations", microepisode.observation_ids, "observation_ids")
        self._require_existing("events", microepisode.event_ids, "event_ids")
        self._require_existing(
            "evidence_references",
            microepisode.evidence_reference_ids,
            "evidence_reference_ids",
        )
        self._conn.execute(
            """
            INSERT INTO microepisodes (
                id, environment_id, title, start_time, end_time,
                created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
            """,
            (
                microepisode.id,
                microepisode.environment_id,
                microepisode.title,
                microepisode.start_time.isoformat(),  # type: ignore[union-attr]
                microepisode.end_time.isoformat(),  # type: ignore[union-attr]
                microepisode.created_at.isoformat(),
                _to_json(microepisode.to_dict()),
            ),
        )

    def get_microepisode(self, microepisode_id: str) -> Optional[MicroEpisode]:
        row = self._conn.execute(
            "SELECT payload_json FROM microepisodes WHERE id = ?", (microepisode_id,)
        ).fetchone()
        return MicroEpisode.from_dict(_from_json(row["payload_json"])) if row else None

    def list_microepisodes(
        self, environment_id: Optional[str] = None
    ) -> List[MicroEpisode]:
        if environment_id is None:
            rows = self._conn.execute(
                "SELECT payload_json FROM microepisodes ORDER BY start_time, end_time, id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT payload_json FROM microepisodes
                WHERE environment_id = ? ORDER BY start_time, end_time, id
                """,
                (environment_id,),
            ).fetchall()
        return [MicroEpisode.from_dict(_from_json(row["payload_json"])) for row in rows]

    def delete_microepisode(self, microepisode_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM microepisodes WHERE id = ?", (microepisode_id,)
        )
        return cursor.rowcount > 0

    _REFERENCE_TABLES = {
        "environment": "environments",
        "source": "sources",
        "asset": "assets",
        "processing_window": "processing_windows",
        "observation": "observations",
        "entity": "entities",
        "event": "events",
        "microepisode": "microepisodes",
        "episode": "episodes",
        "evidence_reference": "evidence_references",
        "relation": "relations",
        "hypothesis": "hypotheses",
    }

    def _require_domain_reference(self, target_type: str, target_id: str) -> None:
        table = self._REFERENCE_TABLES.get(target_type)
        if table is None:
            raise ValueError(f"Tipo de referencia de dominio no soportado: {target_type}")
        self._require_existing(table, [target_id], f"{target_type}_id")

    def save_relation(self, relation: Relation) -> None:
        self._require_domain_reference(relation.origin_type, relation.origin_id)
        self._require_domain_reference(relation.destination_type, relation.destination_id)
        self._require_existing(
            "evidence_references", relation.evidence_reference_ids, "evidence_reference_ids"
        )
        self._conn.execute(
            """
            INSERT INTO relations (
                id, relation_type, origin_type, origin_id, destination_type,
                destination_id, validation_state, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                validation_state=excluded.validation_state,
                payload_json=excluded.payload_json
            """,
            (
                relation.id,
                relation.relation_type,
                relation.origin_type,
                relation.origin_id,
                relation.destination_type,
                relation.destination_id,
                relation.validation_state.value,
                relation.created_at.isoformat(),
                _to_json(relation.to_dict()),
            ),
        )

    def get_relation(self, relation_id: str) -> Optional[Relation]:
        row = self._conn.execute(
            "SELECT payload_json FROM relations WHERE id = ?", (relation_id,)
        ).fetchone()
        return Relation.from_dict(_from_json(row["payload_json"])) if row else None

    def list_relations(
        self,
        relation_type: Optional[str] = None,
        node_type: Optional[str] = None,
        node_id: Optional[str] = None,
        validation_state: Optional[ValidationState] = None,
    ) -> List[Relation]:
        if (node_type is None) != (node_id is None):
            raise ValueError("node_type y node_id deben declararse juntos")
        conditions: list[str] = []
        params: list[Any] = []
        if relation_type is not None:
            conditions.append("relation_type = ?")
            params.append(relation_type.upper())
        if node_type is not None and node_id is not None:
            conditions.append(
                "((origin_type = ? AND origin_id = ?) OR "
                "(destination_type = ? AND destination_id = ?))"
            )
            params.extend((node_type, node_id, node_type, node_id))
        if validation_state is not None:
            conditions.append("validation_state = ?")
            params.append(validation_state.value)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT payload_json FROM relations {where} ORDER BY created_at, id",
            params,
        ).fetchall()
        return [Relation.from_dict(_from_json(row["payload_json"])) for row in rows]

    def save_hypothesis(self, hypothesis: Hypothesis) -> None:
        self._require_domain_reference(hypothesis.subject_type, hypothesis.subject_id)
        if hypothesis.object_type and hypothesis.object_id:
            self._require_domain_reference(hypothesis.object_type, hypothesis.object_id)
        self._require_existing(
            "evidence_references",
            hypothesis.evidence_reference_ids,
            "evidence_reference_ids",
        )
        self._conn.execute(
            """
            INSERT INTO hypotheses (
                id, subject_type, subject_id, state, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state=excluded.state,
                payload_json=excluded.payload_json
            """,
            (
                hypothesis.id,
                hypothesis.subject_type,
                hypothesis.subject_id,
                hypothesis.state.value,
                hypothesis.created_at.isoformat(),
                _to_json(hypothesis.to_dict()),
            ),
        )

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Hypothesis]:
        row = self._conn.execute(
            "SELECT payload_json FROM hypotheses WHERE id = ?", (hypothesis_id,)
        ).fetchone()
        return Hypothesis.from_dict(_from_json(row["payload_json"])) if row else None

    def list_hypotheses(
        self, node_type: Optional[str] = None, node_id: Optional[str] = None,
    ) -> List[Hypothesis]:
        if (node_type is None) != (node_id is None):
            raise ValueError("node_type y node_id deben declararse juntos")
        if node_type is None:
            rows = self._conn.execute(
                "SELECT payload_json FROM hypotheses ORDER BY created_at, id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT payload_json FROM hypotheses
                WHERE (subject_type = ? AND subject_id = ?)
                   OR (json_extract(payload_json, '$.object_type') = ?
                       AND json_extract(payload_json, '$.object_id') = ?)
                ORDER BY created_at, id
                """,
                (node_type, node_id, node_type, node_id),
            ).fetchall()
        return [Hypothesis.from_dict(_from_json(row["payload_json"])) for row in rows]

    # -----------------------------------------------------------------------
    # Representations (Outbox de indexación)
    # -----------------------------------------------------------------------

    def save_representation(self, rep: Representation) -> None:
        sql = """
        INSERT INTO representations (
            id, target_type, target_id, space, model_id, model_revision,
            dimension, indexing_state, qdrant_point_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            indexing_state=excluded.indexing_state,
            qdrant_point_id=excluded.qdrant_point_id;
        """
        self._conn.execute(
            sql,
            (
                rep.id,
                rep.target_type,
                rep.target_id,
                rep.space.value,
                rep.model_id,
                rep.model_revision,
                rep.dimension,
                rep.indexing_state.value,
                rep.qdrant_point_id,
                rep.created_at.isoformat(),
            ),
        )

    def list_pending_representations(self, limit: int = 100) -> List[Representation]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM representations WHERE indexing_state = ? ORDER BY created_at ASC LIMIT ?",
            (IndexingState.PENDING.value, limit),
        )
        return [self._row_to_representation(r) for r in cur.fetchall()]

    def update_representation_state(
        self, rep_id: str, state: IndexingState, qdrant_point_id: Optional[str] = None
    ) -> None:
        if qdrant_point_id is not None:
            self._conn.execute(
                "UPDATE representations SET indexing_state = ?, qdrant_point_id = ? WHERE id = ?",
                (state.value, qdrant_point_id, rep_id),
            )
        else:
            self._conn.execute(
                "UPDATE representations SET indexing_state = ? WHERE id = ?",
                (state.value, rep_id),
            )

    def list_representations_for_target(self, target_id: str) -> List[Representation]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM representations WHERE target_id = ? ORDER BY created_at ASC",
            (target_id,),
        )
        return [self._row_to_representation(r) for r in cur.fetchall()]

    def get_indexing_summary(self) -> Dict[str, int]:
        """Resume la outbox sin cargar todas las representaciones en memoria."""
        summary = {state.value: 0 for state in IndexingState}
        cur = self._conn.cursor()
        cur.execute(
            "SELECT indexing_state, COUNT(*) AS count FROM representations GROUP BY indexing_state"
        )
        for row in cur.fetchall():
            summary[str(row["indexing_state"])] = int(row["count"])
        summary["total"] = sum(
            count for state, count in summary.items() if state != "total"
        )
        return summary

    def _row_to_representation(self, row: sqlite3.Row) -> Representation:
        return Representation(
            id=row["id"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            space=RepresentationSpace(row["space"]),
            model_id=row["model_id"],
            model_revision=row["model_revision"],
            dimension=row["dimension"],
            indexing_state=IndexingState(row["indexing_state"]),
            qdrant_point_id=row["qdrant_point_id"],
            created_at=_parse_iso(row["created_at"]) or utc_now(),
        )

    # -----------------------------------------------------------------------
    # Capturas y trabajos asíncronos
    # -----------------------------------------------------------------------

    def save_capture(self, capture: Capture) -> None:
        self._require_existing("environments", [capture.environment_id], "environment_id")
        self._require_existing("assets", capture.asset_ids, "asset_ids")
        self._conn.execute(
            """
            INSERT INTO captures (
                id, environment_id, title, status, episode_id, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                status=excluded.status,
                episode_id=excluded.episode_id,
                payload_json=excluded.payload_json
            """,
            (
                capture.id,
                capture.environment_id,
                capture.title,
                capture.status.value,
                capture.episode_id,
                capture.created_at.isoformat(),
                _to_json(capture.to_dict()),
            ),
        )

    def get_capture(self, capture_id: str) -> Optional[Capture]:
        row = self._conn.execute(
            "SELECT payload_json FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        return Capture.from_dict(_from_json(row["payload_json"])) if row else None

    def find_capture_for_episode(self, episode_id: str) -> Optional[Capture]:
        row = self._conn.execute(
            "SELECT payload_json FROM captures WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        return Capture.from_dict(_from_json(row["payload_json"])) if row else None

    def list_captures(
        self, environment_id: Optional[str] = None, limit: int = 50
    ) -> List[Capture]:
        if environment_id is None:
            rows = self._conn.execute(
                "SELECT payload_json FROM captures ORDER BY created_at DESC, id LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT payload_json FROM captures
                WHERE environment_id = ? ORDER BY created_at DESC, id LIMIT ?
                """,
                (environment_id, limit),
            ).fetchall()
        return [Capture.from_dict(_from_json(row["payload_json"])) for row in rows]

    def save_job(self, job: Job) -> None:
        self._conn.execute(
            """
            INSERT INTO jobs (
                id, kind, target_id, state, created_at, updated_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state=excluded.state,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                job.id,
                job.job_type,
                job.target_id,
                job.state.value,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
                _to_json(job.to_dict()),
            ),
        )

    def get_job(self, job_id: str) -> Optional[Job]:
        row = self._conn.execute(
            "SELECT payload_json FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return Job.from_dict(_from_json(row["payload_json"])) if row else None

    def list_jobs(
        self,
        states: Optional[List[JobState]] = None,
        target_id: Optional[str] = None,
    ) -> List[Job]:
        conditions: List[str] = []
        params: List[Any] = []
        if states:
            conditions.append(f"state IN ({', '.join('?' for _ in states)})")
            params.extend(state.value for state in states)
        if target_id:
            conditions.append("target_id = ?")
            params.append(target_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT payload_json FROM jobs {where} ORDER BY created_at, id",
            params,
        ).fetchall()
        return [Job.from_dict(_from_json(row["payload_json"])) for row in rows]

    def list_recent_jobs(self, limit: int = 50) -> List[Job]:
        rows = self._conn.execute(
            "SELECT payload_json FROM jobs ORDER BY created_at DESC, id LIMIT ?",
            (limit,),
        ).fetchall()
        return [Job.from_dict(_from_json(row["payload_json"])) for row in rows]

    # -----------------------------------------------------------------------
    # Entrada automática (carpetas vigiladas) y registro de ejecución
    # -----------------------------------------------------------------------

    def save_inbox_file(self, item: InboxFile) -> None:
        self._conn.execute(
            """
            INSERT INTO inbox_files (
                id, source_name, rel_path, group_key, state, capture_id,
                archived, updated_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                group_key=excluded.group_key,
                state=excluded.state,
                capture_id=excluded.capture_id,
                archived=excluded.archived,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                item.id,
                item.source_name,
                item.rel_path,
                item.group_key,
                item.state.value,
                item.capture_id,
                0 if item.present else 1,
                item.updated_at.isoformat(),
                _to_json(item.to_dict()),
            ),
        )

    def list_live_inbox_files(self, source_name: str) -> List[InboxFile]:
        """Ficheros que siguen en la carpeta (ni archivados ni desaparecidos)."""
        rows = self._conn.execute(
            """
            SELECT payload_json FROM inbox_files
            WHERE source_name = ? AND archived = 0 ORDER BY rel_path
            """,
            (source_name,),
        ).fetchall()
        return [InboxFile.from_dict(_from_json(row["payload_json"])) for row in rows]

    def list_inbox_group_keys(self, source_name: str) -> List[str]:
        """Grupos que ya produjeron una captura (también los archivados)."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT group_key FROM inbox_files
            WHERE source_name = ? AND capture_id IS NOT NULL
            """,
            (source_name,),
        ).fetchall()
        return [row["group_key"] for row in rows]

    def list_inbox_files(
        self,
        source_name: Optional[str] = None,
        states: Optional[List[InboxFileState]] = None,
        limit: int = 200,
    ) -> List[InboxFile]:
        conditions: List[str] = []
        params: List[Any] = []
        if source_name:
            conditions.append("source_name = ?")
            params.append(source_name)
        if states:
            conditions.append(f"state IN ({', '.join('?' for _ in states)})")
            params.extend(state.value for state in states)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT payload_json FROM inbox_files {where} ORDER BY updated_at DESC, id LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [InboxFile.from_dict(_from_json(row["payload_json"])) for row in rows]

    def count_inbox_files(self, source_name: str) -> Dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT state, COUNT(*) AS n FROM inbox_files
            WHERE source_name = ? GROUP BY state
            """,
            (source_name,),
        ).fetchall()
        return {row["state"]: row["n"] for row in rows}

    def append_activity(self, entry: ActivityEntry) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO activity_log (at, level, component, job_id, capture_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entry.at.isoformat(),
                entry.level.value,
                entry.component,
                entry.job_id,
                entry.capture_id,
                _to_json(entry.to_dict()),
            ),
        )
        return int(cursor.lastrowid)

    def list_activity(
        self,
        after_id: int = 0,
        limit: int = 200,
        component: Optional[str] = None,
        levels: Optional[List[ActivityLevel]] = None,
    ) -> List[ActivityEntry]:
        """Líneas posteriores a ``after_id`` o, sin él, las ``limit`` más recientes.

        Siempre en orden cronológico.
        """
        conditions = ["id > ?"]
        params: List[Any] = [after_id]
        if component:
            conditions.append("component = ?")
            params.append(component)
        if levels:
            conditions.append(f"level IN ({', '.join('?' for _ in levels)})")
            params.extend(level.value for level in levels)
        order = "ASC" if after_id else "DESC"
        rows = self._conn.execute(
            f"""
            SELECT id, payload_json FROM activity_log
            WHERE {' AND '.join(conditions)} ORDER BY id {order} LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        entries = [
            ActivityEntry.from_dict({**_from_json(row["payload_json"]), "id": row["id"]})
            for row in rows
        ]
        return entries if after_id else entries[::-1]

    def prune_activity(
        self, keep: int, levels: Optional[List[ActivityLevel]] = None
    ) -> int:
        """Conserva las ``keep`` líneas más recientes (de esos niveles, si se dan)."""
        scope, params = "1 = 1", []
        if levels:
            scope = f"level IN ({', '.join('?' for _ in levels)})"
            params = [level.value for level in levels]
        # Id de la línea más antigua que se conserva; sin ella, no sobra ninguna.
        cursor = self._conn.execute(
            f"""
            DELETE FROM activity_log
            WHERE {scope} AND id < COALESCE((
                SELECT id FROM activity_log WHERE {scope}
                ORDER BY id DESC LIMIT 1 OFFSET ?
            ), 0)
            """,
            [*params, *params, max(keep, 1) - 1],
        )
        return cursor.rowcount

    def acquire_lease(self, name: str, owner: str, ttl_seconds: float, now: float) -> bool:
        """Toma o renueva una concesión; falla si otro dueño la tiene vigente."""
        cursor = self._conn.execute(
            """
            INSERT INTO leases (name, owner, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                owner=excluded.owner, expires_at=excluded.expires_at
            WHERE leases.owner = excluded.owner OR leases.expires_at < ?
            """,
            (name, owner, now + ttl_seconds, now),
        )
        return cursor.rowcount == 1

    def get_lease(self, name: str) -> Optional[Tuple[str, float]]:
        row = self._conn.execute(
            "SELECT owner, expires_at FROM leases WHERE name = ?", (name,)
        ).fetchone()
        return (row["owner"], float(row["expires_at"])) if row else None

    def release_lease(self, name: str, owner: str) -> None:
        self._conn.execute(
            "DELETE FROM leases WHERE name = ? AND owner = ?", (name, owner)
        )

    # -----------------------------------------------------------------------
    # QueryRuns (Auditoría)
    # -----------------------------------------------------------------------

    def save_query_run(self, qr: QueryRun) -> None:
        results_dicts = [
            {
                "episode_id": r.episode_id,
                "score": r.score,
                "rank": r.rank,
                "space": r.space.value if r.space else None,
                "original_score": r.original_score,
                "evidence_ids": r.evidence_ids,
                "is_direct_match": r.is_direct_match,
                "verdict": r.verdict.value,
                "reasons": r.reasons,
            }
            for r in qr.results
        ]
        sql = """
        INSERT INTO query_runs (
            id, intent, text_query, image_query_evidence_id, source_filter_json,
            interval_filter_json, entity_filter, limit_val, results_json,
            overall_verdict, reasons_json, executed_at, duration_ms, config_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            results_json=excluded.results_json,
            overall_verdict=excluded.overall_verdict;
        """
        self._conn.execute(
            sql,
            (
                qr.id,
                qr.intent.value,
                qr.text_query,
                qr.image_query_evidence_id,
                _to_json(qr.source_filter),
                _to_json(qr.interval_filter.to_dict()) if qr.interval_filter else None,
                qr.entity_filter,
                qr.limit,
                _to_json(results_dicts),
                qr.overall_verdict.value,
                _to_json(qr.reasons),
                qr.executed_at.isoformat(),
                qr.duration_ms,
                _to_json(qr.config),
            ),
        )

    def get_query_run(self, run_id: str) -> Optional[QueryRun]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM query_runs WHERE id = ?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        raw_results = _from_json(row["results_json"], [])
        results = [
            QueryResult(
                episode_id=d.get("episode_id", ""),
                score=d.get("score", 0.0),
                rank=d.get("rank", 0),
                space=RepresentationSpace(d["space"]) if d.get("space") else None,
                original_score=d.get("original_score", 0.0),
                evidence_ids=d.get("evidence_ids", []),
                is_direct_match=d.get("is_direct_match", True),
                verdict=Verdict(d["verdict"]) if d.get("verdict") else Verdict.CANDIDATE,
                reasons=d.get("reasons", []),
            )
            for d in raw_results
        ]
        return QueryRun(
            id=row["id"],
            intent=SearchIntent(row["intent"]),
            text_query=row["text_query"],
            image_query_evidence_id=row["image_query_evidence_id"],
            source_filter=_from_json(row["source_filter_json"]),
            interval_filter=Interval.from_dict(_from_json(row["interval_filter_json"])),
            entity_filter=row["entity_filter"],
            limit=row["limit_val"],
            results=results,
            overall_verdict=Verdict(row["overall_verdict"]),
            reasons=_from_json(row["reasons_json"], []),
            executed_at=_parse_iso(row["executed_at"]) or utc_now(),
            duration_ms=row["duration_ms"],
            config=_from_json(row["config_json"], {}),
        )
