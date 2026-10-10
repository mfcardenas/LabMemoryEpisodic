"""Evalúa las consultas semánticas Atlas usando exclusivamente Nomic real."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from episodic.adapters.models.fastembed import FastEmbedEpisodeTextEncoder
from episodic.adapters.qdrant.index import QdrantVectorIndex
from episodic.adapters.qdrant_edge.index import QdrantEdgeVectorIndex
from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.application.retrieval import RetrievalService
from episodic.config import EpisodicConfig
from episodic.domain.types import SearchIntent
from episodic.evaluation import mean_defined, precision_at_k, recall_at_k


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="episodic-data", help="Directorio de datos ya importado")
    parser.add_argument("--queries", default="data/atlas/queries.json", help="Consultas etiquetadas")
    parser.add_argument("--qdrant-host", default="127.0.0.1")
    parser.add_argument("--qdrant-port", type=int, default=6333)
    parser.add_argument("--qdrant-collection", default="episodic_memory")
    parser.add_argument(
        "--vector-backend",
        choices=("edge", "http"),
        default="edge",
    )
    parser.add_argument("--limit", type=int, default=5, help="Valor k de recall@k")
    args = parser.parse_args()

    if args.limit <= 0:
        parser.error("--limit debe ser mayor que cero")

    queries_path = Path(args.queries)
    if not queries_path.is_file():
        parser.error(f"No existe el fichero de consultas: {queries_path}")

    config = EpisodicConfig(
        data_dir=args.data,
        vector_backend=args.vector_backend,
        qdrant_host=args.qdrant_host,
        qdrant_port=args.qdrant_port,
        qdrant_collection=args.qdrant_collection,
    )
    repository = SqliteEpisodicRepository(config.sqlite_path)
    if config.vector_backend == "edge":
        vector_index = QdrantEdgeVectorIndex(
            root_dir=config.qdrant_edge_dir,
            collection_name=config.qdrant_collection,
            text_dim=config.nomic_dim,
            image_dim=config.clip_dim,
        )
    else:
        vector_index = QdrantVectorIndex(
            host=config.qdrant_host,
            port=config.qdrant_port,
            collection_name=config.qdrant_collection,
            text_dim=config.nomic_dim,
            image_dim=config.clip_dim,
        )
    text_encoder = FastEmbedEpisodeTextEncoder(
        model_id=config.nomic_model,
        dimension=config.nomic_dim,
        cache_dir=config.fastembed_cache_dir,
        threads=config.encoder_threads,
    )
    retrieval = RetrievalService(
        repository=repository,
        vector_index=vector_index,
        text_encoder=text_encoder,
        aligned_text_encoder=None,
        image_encoder=None,
        default_text_threshold=config.default_text_threshold,
        default_image_threshold=config.default_image_threshold,
        rrf_k=config.rrf_k,
    )

    try:
        queries = json.loads(queries_path.read_text(encoding="utf-8"))
        evaluations = []
        for query in queries:
            if query.get("intent") != SearchIntent.SEMANTIC_SEARCH.value:
                continue

            run = retrieval.search(
                text_query=query["text_query"],
                intent=SearchIntent.SEMANTIC_SEARCH,
                require_unique_resolution=query.get("require_unique_resolution", False),
                limit=args.limit,
            )
            retrieved_ids = []
            ranked_results = []
            for result in run.results:
                episode = repository.get_episode(result.episode_id)
                if episode is not None:
                    retrieved_ids.append(episode.external_id)
                    ranked_results.append(
                        {
                            "episode": episode.external_id,
                            "score": round(result.score, 6),
                            "verdict": result.verdict.value,
                        }
                    )

            expected_ids = query.get("expected_episodes", [])
            query_recall = recall_at_k(expected_ids, retrieved_ids, args.limit)
            query_precision = precision_at_k(expected_ids, retrieved_ids, args.limit)
            evaluations.append(
                {
                    "id": query["id"],
                    "expected_episodes": expected_ids,
                    "retrieved_episodes": retrieved_ids,
                    "ranked_results": ranked_results,
                    "recall_at_k": query_recall,
                    "precision_at_k": query_precision,
                    "expected_verdict": query.get("expected_verdict"),
                    "actual_verdict": run.overall_verdict.value,
                    "verdict_match": run.overall_verdict.value == query.get("expected_verdict"),
                    "duration_ms": round(run.duration_ms, 3),
                }
            )

        report = {
            "profile": "nomic_text_only",
            "backend": f"qdrant_{config.vector_backend}",
            "collection": config.qdrant_collection,
            "k": args.limit,
            "query_count": len(evaluations),
            "macro_recall_at_k": mean_defined(item["recall_at_k"] for item in evaluations),
            "macro_precision_at_k": mean_defined(
                item["precision_at_k"] for item in evaluations
            ),
            "verdict_accuracy": (
                sum(item["verdict_match"] for item in evaluations) / len(evaluations)
                if evaluations
                else None
            ),
            "mean_duration_ms": (
                sum(item["duration_ms"] for item in evaluations) / len(evaluations)
                if evaluations
                else None
            ),
            "queries": evaluations,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        repository.close()
        if hasattr(vector_index, "close"):
            vector_index.close()


if __name__ == "__main__":
    main()
