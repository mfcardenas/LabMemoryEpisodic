"""Test de integración E2E del sistema de memoria episódica."""
from pathlib import Path
import sqlite3
import pytest

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.types import SearchIntent, Verdict


def test_full_episodic_workflow_e2e(tmp_path: Path):
    manifest_file = Path("data/atlas/manifest.json")
    assert manifest_file.exists(), "El manifiesto data/atlas/manifest.json debe existir"

    config = EpisodicConfig(
        data_dir=str(tmp_path / "e2e_data"),
        qdrant_collection="e2e_test_col",
        default_text_threshold=0.0,
    )

    # 1. Inicialización con in-memory Qdrant y fake encoders deterministas
    container = create_app_container(config, use_fake_encoders=True, use_memory_qdrant=True)
    try:
        # 2. Ingesta del manifiesto Atlas Security
        stats = container.ingest_service.ingest_manifest(manifest_file)
        assert stats["episodes"] == 10
        assert stats["sources"] == 8
        assert stats["representations_queued"] >= 10

        # 3. Indexación del outbox hacia Qdrant
        total_indexed = 0
        while True:
            batch = container.indexing_worker.process_pending_outbox(batch_size=20)
            if batch == 0:
                break
            total_indexed += batch

        assert total_indexed > 0
        assert container.vector_index.count() >= 10

        # 4. Búsqueda semántica multimodal
        run = container.retrieval_service.search(
            text_query="individuo con chaqueta azul marino y mochila con franja reflectante",
            intent=SearchIntent.SEMANTIC_SEARCH,
            limit=5,
        )
        assert len(run.results) > 0
        assert run.overall_verdict in (Verdict.SUPPORTED, Verdict.CANDIDATE, Verdict.AMBIGUOUS)

        # 5. Comprobación de evidencia asociada
        top_result = run.results[0]
        assert top_result.episode_id != ""

        # 6. Enriquecimiento con anotación versionada
        ann1 = container.lifecycle_service.add_annotation(
            episode_id=top_result.episode_id,
            content="Anotación 1 de verificación forense",
            author="perito_seguridad",
        )
        assert ann1.revision_number == 1
        assert ann1.supersedes_id is None

        ann2 = container.lifecycle_service.add_annotation(
            episode_id=top_result.episode_id,
            content="Anotación 2 con ampliación pericial",
            author="perito_seguridad",
        )
        assert ann2.revision_number == 2
        assert ann2.supersedes_id == ann1.id

        # 7. Reconstrucción completa del índice
        db_path = Path(config.sqlite_path)
        with sqlite3.connect(db_path) as db:
            reps_before = db.execute("SELECT COUNT(*) FROM representations").fetchone()[0]
        rebuilt = container.lifecycle_service.rebuild_index(container.indexing_worker)
        assert rebuilt >= 10
        assert container.vector_index.count() >= 10
        with sqlite3.connect(db_path) as db:
            reps_after = db.execute("SELECT COUNT(*) FROM representations").fetchone()[0]
        assert reps_after == reps_before

        # 8. Eliminación canónica y desaparición de búsqueda
        ep_to_delete = top_result.episode_id
        container.lifecycle_service.delete_episode(ep_to_delete)

        # Verificar que ya no aparece como visible en SQLite
        ep_check = container.repository.get_episode(ep_to_delete)
        assert ep_check.is_visible is False

    finally:
        container.close()
