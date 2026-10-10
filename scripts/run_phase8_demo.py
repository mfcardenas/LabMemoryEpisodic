"""Ejecuta las once herramientas de memoria sin agentes ni LLM."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig
from episodic.examples.phase8_tools import persist_phase8_tool_scenario


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demostracion EpiGraph Fase 8")
    parser.add_argument("--data", type=Path, default=Path(".runtime/fase8-demo"))
    parser.add_argument("--real-encoders", action="store_true")
    return parser.parse_args()


def _summary(output) -> dict:
    return {
        "result_ids": output.result_ids,
        "confidence": output.confidence,
        "counts": {
            "observations": len(output.observations),
            "episodes": len(output.episodes),
            "entities": len(output.entities),
            "relations": len(output.relations),
            "hypotheses": len(output.hypotheses),
            "evidence": len(output.evidence),
            "documents": len(output.documents),
            "environments": len(output.environments),
            "sources": len(output.sources),
            "comparisons": len(output.comparisons),
        },
        "fact_ids": [
            item.id
            for collection in (
                output.observations,
                output.episodes,
                output.entities,
                output.relations,
                output.documents,
                output.environments,
                output.sources,
            )
            for item in collection
        ],
        "hypothesis_ids": [item.id for item in output.hypotheses],
        "evidence_ids": [item.id for item in output.evidence],
        "metadata": output.metadata,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = _arguments()
    container = create_app_container(
        EpisodicConfig(data_dir=args.data),
        use_fake_encoders=not args.real_encoders,
    )
    try:
        scenario = persist_phase8_tool_scenario(container.repository)
        indexed = container.lifecycle_service.rebuild_index(container.indexing_worker)
        hypothesis_id = scenario.maintenance.identity_hypothesis.id
        calls = {
            "search_observations": {
                "query": "taladro azul",
                "entity_id": "drill_north_track",
                "validation_states": ["validated"],
            },
            "search_episodes": {
                "query": "taladro azul",
                "entity_id": "drill_north_track",
                "expand_relations": True,
                "relation_types": ["CONTRADICTS"],
            },
            "get_episode": {"episode_id": "episode_north_inventory"},
            "get_entity_timeline": {"entity_id": "drill_north_track"},
            "get_last_confirmed_observation": {
                "entity_id": "drill_north_track"
            },
            "expand_relations": {
                "node_type": "episode",
                "node_id": "episode_north_inventory",
                "relation_types": ["BEFORE", "AFTER"],
                "max_depth": 2,
            },
            "get_supporting_evidence": {
                "target_type": "hypothesis",
                "target_id": hypothesis_id,
            },
            "find_contradictions": {"entity_id": "drill_north_track"},
            "compare_entities": {
                "entity_a_id": "drill_north_track",
                "entity_b_id": "drill_south_track",
            },
            "search_documents": {"query": "numero de serie", "limit": 5},
            "get_environment_topology": {
                "environment_ids": ["workshop_north", "workshop_south"]
            },
        }
        outputs = {
            name: container.memory_tools.execute(name, payload)
            for name, payload in calls.items()
        }
        print(json.dumps({
            "profile": "real_nomic" if args.real_encoders else "fake_deterministic",
            "indexed_points": indexed,
            "tool_count": len(container.memory_tools.describe()),
            "all_read_only": all(item.read_only for item in container.memory_tools.describe()),
            "tools": {name: _summary(output) for name, output in outputs.items()},
        }, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        container.close()


if __name__ == "__main__":
    raise SystemExit(main())
