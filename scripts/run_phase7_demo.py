"""Demostracion reproducible de recuperacion hibrida y sus metricas."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.domain.retrieval import HybridSearchFilters
from episodic.domain.types import ValidationState
from episodic.evaluation import (
    latest_event_accuracy,
    mean_defined,
    mean_reciprocal_rank,
    recall_at_k,
    reciprocal_rank,
    results_without_evidence,
)
from episodic.examples.phase6_maintenance import persist_phase6_maintenance_scenario


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demostracion EpiGraph Fase 7")
    parser.add_argument("--data", type=Path, default=Path(".runtime/fase7-demo"))
    parser.add_argument(
        "--real-encoders",
        action="store_true",
        help="Usar Nomic/CLIP reales; por defecto usa dobles deterministas.",
    )
    parser.add_argument("--k", type=int, default=5)
    return parser.parse_args()


def _ids(run) -> list[str]:
    return [item.episode.id for item in run.results]


def _result_view(run) -> dict:
    return {
        "latency_ms": round(run.duration_ms, 3),
        "results_without_evidence": run.results_without_evidence,
        "results": [
            {
                "episode_id": item.episode.id,
                "environment_ids": item.episode.environment_ids,
                "observation_ids": [obs.id for obs in item.observations],
                "evidence_reference_ids": [ref.id for ref in item.evidence_references],
                "relation_types": [
                    expansion.relation.relation_type
                    for expansion in item.relation_expansions
                ],
                "validated_relations": [
                    expansion.relation.id
                    for expansion in item.relation_expansions
                    if expansion.is_validated
                ],
                "scores": {
                    "structured": item.scores.structured,
                    "semantic": item.scores.semantic,
                    "temporal": item.scores.temporal,
                    "relational": item.scores.relational,
                },
                "direct": item.is_direct_match,
            }
            for item in run.results
        ],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = _arguments()
    if args.k < 1:
        raise ValueError("k debe ser mayor que cero")
    container = create_app_container(
        EpisodicConfig(data_dir=args.data),
        use_fake_encoders=not args.real_encoders,
    )
    try:
        persist_phase6_maintenance_scenario(container.repository)
        indexed = container.lifecycle_service.rebuild_index(container.indexing_worker)
        service = container.hybrid_retrieval_service

        where = service.search(
            text_query="Donde se observo el taladro azul?",
            filters=HybridSearchFilters(
                entity_ids=("drill_north_track",),
                validation_states=(ValidationState.VALIDATED,),
            ),
            limit=args.k,
        )
        latest = service.last_confirmed_observation("drill_north_track")
        before_after = service.episode_context(
            "episode_north_inventory", relation_types=("BEFORE", "AFTER"), limit=args.k
        )
        another_environment = service.search(
            filters=HybridSearchFilters(
                entity_ids=("drill_north_track",),
                validation_states=(ValidationState.VALIDATED,),
            ),
            limit=args.k,
        )
        contradictions = service.search(
            filters=HybridSearchFilters(entity_ids=("drill_north_track",)),
            expand_relations=True,
            relation_types=("CONTRADICTS",),
            limit=args.k,
        )

        queries = {
            "where_was_tool_observed": (
                where,
                {"episode_north_inspection", "episode_south_repair"},
            ),
            "latest_observation": (latest, {"episode_south_repair"}),
            "before_and_after": (
                before_after,
                {
                    "episode_north_inspection",
                    "episode_north_inventory",
                    "episode_south_repair",
                },
            ),
            "appeared_in_another_environment": (
                another_environment,
                {"episode_north_inspection", "episode_south_repair"},
            ),
            "contradictory_observations": (
                contradictions,
                {"episode_south_repair", "episode_south_second_angle"},
            ),
        }
        recalls = [
            recall_at_k(expected, _ids(run), args.k)
            for run, expected in queries.values()
        ]
        reciprocal_ranks = [
            reciprocal_rank(expected, _ids(run))
            for run, expected in queries.values()
        ]
        latest_observation_id = (
            latest.results[0].observations[0].id
            if latest.results and latest.results[0].observations
            else None
        )
        all_results = [
            item
            for run, _ in queries.values()
            for item in run.results
        ]
        output = {
            "profile": "real_nomic" if args.real_encoders else "fake_deterministic",
            "indexed_points": indexed,
            "queries": {
                name: _result_view(run) for name, (run, _) in queries.items()
            },
            "metrics": {
                f"recall_at_{args.k}": mean_defined(recalls),
                "mrr": mean_reciprocal_rank(reciprocal_ranks),
                "mean_latency_ms": mean_defined(
                    run.duration_ms for run, _ in queries.values()
                ),
                "results_without_evidence": results_without_evidence(
                    item.has_evidence for item in all_results
                ),
                "latest_event_accuracy": latest_event_accuracy(
                    "observation_drill_south", latest_observation_id
                ),
            },
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        container.close()


if __name__ == "__main__":
    raise SystemExit(main())
