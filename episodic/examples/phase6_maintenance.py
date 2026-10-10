"""Escenario reproducible de herramientas y mantenimiento para Fase 6."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from episodic.application.logical_graph import LogicalGraphService
from episodic.domain.entities import Entity, Episode, Observation, Source
from episodic.domain.provenance import Asset, Environment, EvidenceReference
from episodic.domain.relations import Hypothesis, Relation
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceReferenceKind,
    LogicalRelationType,
    ProvenanceKind,
    SourceType,
    TransitionMode,
    ValidationState,
)
from episodic.ports.repository import EpisodicRepository


@dataclass(frozen=True)
class Phase6MaintenanceScenario:
    environments: tuple[Environment, ...]
    sources: tuple[Source, ...]
    assets: tuple[Asset, ...]
    entities: tuple[Entity, ...]
    episodes: tuple[Episode, ...]
    evidence_references: tuple[EvidenceReference, ...]
    observations: tuple[Observation, ...]
    identity_hypothesis: Hypothesis
    relations: tuple[Relation, ...]


def persist_phase6_maintenance_scenario(
    repository: EpisodicRepository,
) -> Phase6MaintenanceScenario:
    """Persiste el caso y construye los doce vínculos del vocabulario inicial."""
    base = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)
    created = base - timedelta(minutes=5)
    north = Environment(
        id="workshop_north",
        name="Taller norte",
        producer="phase6-fixture",
        model_version="1",
        created_at=created,
    )
    south = Environment(
        id="workshop_south",
        name="Taller sur",
        producer="phase6-fixture",
        model_version="1",
        created_at=created,
    )
    environments = (north, south)
    sources = (
        Source(
            id="camera_north",
            environment_id=north.id,
            name="Cámara taller norte",
            source_type=SourceType.CAMERA,
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        ),
        Source(
            id="camera_south_a",
            environment_id=south.id,
            name="Cámara A taller sur",
            source_type=SourceType.CAMERA,
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        ),
        Source(
            id="camera_south_b",
            environment_id=south.id,
            name="Cámara B taller sur",
            source_type=SourceType.CAMERA,
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        ),
        Source(
            id="maintenance_log",
            environment_id=north.id,
            name="Registro de mantenimiento",
            source_type=SourceType.DOCUMENT,
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        ),
    )
    media_by_source = {
        "camera_north": (EvidenceMediaType.VIDEO, "video/mp4"),
        "camera_south_a": (EvidenceMediaType.VIDEO, "video/mp4"),
        "camera_south_b": (EvidenceMediaType.VIDEO, "video/mp4"),
        "maintenance_log": (EvidenceMediaType.DOCUMENT, "application/pdf"),
    }
    assets = tuple(
        Asset(
            id=f"asset-{source.id}",
            environment_id=source.environment_id,
            source_id=source.id,
            uri=f"phase6://{source.id}",
            media_type=media_by_source[source.id][0],
            mime_type=media_by_source[source.id][1],
            content_hash=f"sha256:phase6-{source.id}",
            captured_at=base,
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        )
        for source in sources
    )
    entities = (
        Entity(
            id="drill_north_track",
            label="Taladro azul (taller norte)",
            entity_type="tool",
            created_by="phase6-fixture",
            provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
            environment_ids=[north.id],
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        ),
        Entity(
            id="drill_south_track",
            label="Taladro azul (taller sur)",
            entity_type="tool",
            created_by="phase6-fixture",
            provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
            environment_ids=[south.id],
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        ),
        Entity(
            id="torque_wrench",
            label="Llave dinamométrica",
            entity_type="tool",
            created_by="phase6-fixture",
            provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
            environment_ids=[north.id],
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        ),
        Entity(
            id="reviewer_ana",
            label="Ana, responsable de mantenimiento",
            entity_type="person",
            created_by="phase6-fixture",
            provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
            environment_ids=[north.id, south.id],
            producer="phase6-fixture",
            model_version="1",
            created_at=created,
        ),
    )
    episodes = (
        Episode(
            id="episode_north_inspection",
            title="Inspección inicial de herramientas",
            source_ids=["camera_north"],
            location=north.id,
            environment_ids=[north.id],
            indexable_text="Taladro azul observado en el banco del taller norte.",
            start_time=base,
            end_time=base + timedelta(minutes=10),
            producer="phase6-fixture",
            model_version="1",
            created_at=base,
            metadata={"episode_type": "inspection"},
        ),
        Episode(
            id="episode_north_inventory",
            title="Inventario previo a mantenimiento",
            source_ids=["camera_north"],
            location=north.id,
            environment_ids=[north.id],
            indexable_text="Inventario de llave dinamometrica en el carro de herramientas.",
            start_time=base + timedelta(minutes=30),
            end_time=base + timedelta(minutes=35),
            producer="phase6-fixture",
            model_version="1",
            created_at=base + timedelta(minutes=30),
            metadata={"episode_type": "inventory"},
        ),
        Episode(
            id="episode_south_repair",
            title="Reparación en taller sur",
            source_ids=["camera_south_a"],
            location=south.id,
            environment_ids=[south.id],
            indexable_text="Taladro azul durante la reparacion en el taller sur.",
            start_time=base + timedelta(hours=1),
            end_time=base + timedelta(hours=1, minutes=20),
            producer="phase6-fixture",
            model_version="1",
            created_at=base + timedelta(hours=1),
            metadata={"episode_type": "maintenance"},
        ),
        Episode(
            id="episode_south_second_angle",
            title="Segundo ángulo de la reparación",
            source_ids=["camera_south_b"],
            location=south.id,
            environment_ids=[south.id],
            indexable_text="Segundo angulo de la reparacion sin taladro visible.",
            start_time=base + timedelta(hours=1, minutes=5),
            end_time=base + timedelta(hours=1, minutes=15),
            producer="phase6-fixture",
            model_version="1",
            created_at=base + timedelta(hours=1, minutes=5),
            metadata={"episode_type": "maintenance"},
        ),
    )

    evidence_references = (
        _interval_reference("evidence_drill_north", assets[0], base + timedelta(minutes=5), 300),
        _interval_reference("evidence_wrench_north", assets[0], base + timedelta(minutes=32), 1920),
        _interval_reference("evidence_drill_south", assets[1], base + timedelta(hours=1, minutes=5), 3900),
        _interval_reference("evidence_drill_absent", assets[2], base + timedelta(hours=1, minutes=6), 3960),
        EvidenceReference(
            id="evidence_review_record",
            asset_id="asset-maintenance_log",
            reference_kind=EvidenceReferenceKind.PAGE,
            page_number=2,
            producer="phase6-fixture",
            model_version="1",
            confidence=1.0,
            created_at=base + timedelta(hours=1, minutes=30),
        ),
    )
    observations = (
        _observation(
            "observation_drill_north",
            "El taladro azul está en el banco del taller norte.",
            entities[0].id,
            episodes[0].id,
            north.id,
            sources[0].id,
            assets[0].id,
            evidence_references[0].id,
            base + timedelta(minutes=5),
            ValidationState.VALIDATED,
        ),
        _observation(
            "observation_wrench_north",
            "La llave dinamométrica está en el carro de herramientas.",
            entities[2].id,
            episodes[1].id,
            north.id,
            sources[0].id,
            assets[0].id,
            evidence_references[1].id,
            base + timedelta(minutes=32),
            ValidationState.VALIDATED,
        ),
        _observation(
            "observation_drill_south",
            "Un taladro azul aparece durante la reparación del taller sur.",
            entities[1].id,
            episodes[2].id,
            south.id,
            sources[1].id,
            assets[1].id,
            evidence_references[2].id,
            base + timedelta(hours=1, minutes=5),
            ValidationState.VALIDATED,
        ),
        _observation(
            "observation_drill_absent",
            "Desde el segundo ángulo no se observa ningún taladro azul.",
            entities[1].id,
            episodes[3].id,
            south.id,
            sources[2].id,
            assets[2].id,
            evidence_references[3].id,
            base + timedelta(hours=1, minutes=6),
            ValidationState.PROPOSED,
        ),
    )

    for environment in environments:
        repository.save_environment(environment)
    for source in sources:
        repository.save_source(source)
    for asset in assets:
        repository.save_asset(asset)
    for entity in entities:
        repository.save_entity(entity)
    for episode in episodes:
        repository.save_episode(episode)
    for reference in evidence_references:
        repository.save_evidence_reference(reference)
    for observation in observations:
        repository.save_observation(observation)

    graph = LogicalGraphService(repository)
    hypothesis = graph.propose_possible_same_entity(
        entities[0].id,
        entities[1].id,
        method="color-shape-and-temporal-candidate",
        evidence_reference_ids=[evidence_references[0].id, evidence_references[2].id],
        confidence=0.72,
        created_at=base + timedelta(hours=1, minutes=10),
    )
    same_entity = graph.validate_same_entity(
        entities[0].id,
        entities[1].id,
        method="human-review-of-serial-and-images",
        evidence_reference_ids=[
            evidence_references[0].id,
            evidence_references[2].id,
            evidence_references[4].id,
        ],
        hypothesis_id=hypothesis.id,
        reviewer_entity_id=entities[3].id,
        confidence=1.0,
        created_at=base + timedelta(hours=1, minutes=30),
    )
    observed_in = graph.create_relation(
        LogicalRelationType.OBSERVED_IN,
        origin_type="entity",
        origin_id=entities[0].id,
        destination_type="environment",
        destination_id=north.id,
        method="validated-visual-observation",
        evidence_reference_ids=[evidence_references[0].id],
        validation_state=ValidationState.VALIDATED,
        confidence=0.98,
        created_at=base + timedelta(minutes=5),
    )
    located_in = graph.create_relation(
        LogicalRelationType.LOCATED_IN,
        origin_type="entity",
        origin_id=entities[2].id,
        destination_type="environment",
        destination_id=north.id,
        method="validated-visual-observation",
        evidence_reference_ids=[evidence_references[1].id],
        validation_state=ValidationState.VALIDATED,
        confidence=0.97,
        created_at=base + timedelta(minutes=32),
    )
    before = graph.create_relation(
        LogicalRelationType.BEFORE,
        origin_type="episode",
        origin_id=episodes[0].id,
        destination_type="episode",
        destination_id=episodes[1].id,
        method="deterministic-timeline",
        evidence_reference_ids=[evidence_references[0].id, evidence_references[1].id],
        validation_state=ValidationState.VALIDATED,
        derivation="inferred",
        observation_gap={
            "start_time": episodes[0].end_time.isoformat(),
            "end_time": episodes[1].start_time.isoformat(),
            "duration_seconds": 1200.0,
            "reason": "sin observaciones entre inspección e inventario",
        },
        created_at=base + timedelta(minutes=35),
    )
    after = graph.create_relation(
        LogicalRelationType.AFTER,
        origin_type="episode",
        origin_id=episodes[2].id,
        destination_type="episode",
        destination_id=episodes[1].id,
        method="deterministic-timeline",
        evidence_reference_ids=[evidence_references[1].id, evidence_references[2].id],
        validation_state=ValidationState.VALIDATED,
        derivation="inferred",
        created_at=base + timedelta(hours=1, minutes=20),
    )
    overlaps = graph.create_relation(
        LogicalRelationType.OVERLAPS,
        origin_type="episode",
        origin_id=episodes[2].id,
        destination_type="episode",
        destination_id=episodes[3].id,
        method="interval-overlap",
        evidence_reference_ids=[evidence_references[2].id, evidence_references[3].id],
        validation_state=ValidationState.VALIDATED,
        derivation="inferred",
        created_at=base + timedelta(hours=1, minutes=15),
    )
    moved = graph.create_relation(
        LogicalRelationType.MOVED_TO,
        origin_type="entity",
        origin_id=entities[0].id,
        destination_type="environment",
        destination_id=south.id,
        method="last-north-first-south-inference",
        evidence_reference_ids=[evidence_references[0].id, evidence_references[2].id],
        validation_state=ValidationState.PROPOSED,
        transition_mode=TransitionMode.INFERRED,
        derivation="inferred",
        confidence=0.75,
        metadata={"from_environment_id": north.id},
        created_at=base + timedelta(hours=1, minutes=11),
    )
    supports = graph.create_relation(
        LogicalRelationType.SUPPORTS,
        origin_type="evidence_reference",
        origin_id=evidence_references[4].id,
        destination_type="hypothesis",
        destination_id=hypothesis.id,
        method="review-record-support",
        evidence_reference_ids=[evidence_references[4].id],
        validation_state=ValidationState.VALIDATED,
        created_at=base + timedelta(hours=1, minutes=30),
    )
    contradicts = graph.create_relation(
        LogicalRelationType.CONTRADICTS,
        origin_type="observation",
        origin_id=observations[2].id,
        destination_type="observation",
        destination_id=observations[3].id,
        method="simultaneous-view-comparison",
        evidence_reference_ids=[evidence_references[2].id, evidence_references[3].id],
        validation_state=ValidationState.VALIDATED,
        confidence=1.0,
        derivation="inferred",
        created_at=base + timedelta(hours=1, minutes=7),
    )
    derived_from = graph.create_relation(
        LogicalRelationType.DERIVED_FROM,
        origin_type="relation",
        origin_id=moved.id,
        destination_type="hypothesis",
        destination_id=hypothesis.id,
        method="identity-supported-spatial-inference",
        evidence_reference_ids=[evidence_references[0].id, evidence_references[2].id],
        validation_state=ValidationState.PROPOSED,
        derivation="inferred",
        created_at=base + timedelta(hours=1, minutes=12),
    )
    reviewed_by = graph.create_relation(
        LogicalRelationType.REVIEWED_BY,
        origin_type="relation",
        origin_id=same_entity.id,
        destination_type="entity",
        destination_id=entities[3].id,
        method="signed-maintenance-review",
        evidence_reference_ids=[evidence_references[4].id],
        validation_state=ValidationState.VALIDATED,
        created_at=base + timedelta(hours=1, minutes=30),
    )
    relations = (
        observed_in,
        before,
        after,
        overlaps,
        located_in,
        same_entity,
        moved,
        supports,
        contradicts,
        derived_from,
        reviewed_by,
    )
    return Phase6MaintenanceScenario(
        environments=environments,
        sources=sources,
        assets=assets,
        entities=entities,
        episodes=episodes,
        evidence_references=evidence_references,
        observations=observations,
        identity_hypothesis=repository.get_hypothesis(hypothesis.id),  # type: ignore[arg-type]
        relations=relations,
    )


def _interval_reference(
    reference_id: str, asset: Asset, instant: datetime, offset: float
) -> EvidenceReference:
    return EvidenceReference(
        id=reference_id,
        asset_id=asset.id,
        reference_kind=EvidenceReferenceKind.AUDIOVISUAL_INTERVAL,
        start_time=instant,
        end_time=instant + timedelta(seconds=1),
        start_offset_seconds=offset,
        end_offset_seconds=offset + 1,
        producer="phase6-fixture",
        model_version="1",
        confidence=1.0,
        created_at=instant,
    )


def _observation(
    observation_id: str,
    description: str,
    entity_id: str,
    episode_id: str,
    environment_id: str,
    source_id: str,
    asset_id: str,
    evidence_reference_id: str,
    instant: datetime,
    validation_state: ValidationState,
) -> Observation:
    return Observation(
        id=observation_id,
        episode_id=episode_id,
        content=description,
        description=description,
        modality="video",
        observation_type="tool_presence",
        validation_state=validation_state,
        attributes={"action": "observe_tool"},
        provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
        author="phase6-fixture",
        tool_version="1",
        confidence=0.95,
        entity_id=entity_id,
        environment_id=environment_id,
        source_id=source_id,
        asset_id=asset_id,
        evidence_reference_ids=[evidence_reference_id],
        start_time=instant,
        end_time=instant + timedelta(seconds=1),
        producer="phase6-fixture",
        model_version="1",
        created_at=instant,
    )
