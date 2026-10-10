"""Derivación de eventos a partir de observaciones de tramos de vídeo.

En la Fase C un evento corresponde a un tramo descrito por el modelo de
visión. La fusión con audio y documentos (Fase E) enriquecerá su descripción
sin cambiar su identidad.
"""
from __future__ import annotations

import uuid
from typing import Sequence

from episodic.application.episode_composition import latest_processor_outputs
from episodic.domain.entities import Observation
from episodic.domain.processing import Event
from episodic.ports.repository import EpisodicRepository
from episodic.processors.video_events import VIDEO_EVENT_OBSERVATION

_EVENT_NAMESPACE = uuid.UUID("2f6b8c1e-4d7a-4b52-9c0e-7a3d5e1f9b20")
EVENT_PRODUCER = "video-event-derivation"
EVENT_MODEL_VERSION = "events-v1"


class EventDerivationService:
    def __init__(self, repository: EpisodicRepository) -> None:
        self.repository = repository

    def derive_for_assets(self, asset_ids: Sequence[str]) -> list[Event]:
        events: list[Event] = []
        for asset_id in asset_ids:
            observations = sorted(
                (
                    item
                    for item in latest_processor_outputs(
                        self.repository.list_observations_for_asset(asset_id)
                    )
                    if item.observation_type == VIDEO_EVENT_OBSERVATION
                ),
                key=lambda item: (item.start_time, item.attributes.get("segment_index", 0)),
            )
            for observation in observations:
                event = event_from_observation(observation)
                self.repository.save_event(event)
                events.append(event)
        return events


def event_from_observation(observation: Observation) -> Event:
    attributes = observation.attributes
    description = attributes.get("description", {})
    return Event(
        # Estable por observación: reprocesar actualiza el mismo evento.
        id=str(uuid.uuid5(_EVENT_NAMESPACE, observation.id)),
        event_type="video_segment",
        description=description.get("detailed_description") or observation.description,
        environment_id=observation.environment_id or "",
        source_id=observation.source_id,
        processing_run_id=observation.processing_run_id,
        processing_window_id=observation.processing_window_id,
        evidence_reference_ids=list(observation.evidence_reference_ids),
        start_time=observation.start_time,
        end_time=observation.end_time,
        producer=EVENT_PRODUCER,
        model_version=EVENT_MODEL_VERSION,
        confidence=observation.confidence,
        asset_id=observation.asset_id,
        sequence_number=int(attributes.get("segment_index", 0)),
        start_offset_seconds=attributes.get("start_offset_seconds"),
        end_offset_seconds=attributes.get("end_offset_seconds"),
        title=description.get("title", ""),
        summary=description.get("summary", ""),
        attributes={
            key: description.get(key)
            for key in (
                "scene",
                "actions",
                "actors",
                "objects",
                "visible_text",
                "changes_from_previous",
                "uncertainty",
            )
            if description.get(key) not in (None, "", [])
        },
        keyframes=list(attributes.get("keyframes", [])),
        metadata={
            "observation_ids": [observation.id],
            "provenance_kind": observation.provenance_kind.value,
            "generation": attributes.get("generation", {}),
        },
    )
