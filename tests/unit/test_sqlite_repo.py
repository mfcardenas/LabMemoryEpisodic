"""Tests unitarios para la persistencia SQLite (fuente de verdad canónica)."""
from pathlib import Path
import sqlite3
import pytest

from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.domain.entities import (
    Episode,
    Evidence,
    Observation,
    Representation,
    Source,
)
from episodic.domain.times import utc_now
from episodic.domain.types import (
    IndexingState,
    ProvenanceKind,
    RepresentationSpace,
    SourceType,
)


@pytest.fixture
def repo(tmp_path: Path):
    db_file = tmp_path / "test_episodic.db"
    r = SqliteEpisodicRepository(db_file)
    yield r
    r.close()


def test_wal_mode_and_foreign_keys(repo: SqliteEpisodicRepository):
    cur = repo._conn.cursor()
    cur.execute("PRAGMA journal_mode;")
    mode = cur.fetchone()[0]
    assert mode.lower() == "wal"

    cur.execute("PRAGMA foreign_keys;")
    fk = cur.fetchone()[0]
    assert fk == 1


def test_cursor_pagination_observations(repo: SqliteEpisodicRepository):
    # Guardar episodio
    ep = Episode(id="ep-pagination", title="Episodio de prueba")
    repo.save_episode(ep)

    # Guardar 5 observaciones para una misma entidad
    for i in range(5):
        obs = Observation(
            id=f"obs-{i}",
            episode_id="ep-pagination",
            entity_id="entity-x",
            content=f"Observación número {i}",
            provenance_kind=ProvenanceKind.VISUAL_DETECTION,
        )
        repo.save_observation(obs)

    # Página 1 (limit=2)
    p1, cursor1 = repo.list_observations_for_entity("entity-x", limit=2)
    assert len(p1) == 2
    assert cursor1 is not None

    # Página 2 (limit=2 con cursor)
    p2, cursor2 = repo.list_observations_for_entity("entity-x", limit=2, cursor=cursor1)
    assert len(p2) == 2
    assert cursor2 is not None

    # Página 3 (último elemento)
    p3, cursor3 = repo.list_observations_for_entity("entity-x", limit=2, cursor=cursor2)
    assert len(p3) == 1
    assert cursor3 is None


def test_transaction_rollback(repo: SqliteEpisodicRepository):
    s = Source(id="src-atomic", name="Fuente Atómica", source_type=SourceType.CAMERA)
    repo.save_source(s)

    # Iniciar transacción
    repo.begin_transaction()
    s2 = Source(id="src-rollback", name="Debe cancelarse", source_type=SourceType.SENSOR)
    repo.save_source(s2)
    repo.rollback_transaction()

    # Verificar que src-rollback no persistió
    assert repo.get_source("src-atomic") is not None
    assert repo.get_source("src-rollback") is None


def test_outbox_state_transitions(repo: SqliteEpisodicRepository):
    rep = Representation(
        id="rep-state-1",
        target_type="episode",
        target_id="ep-any",
        space=RepresentationSpace.NOMIC_TEXT,
        model_id="nomic",
        model_revision="v1",
        dimension=768,
        indexing_state=IndexingState.PENDING,
    )
    repo.save_representation(rep)

    pending = repo.list_pending_representations()
    assert any(r.id == "rep-state-1" for r in pending)
    assert repo.get_indexing_summary() == {
        "pending": 1,
        "indexed": 0,
        "failed": 0,
        "stale": 0,
        "total": 1,
    }

    # Actualizar a INDEXED con Qdrant point ID
    repo.update_representation_state("rep-state-1", IndexingState.INDEXED, qdrant_point_id="pt-100")
    pending_after = repo.list_pending_representations()
    assert not any(r.id == "rep-state-1" for r in pending_after)
    assert repo.get_indexing_summary() == {
        "pending": 0,
        "indexed": 1,
        "failed": 0,
        "stale": 0,
        "total": 1,
    }
