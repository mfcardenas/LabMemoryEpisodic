"""Derivación de eventos a partir de observaciones de tramos de vídeo.

En la Fase C un evento corresponde a un tramo descrito por el modelo de
visión. En la Fase D cada evento recibe además lo que se dijo durante su
intervalo: los segmentos de habla se asignan al evento con el que más se
solapan. La fusión (Fase E) enriquecerá su descripción sin cambiar su
identidad.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Sequence

from episodic.application.episode_composition import latest_processor_outputs
from episodic.domain.entities import Observation
from episodic.domain.processing import Event
from episodic.ports.repository import EpisodicRepository
from episodic.processors.speech import SPEECH_OBSERVATION
from episodic.processors.video_events import VIDEO_EVENT_OBSERVATION

_EVENT_NAMESPACE = uuid.UUID("2f6b8c1e-4d7a-4b52-9c0e-7a3d5e1f9b20")
EVENT_PRODUCER = "video-event-derivation"
EVENT_MODEL_VERSION = "events-v2"


class EventDerivationService:
    def __init__(self, repository: EpisodicRepository) -> None:
        self.repository = repository

    def derive_for_assets(
        self, asset_ids: Sequence[str], audio_offset_seconds: float = 0.0
    ) -> list[Event]:
        """``audio_offset_seconds`` desplaza el habla de los archivos que no son
        el propio vídeo (un audio grabado aparte que empezó antes o después)."""
        outputs = {
            asset_id: latest_processor_outputs(
                self.repository.list_observations_for_asset(asset_id)
            )
            for asset_id in asset_ids
        }
        speech = [
            item
            for observations in outputs.values()
            for item in observations
            if item.observation_type == SPEECH_OBSERVATION
        ]
        events: list[Event] = []
        for asset_id in asset_ids:
            observations = sorted(
                (
                    item
                    for item in outputs[asset_id]
                    if item.observation_type == VIDEO_EVENT_OBSERVATION
                ),
                key=lambda item: (item.start_time, item.attributes.get("segment_index", 0)),
            )
            asset_events = [event_from_observation(item) for item in observations]
            asset_events = attach_speech(asset_events, speech, audio_offset_seconds)
            for event in asset_events:
                self.repository.save_event(event)
            events.extend(asset_events)
        return events


def attach_speech(
    events: Sequence[Event], speech: Sequence[Observation], audio_offset_seconds: float = 0.0
) -> list[Event]:
    """Asigna cada segmento de habla al evento con el que más se solapa.

    Un segmento que no se solapa con ningún evento (habla antes o después del
    vídeo) no se asigna: sigue disponible como observación de la captura.
    """
    assigned: dict[str, list[tuple[Observation, datetime, datetime]]] = {}
    for observation in speech:
        if observation.start_time is None or observation.end_time is None:
            continue
        own_track = bool(events) and observation.asset_id == events[0].asset_id
        if not own_track and observation.modality == "video":
            # La pista de otro vídeo de la captura pertenece a sus propios eventos.
            continue
        shift = timedelta(seconds=0.0 if own_track else audio_offset_seconds)
        start = observation.start_time + shift
        end = observation.end_time + shift
        best: tuple[float, Event] | None = None
        for event in events:
            overlap = (min(end, event.end_time) - max(start, event.start_time)).total_seconds()
            if overlap > 0 and (best is None or overlap > best[0]):
                best = (overlap, event)
        if best is not None:
            assigned.setdefault(best[1].id, []).append((observation, start, end))

    result: list[Event] = []
    for event in events:
        items = sorted(assigned.get(event.id, []), key=lambda item: item[1])
        if not items:
            result.append(event)
            continue
        video_origin = event.start_time - timedelta(seconds=event.start_offset_seconds or 0.0)
        result.append(
            replace(
                event,
                attributes={
                    **event.attributes,
                    "speech": [
                        _speech_entry(observation, start, end, video_origin)
                        for observation, start, end in items
                    ],
                },
                metadata={
                    **event.metadata,
                    "speech_observation_ids": [observation.id for observation, _, _ in items],
                },
            )
        )
    return result


def _speech_entry(
    observation: Observation, start: datetime, end: datetime, video_origin: datetime
) -> dict[str, Any]:
    generation = observation.attributes.get("generation", {})
    return {
        "observation_id": observation.id,
        "asset_id": observation.asset_id,
        "source_id": observation.source_id,
        "text": observation.description,
        # Respecto al inicio del vídeo del evento: permite saltar a ese segundo.
        "start_offset_seconds": round((start - video_origin).total_seconds(), 3),
        "end_offset_seconds": round((end - video_origin).total_seconds(), 3),
        "speech_model": generation.get("speech_model"),
    }


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
