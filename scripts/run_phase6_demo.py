"""Demostración del grafo lógico entre dos talleres."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from episodic.bootstrap import create_ingestion_container
from episodic.config import EpisodicConfig
from episodic.examples.phase6_maintenance import (
    persist_phase6_maintenance_scenario,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demostración EpiGraph Fase 6")
    parser.add_argument("--data", type=Path, default=Path(".runtime/fase6-demo"))
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = _arguments()
    container = create_ingestion_container(EpisodicConfig(data_dir=args.data))
    try:
        scenario = persist_phase6_maintenance_scenario(container.repository)
        graph = container.logical_graph_service
        entity_observations = graph.observations_for_entity("drill_north_track")
        episode_relations = graph.relations_for_episode("episode_north_inventory")
        traversal = graph.traverse_episodes("episode_north_inventory")
        contradictions = graph.contradictions()
        last = graph.last_confirmed_observation("drill_north_track")
        output = {
            "environments": [item.id for item in scenario.environments],
            "entities": [item.id for item in scenario.entities],
            "possible_identity_hypothesis": scenario.identity_hypothesis.to_dict(),
            "validated_relation_types": [item.relation_type for item in scenario.relations],
            "entity_observations": [item.to_dict() for item in entity_observations],
            "episode_relations": [item.to_dict() for item in episode_relations],
            "episode_traversal": {
                "previous": [item.episode.id for item in traversal.previous],
                "following": [item.episode.id for item in traversal.following],
                "overlapping": [item.episode.id for item in traversal.overlapping],
                "gaps": [
                    item.observation_gap
                    for item in (*traversal.previous, *traversal.following)
                    if item.observation_gap is not None
                ],
            },
            "contradictions": [
                {
                    "relation": item.to_dict(),
                    "evidence": [
                        reference.to_dict()
                        for reference in graph.evidence_for_relation(item.id)
                    ],
                }
                for item in contradictions
            ],
            "last_confirmed_observation": (
                {
                    "observation": last.observation.to_dict(),
                    "evidence": [item.to_dict() for item in last.evidence_references],
                    "contradictions": [item.to_dict() for item in last.contradictions],
                }
                if last is not None
                else None
            ),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        container.close()


if __name__ == "__main__":
    raise SystemExit(main())
