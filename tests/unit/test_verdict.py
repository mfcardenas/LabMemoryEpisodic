"""Tests unitarios para el sistema de veredictos explícito (Prompt §9.6-7)."""
from pathlib import Path
import pytest

from episodic.adapters.models.fake import FakeAlignedTextEncoder, FakeEpisodeTextEncoder, FakeImageEncoder
from episodic.adapters.qdrant.index import QdrantVectorIndex
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.retrieval import RetrievalService
from episodic.domain.entities import Episode
from episodic.domain.types import SearchIntent, Verdict


@pytest.fixture
def retrieval_env(tmp_path: Path):
    db_file = tmp_path / "verdict_test.db"
    repo = SqliteEpisodicRepository(db_file)
    v_index = QdrantVectorIndex(use_memory=True, collection_name="verdict_col")
    text_enc = FakeEpisodeTextEncoder()
    aligned_enc = FakeAlignedTextEncoder()
    img_enc = FakeImageEncoder()

    svc = RetrievalService(
        repository=repo,
        vector_index=v_index,
        text_encoder=text_enc,
        aligned_text_encoder=aligned_enc,
        image_encoder=img_enc,
        default_text_threshold=0.3,
    )

    yield repo, v_index, svc, text_enc
    repo.close()


def test_verdict_no_match_on_empty_index(retrieval_env):
    repo, v_index, svc, text_enc = retrieval_env
    run = svc.search(text_query="objeto inexistente en base de datos", intent=SearchIntent.SEMANTIC_SEARCH)
    assert run.overall_verdict == Verdict.NO_MATCH
    assert len(run.results) == 0


def test_verdict_supported_with_evidence(retrieval_env):
    repo, v_index, svc, text_enc = retrieval_env

    # Insertar episodio con evidencia
    ep = Episode(
        id="ep-evidence-1",
        title="Detección de dron en perímetro",
        indexable_text="Detección de dron en perímetro exterior",
        evidence_ids=["ev-dron-01"],
    )
    repo.save_episode(ep)

    # Insertar en Qdrant
    vec = text_enc.embed_documents([ep.indexable_text])[0]
    v_index.upsert("pt-dron", ep.id, {"text": vec}, {"episode_id": ep.id, "target_type": "episode", "is_visible": True})

    run = svc.search(text_query="Detección de dron en perímetro exterior", intent=SearchIntent.SEMANTIC_SEARCH)
    assert len(run.results) == 1
    assert run.results[0].verdict == Verdict.SUPPORTED
    assert run.overall_verdict == Verdict.SUPPORTED


def test_hidden_episode_is_rejected_even_if_vector_is_stale(retrieval_env):
    repo, v_index, svc, text_enc = retrieval_env
    ep = Episode(
        id="ep-hidden",
        title="Episodio que ya no es visible",
        indexable_text="vehículo rojo junto a la puerta norte",
        evidence_ids=["ev-stale"],
    )
    repo.save_episode(ep)
    vec = text_enc.embed_documents([ep.indexable_text])[0]
    v_index.upsert(
        "pt-hidden",
        ep.id,
        {"text": vec},
        {"episode_id": ep.id, "target_type": "episode", "is_visible": True},
    )

    # Simula un borrado vectorial pendiente: SQLite ya oculta el episodio,
    # pero el punto antiguo continúa visible en Qdrant.
    repo.set_episode_visibility(ep.id, False)
    run = svc.search(text_query=ep.indexable_text, intent=SearchIntent.SEMANTIC_SEARCH)

    assert run.results == []
    assert run.overall_verdict == Verdict.NO_MATCH


def test_ambiguity_requires_explicit_unique_resolution(retrieval_env):
    repo, v_index, svc, text_enc = retrieval_env
    query = "persona con mochila oscura"
    vector = text_enc.embed_documents([query])[0]
    for suffix in ("a", "b"):
        episode = Episode(
            id=f"ep-{suffix}",
            title=f"Candidato {suffix}",
            indexable_text=query,
            evidence_ids=[f"ev-{suffix}"],
        )
        repo.save_episode(episode)
        v_index.upsert(
            f"pt-{suffix}",
            episode.id,
            {"text": vector},
            {"episode_id": episode.id, "target_type": "episode", "is_visible": True},
        )

    ordinary = svc.search(text_query=query, intent=SearchIntent.SEMANTIC_SEARCH)
    unique = svc.search(
        text_query=query,
        intent=SearchIntent.SEMANTIC_SEARCH,
        require_unique_resolution=True,
    )

    assert ordinary.overall_verdict == Verdict.SUPPORTED
    assert unique.overall_verdict == Verdict.AMBIGUOUS
