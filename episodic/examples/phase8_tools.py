"""Corpus minimo para ejecutar las once herramientas de Fase 8."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from episodic.domain.entities import Observation
from episodic.domain.types import ProvenanceKind, ValidationState
from episodic.examples.phase6_maintenance import (
    Phase6MaintenanceScenario,
    persist_phase6_maintenance_scenario,
)
from episodic.ports.repository import EpisodicRepository


@dataclass(frozen=True)
class Phase8ToolScenario:
    maintenance: Phase6MaintenanceScenario
    document_observation: Observation


def persist_phase8_tool_scenario(
    repository: EpisodicRepository,
) -> Phase8ToolScenario:
    maintenance = persist_phase6_maintenance_scenario(repository)
    instant = datetime(2026, 10, 6, 9, 33, tzinfo=timezone.utc)
    document_observation = Observation(
        id="observation_maintenance_document",
        episode_id="episode_north_inventory",
        content=(
            "El registro de mantenimiento confirma el numero de serie del "
            "taladro azul y la revision firmada por Ana."
        ),
        description=(
            "Registro documental del numero de serie del taladro azul, "
            "pagina 2."
        ),
        modality="document",
        observation_type="maintenance_record",
        validation_state=ValidationState.VALIDATED,
        attributes={
            "document_kind": "maintenance_log",
            "mentions": ["taladro azul", "numero de serie", "Ana"],
        },
        provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
        author="phase8-fixture",
        tool_version="1",
        confidence=1.0,
        environment_id="workshop_north",
        source_id="maintenance_log",
        asset_id="asset-maintenance_log",
        evidence_reference_ids=["evidence_review_record"],
        start_time=instant,
        end_time=instant + timedelta(seconds=1),
        producer="phase8-fixture",
        model_version="1",
        created_at=instant,
    )
    repository.save_observation(document_observation)
    return Phase8ToolScenario(maintenance, document_observation)
