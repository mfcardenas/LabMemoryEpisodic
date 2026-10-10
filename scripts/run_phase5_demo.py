"""Carga los escenarios de Fase 5 y ejecuta la composición determinista."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from episodic.application.episode_composition import (
    DeterministicEpisodeComposer,
    EpisodeRuleConfig,
)
from episodic.bootstrap import create_ingestion_container
from episodic.config import EpisodicConfig
from episodic.examples.phase5_scenarios import build_phase5_scenario


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demostración EpiGraph Fase 5")
    parser.add_argument(
        "--observations",
        type=Path,
        default=Path("data_samples/phase5_observations.json"),
    )
    parser.add_argument(
        "--rules", type=Path, default=Path("data_samples/phase5_rules.json")
    )
    parser.add_argument("--data", type=Path, default=Path(".runtime/fase5-demo"))
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = _arguments()
    scenario = build_phase5_scenario(args.observations)
    rules = EpisodeRuleConfig.from_json_file(args.rules)
    container = create_ingestion_container(EpisodicConfig(data_dir=args.data))
    try:
        scenario.persist(container.repository)
        result = DeterministicEpisodeComposer(container.repository, rules).compose(
            scenario.environment.id
        )
        payload = {
            "environment_id": scenario.environment.id,
            "observation_count": result.observation_count,
            "microepisodes": [
                {
                    "id": item.id,
                    "start_time": item.start_time.isoformat(),
                    "end_time": item.end_time.isoformat(),
                    "observation_ids": item.observation_ids,
                    "processing_window_ids": item.metadata["processing_window_ids"],
                    "evidence_reference_ids": item.evidence_reference_ids,
                    "grouping_decisions": item.metadata["grouping_decisions"],
                    "termination_rules": item.metadata["termination_rules"],
                    "contains_contradiction": item.metadata["contains_contradiction"],
                }
                for item in result.microepisodes
            ],
            "episodes": [
                {
                    "id": item.id,
                    "microepisode_ids": item.microepisode_ids,
                    "observation_ids": item.observation_ids,
                    "aggregation_decisions": item.metadata["aggregation_decisions"],
                    "evidence_reference_ids": item.metadata["evidence_reference_ids"],
                }
                for item in result.episodes
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        container.close()


if __name__ == "__main__":
    raise SystemExit(main())
