"""Datos controlados para verificar las reglas episódicas de Fase 5."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from episodic.domain.entities import Entity, Observation, Source
from episodic.domain.processing import ProcessingRun, ProcessingWindow
from episodic.domain.provenance import Asset, Environment, EvidenceReference
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceReferenceKind,
    ProcessingRunState,
    ProvenanceKind,
    SourceType,
)
from episodic.ports.repository import EpisodicRepository


@dataclass(frozen=True)
class Phase5Scenario:
    environment: Environment
    sources: tuple[Source, ...]
    assets: tuple[Asset, ...]
    runs: tuple[ProcessingRun, ...]
    windows: tuple[ProcessingWindow, ...]
    entities: tuple[Entity, ...]
    evidence_references: tuple[EvidenceReference, ...]
    observations: tuple[Observation, ...]

    def persist(self, repository: EpisodicRepository) -> None:
        repository.save_environment(self.environment)
        for source in self.sources:
            repository.save_source(source)
        for asset in self.assets:
            repository.save_asset(asset)
        for run in self.runs:
            repository.save_processing_run(run)
        for window in self.windows:
            repository.save_processing_window(window)
        for entity in self.entities:
            repository.save_entity(entity)
        for reference in self.evidence_references:
            repository.save_evidence_reference(reference)
        for observation in self.observations:
            repository.save_observation(observation)


def build_phase5_scenario(
    path: str | Path,
    *,
    window_sequence_numbers: dict[str, int] | None = None,
) -> Phase5Scenario:
    """Construye los cinco casos obligatorios desde un JSON legible."""
    with Path(path).open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    environment_id = payload["environment_id"]
    base = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)
    created_at = base - timedelta(minutes=1)
    environment = Environment(
        id=environment_id,
        name="Laboratorio de pruebas Fase 5",
        producer="phase5-fixture",
        model_version="1",
        created_at=created_at,
    )

    source_specs = {
        "camera_A": (SourceType.CAMERA, EvidenceMediaType.VIDEO, "video/mp4"),
        "camera_B": (SourceType.CAMERA, EvidenceMediaType.VIDEO, "video/mp4"),
        "microphone_A": (SourceType.MICROPHONE, EvidenceMediaType.AUDIO, "audio/mpeg"),
    }
    sources = tuple(
        Source(
            id=source_id,
            environment_id=environment_id,
            name=source_id,
            source_type=spec[0],
            producer="phase5-fixture",
            model_version="1",
            created_at=created_at,
        )
        for source_id, spec in source_specs.items()
    )
    assets = tuple(
        Asset(
            id=f"asset-{source_id}",
            environment_id=environment_id,
            source_id=source_id,
            uri=f"phase5://{source_id}",
            media_type=spec[1],
            mime_type=spec[2],
            content_hash=f"sha256:phase5-{source_id}",
            duration_seconds=20.0,
            captured_at=base,
            producer="phase5-fixture",
            model_version="1",
            created_at=created_at,
        )
        for source_id, spec in source_specs.items()
    )
    runs = tuple(
        ProcessingRun(
            id=f"run-{source.id}",
            environment_id=environment_id,
            source_id=source.id,
            asset_ids=[f"asset-{source.id}"],
            producer="phase5-fixture",
            model_version="1",
            state=ProcessingRunState.COMPLETED,
            start_time=base,
            end_time=base + timedelta(seconds=20),
            created_at=created_at,
        )
        for source in sources
    )
    window_specs = {
        "camera_A_window_1": ("camera_A", 0.0, 5.0, 90),
        "camera_A_window_2": ("camera_A", 4.0, 12.0, 10),
        "camera_A_window_3": ("camera_A", 12.0, 20.0, 70),
        "camera_B_window_1": ("camera_B", 0.0, 20.0, 3),
        "microphone_A_window_1": ("microphone_A", 0.0, 20.0, 44),
    }
    sequence_numbers = window_sequence_numbers or {}
    windows = tuple(
        ProcessingWindow(
            id=window_id,
            processing_run_id=f"run-{source_id}",
            environment_id=environment_id,
            source_id=source_id,
            asset_id=f"asset-{source_id}",
            start_time=base + timedelta(seconds=start_offset),
            end_time=base + timedelta(seconds=end_offset),
            sequence_number=sequence_numbers.get(window_id, default_sequence),
            start_offset_seconds=start_offset,
            end_offset_seconds=end_offset,
            delta_time_seconds=end_offset - start_offset,
            overlap_seconds=1.0 if window_id == "camera_A_window_2" else 0.0,
            producer="phase5-fixture",
            model_version="1",
            created_at=created_at,
        )
        for window_id, (source_id, start_offset, end_offset, default_sequence) in window_specs.items()
    )

    entity_ids = sorted({item["entity_id"] for item in payload["observations"]})
    entities = tuple(
        Entity(
            id=entity_id,
            label=entity_id.replace("_", " "),
            entity_type="test_entity",
            created_by="phase5-fixture",
            provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
            environment_ids=[environment_id],
            producer="phase5-fixture",
            model_version="1",
            created_at=created_at,
        )
        for entity_id in entity_ids
    )

    references: list[EvidenceReference] = []
    observations: list[Observation] = []
    control_keys = {
        "id", "source_id", "window", "start_offset", "end_offset",
        "entity_id", "description", "evidence", "modality",
    }
    for item in payload["observations"]:
        start_time = base + timedelta(seconds=float(item["start_offset"]))
        end_time = base + timedelta(seconds=float(item["end_offset"]))
        evidence_ids: list[str] = []
        if item["evidence"]:
            reference = EvidenceReference(
                id=f"evidence-{item['id']}",
                asset_id=f"asset-{item['source_id']}",
                reference_kind=EvidenceReferenceKind.AUDIOVISUAL_INTERVAL,
                start_time=start_time,
                end_time=end_time,
                start_offset_seconds=float(item["start_offset"]),
                end_offset_seconds=float(item["end_offset"]),
                producer="phase5-fixture",
                model_version="1",
                confidence=1.0,
                created_at=start_time,
            )
            references.append(reference)
            evidence_ids.append(reference.id)
        attributes: dict[str, Any] = {
            key: value for key, value in item.items() if key not in control_keys
        }
        observations.append(
            Observation(
                id=item["id"],
                content=item["description"],
                description=item["description"],
                modality=item.get("modality", "video"),
                observation_type="phase5_action_observation",
                attributes=attributes,
                provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
                author="phase5-fixture",
                tool_version="1",
                confidence=0.9,
                entity_id=item["entity_id"],
                environment_id=environment_id,
                source_id=item["source_id"],
                asset_id=f"asset-{item['source_id']}",
                processing_run_id=f"run-{item['source_id']}",
                processing_window_id=item["window"],
                evidence_reference_ids=evidence_ids,
                start_time=start_time,
                end_time=end_time,
                producer="phase5-fixture",
                model_version="1",
                created_at=start_time,
            )
        )

    return Phase5Scenario(
        environment=environment,
        sources=sources,
        assets=assets,
        runs=runs,
        windows=windows,
        entities=entities,
        evidence_references=tuple(references),
        observations=tuple(observations),
    )
