"""Fixture persistible de dos ambientes para el caso de la pipeta rosada."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from episodic.domain.entities import Entity, Episode, Observation, Source
from episodic.domain.episodes import MicroEpisode
from episodic.domain.processing import Event, ProcessingRun, ProcessingWindow
from episodic.domain.provenance import Asset, Environment, EvidenceReference
from episodic.domain.relations import Hypothesis, Relation
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceReferenceKind,
    HypothesisState,
    ProcessingRunState,
    ProvenanceKind,
    SourceType,
    ValidationState,
)
from episodic.ports.repository import EpisodicRepository


_NAMESPACE = uuid.UUID("c892b0d2-1e24-4eed-8bd6-ded755d06963")


def _id(name: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, name))


@dataclass
class PinkPipetteFixture:
    environments: list[Environment]
    sources: list[Source]
    assets: list[Asset]
    processing_runs: list[ProcessingRun]
    processing_windows: list[ProcessingWindow]
    evidence_references: list[EvidenceReference]
    entities: list[Entity]
    observations: list[Observation]
    events: list[Event]
    microepisodes: list[MicroEpisode]
    episodes: list[Episode]
    relations: list[Relation]
    hypotheses: list[Hypothesis]

    def persist(self, repository: EpisodicRepository) -> None:
        """Persiste respetando dependencias y claves foráneas."""
        for item in self.environments:
            repository.save_environment(item)
        for item in self.sources:
            repository.save_source(item)
        for item in self.assets:
            repository.save_asset(item)
        for item in self.processing_runs:
            repository.save_processing_run(item)
        for item in self.processing_windows:
            repository.save_processing_window(item)
        for item in self.evidence_references:
            repository.save_evidence_reference(item)
        for item in self.entities:
            repository.save_entity(item)
        for item in self.observations:
            repository.save_observation(item)
        for item in self.events:
            repository.save_event(item)
        for item in self.microepisodes:
            repository.save_microepisode(item)
        for item in self.episodes:
            repository.save_episode(item)
            for observation in self.observations:
                if observation.id in item.observation_ids:
                    observation.episode_id = item.id
                    repository.save_observation(observation)
        for item in self.relations:
            repository.save_relation(item)
        for item in self.hypotheses:
            repository.save_hypothesis(item)


def build_pink_pipette_fixture() -> PinkPipetteFixture:
    """Construye el mismo grafo de objetos en cada ejecución mediante UUIDv5."""
    start = datetime(2026, 3, 18, 10, 15, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=12)

    lab = Environment(
        id=_id("environment:lab"),
        name="Laboratorio de química",
        description="Mesa de preparación y material volumétrico",
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )
    storage = Environment(
        id=_id("environment:storage"),
        name="Almacén de material",
        description="Estanterías y registro documental de inventario",
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )

    camera_lab = Source(
        id=_id("source:camera-lab"),
        environment_id=lab.id,
        name="Cámara mesa laboratorio",
        source_type=SourceType.CAMERA,
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )
    camera_storage = Source(
        id=_id("source:camera-storage"),
        environment_id=storage.id,
        name="Cámara estantería almacén",
        source_type=SourceType.CAMERA,
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )
    inventory = Source(
        id=_id("source:inventory"),
        environment_id=storage.id,
        name="Documento de inventario",
        source_type=SourceType.DOCUMENT,
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )

    lab_video = Asset(
        id=_id("asset:lab-video"),
        environment_id=lab.id,
        source_id=camera_lab.id,
        uri="fixtures/pink-pipette/lab-camera.mp4",
        media_type=EvidenceMediaType.VIDEO,
        content_hash="sha256:fixture-lab-video",
        captured_at=start,
    )
    storage_image = Asset(
        id=_id("asset:storage-image"),
        environment_id=storage.id,
        source_id=camera_storage.id,
        uri="fixtures/pink-pipette/storage-frame.png",
        media_type=EvidenceMediaType.IMAGE,
        content_hash="sha256:fixture-storage-image",
        captured_at=end,
    )
    inventory_doc = Asset(
        id=_id("asset:inventory-document"),
        environment_id=storage.id,
        source_id=inventory.id,
        uri="fixtures/pink-pipette/inventory.pdf",
        media_type=EvidenceMediaType.DOCUMENT,
        content_hash="sha256:fixture-inventory-document",
        captured_at=end,
    )

    run = ProcessingRun(
        id=_id("run:visual-review"),
        environment_id=lab.id,
        source_id=camera_lab.id,
        asset_ids=[lab_video.id],
        producer="fixture-visual-review",
        model_version="1.0",
        state=ProcessingRunState.COMPLETED,
        start_time=start,
        end_time=end,
    )
    window = ProcessingWindow(
        id=_id("window:lab-video-0"),
        processing_run_id=run.id,
        environment_id=lab.id,
        source_id=camera_lab.id,
        asset_id=lab_video.id,
        start_time=start,
        end_time=end,
        producer=run.producer,
        model_version=run.model_version,
    )

    lab_frame = EvidenceReference(
        id=_id("evidence:lab-frame"),
        asset_id=lab_video.id,
        reference_kind=EvidenceReferenceKind.FRAME,
        frame_number=240,
        start_time=start + timedelta(seconds=8),
        end_time=start + timedelta(seconds=8),
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )
    storage_region = EvidenceReference(
        id=_id("evidence:storage-region"),
        asset_id=storage_image.id,
        reference_kind=EvidenceReferenceKind.REGION,
        region={"x": 0.31, "y": 0.28, "width": 0.18, "height": 0.42},
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )
    inventory_page = EvidenceReference(
        id=_id("evidence:inventory-page"),
        asset_id=inventory_doc.id,
        reference_kind=EvidenceReferenceKind.PAGE,
        page_number=2,
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )

    pipette = Entity(
        id=_id("entity:pink-pipette"),
        label="Pipeta rosada",
        entity_type="laboratory_object",
        description="Pipeta monocanal con cuerpo rosado",
        created_by="fixture-author",
        provenance_kind=ProvenanceKind.HUMAN_DECLARATION,
        environment_ids=[lab.id, storage.id],
        producer="fixture-author",
        model_version="pink-pipette-v1",
    )

    observations = [
        Observation(
            id=_id("observation:on-lab-table"),
            content="La pipeta rosada está sobre la mesa del laboratorio.",
            provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
            entity_id=pipette.id,
            environment_id=lab.id,
            source_id=camera_lab.id,
            asset_id=lab_video.id,
            processing_run_id=run.id,
            processing_window_id=window.id,
            evidence_reference_ids=[lab_frame.id],
            start_time=start + timedelta(seconds=8),
            end_time=start + timedelta(seconds=8),
            producer=run.producer,
            model_version=run.model_version,
            confidence=0.94,
        ),
        Observation(
            id=_id("observation:on-storage-shelf"),
            content="Una pipeta rosada aparece en la estantería del almacén.",
            provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
            entity_id=pipette.id,
            environment_id=storage.id,
            source_id=camera_storage.id,
            asset_id=storage_image.id,
            evidence_reference_ids=[storage_region.id],
            start_time=end,
            end_time=end,
            producer="fixture-author",
            model_version="pink-pipette-v1",
            confidence=0.83,
        ),
        Observation(
            id=_id("observation:inventory-entry"),
            content="El inventario registra una pipeta rosada en el almacén.",
            provenance_kind=ProvenanceKind.SCENARIO_FIXTURE,
            entity_id=pipette.id,
            environment_id=storage.id,
            source_id=inventory.id,
            asset_id=inventory_doc.id,
            evidence_reference_ids=[inventory_page.id],
            start_time=end,
            end_time=end,
            producer="fixture-author",
            model_version="pink-pipette-v1",
            confidence=1.0,
        ),
    ]

    event = Event(
        id=_id("event:pipette-observed"),
        event_type="object_presence",
        description="Presencia de la pipeta rosada en la mesa",
        environment_id=lab.id,
        source_id=camera_lab.id,
        processing_run_id=run.id,
        processing_window_id=window.id,
        entity_ids=[pipette.id],
        evidence_reference_ids=[lab_frame.id],
        start_time=start + timedelta(seconds=8),
        end_time=start + timedelta(seconds=8),
        producer=run.producer,
        model_version=run.model_version,
        confidence=0.94,
    )
    micro = MicroEpisode(
        id=_id("microepisode:lab-presence"),
        title="Pipeta rosada observada en laboratorio",
        environment_id=lab.id,
        source_ids=[camera_lab.id],
        observation_ids=[observations[0].id],
        event_ids=[event.id],
        evidence_reference_ids=[lab_frame.id],
        start_time=start,
        end_time=end,
        producer="fixture-composer",
        model_version="1.0",
        confidence=0.92,
    )
    episode = Episode(
        id=_id("episode:pink-pipette-lab"),
        title="Presencia de la pipeta rosada en el laboratorio",
        environment_ids=[lab.id],
        source_ids=[camera_lab.id],
        microepisode_ids=[micro.id],
        observation_ids=[observations[0].id],
        start_time=start,
        end_time=end,
        producer="fixture-composer",
        model_version="1.0",
        confidence=0.92,
    )
    relation = Relation(
        id=_id("relation:pipette-observed-in-lab"),
        relation_type="observed_in",
        origin_type="entity",
        origin_id=pipette.id,
        destination_type="environment",
        destination_id=lab.id,
        method="human_verified_fixture",
        evidence_reference_ids=[lab_frame.id],
        validation_state=ValidationState.VALIDATED,
        producer="fixture-author",
        model_version="pink-pipette-v1",
        confidence=1.0,
    )
    hypothesis = Hypothesis(
        id=_id("hypothesis:pipette-moved-to-storage"),
        statement="La pipeta observada en el almacén podría ser la misma del laboratorio.",
        subject_type="entity",
        subject_id=pipette.id,
        object_type="environment",
        object_id=storage.id,
        evidence_reference_ids=[storage_region.id, inventory_page.id],
        state=HypothesisState.OPEN,
        producer="fixture-analyst",
        model_version="pink-pipette-v1",
        confidence=0.58,
    )

    return PinkPipetteFixture(
        environments=[lab, storage],
        sources=[camera_lab, camera_storage, inventory],
        assets=[lab_video, storage_image, inventory_doc],
        processing_runs=[run],
        processing_windows=[window],
        evidence_references=[lab_frame, storage_region, inventory_page],
        entities=[pipette],
        observations=observations,
        events=[event],
        microepisodes=[micro],
        episodes=[episode],
        relations=[relation],
        hypotheses=[hypothesis],
    )
