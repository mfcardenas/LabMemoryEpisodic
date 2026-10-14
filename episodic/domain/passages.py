"""Pasajes: la unidad mínima de búsqueda dentro de un episodio.

Un pasaje es un **evento** (tramo de vídeo descrito, con su título, resumen,
actores, objetos, texto visible y lo que se dijo mientras tanto) o una
observación con contenido propio que no pertenece a ningún evento (una frase
transcrita fuera del vídeo, un fragmento de documento), siempre con su hora
absoluta y su posición en el archivo. Se indexa por separado para que una
pregunta fina no compita con todo el episodio, y cada resultado apunta al
momento exacto que lo respalda.

La tabla `passages` de SQLite guarda estos objetos tal cual (proyección,
como Qdrant): la búsqueda hidrata desde ella sin volver a las observaciones.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from episodic.domain.entities import Observation
from episodic.domain.processing import Event

# Observaciones sin contenido que buscar (solo datos técnicos del fichero o
# estados de proceso).
NON_PASSAGE_TYPES = frozenset(
    {"audio_segment", "video_interval", "image_frame_metadata", "document_page_status", "mock_observation"}
)
# Tipo de observación que da lugar a un evento; cuando el evento existe, es el
# evento (más rico) el que se indexa, no la observación.
VIDEO_EVENT_TYPE = "video_event"
SPEECH_TYPE = "speech_segment"

# Etiquetas de la descripción visual que aportan términos buscables.
_VISUAL_LIST_KEYS = ("objects", "actions", "actors", "people", "tools", "entities", "visible_text")


@dataclass
class Passage:
    """Lo que se indexa y lo que se devuelve: autosuficiente para mostrarse."""

    id: str                      # id del evento o de la observación
    kind: str                    # video_event | speech_segment | document_text_fragment | …
    episode_id: str
    text: str
    title: str = ""
    environment_id: Optional[str] = None
    source_id: Optional[str] = None
    modality: Optional[str] = None
    asset_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    start_offset_seconds: Optional[float] = None
    end_offset_seconds: Optional[float] = None
    page_number: Optional[int] = None
    sequence_number: Optional[int] = None
    event_id: Optional[str] = None
    observation_id: Optional[str] = None
    model: Optional[str] = None
    keyframes: int = 0

    @property
    def point_id(self) -> str:
        return passage_point_id(self.id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passage_id": self.id,
            "kind": self.kind,
            # Compatibilidad con el front: el tipo de pasaje.
            "observation_type": self.kind,
            "event_id": self.event_id,
            "observation_id": self.observation_id,
            "episode_id": self.episode_id,
            "modality": self.modality,
            "title": self.title,
            "text": self.text,
            "start_time": _iso(self.start_time),
            "end_time": _iso(self.end_time),
            "start_offset_seconds": self.start_offset_seconds,
            "end_offset_seconds": self.end_offset_seconds,
            "page_number": self.page_number,
            "sequence_number": self.sequence_number,
            "source_id": self.source_id,
            "environment_id": self.environment_id,
            "asset_id": self.asset_id,
            "keyframes": self.keyframes,
            "model": self.model,
        }


def passage_point_id(passage_id: str) -> str:
    return f"ps_{passage_id}"


# -- Construcción -------------------------------------------------------------


def event_passage_text(event: Event) -> str:
    """Todo lo legible del evento. Lo más discriminante va primero (título,
    resumen, lo que se dijo, texto visible, objetos, acciones, actores) y la
    descripción larga después: el reordenador solo lee el principio."""
    attributes = event.attributes or {}
    parts: list[str] = [event.title, event.summary]
    for entry in attributes.get("speech") or ():
        parts.append(str(entry.get("text") if isinstance(entry, dict) else entry))
    for key in _VISUAL_LIST_KEYS:
        for item in attributes.get(key) or ():
            parts.append(_flatten(item))
    parts += [event.description, str(attributes.get("scene") or ""), str(attributes.get("changes_from_previous") or "")]
    return _join(parts)


def passage_text(observation: Observation) -> str:
    """Texto buscable de una observación; vacío si no es un pasaje."""
    if observation.observation_type in NON_PASSAGE_TYPES:
        return ""
    parts: list[str] = []
    visual = observation.attributes.get("description")
    if isinstance(visual, dict):
        parts += [str(visual.get(key) or "") for key in ("title", "summary")]
    parts.append(observation.description or observation.content or "")
    if isinstance(visual, dict):
        for key in _VISUAL_LIST_KEYS:
            for item in visual.get(key) or ():
                parts.append(_flatten(item))
        parts += [str(visual.get(key) or "") for key in ("scene", "changes_from_previous")]
    return _join(parts)


def passage_from_event(event: Event, episode: Any) -> Optional[Passage]:
    text = event_passage_text(event)
    if not text:
        return None
    generation = (event.metadata or {}).get("generation") or {}
    speech = (event.attributes or {}).get("speech") or []
    return Passage(
        id=event.id,
        kind=VIDEO_EVENT_TYPE,
        episode_id=episode.id,
        text=text,
        title=event.title,
        environment_id=event.environment_id or (episode.environment_ids[0] if episode.environment_ids else None),
        source_id=event.source_id,
        modality="video",
        asset_id=event.asset_id,
        start_time=event.start_time,
        end_time=event.end_time,
        start_offset_seconds=_number(event.start_offset_seconds),
        end_offset_seconds=_number(event.end_offset_seconds),
        sequence_number=event.sequence_number,
        event_id=event.id,
        observation_id=next(iter((event.metadata or {}).get("observation_ids") or []), None),
        model=generation.get("vision_model") or (f"+{generation.get('speech_model')}" if speech else None),
        keyframes=len(event.keyframes or []),
    )


def passage_from_observation(observation: Observation, episode: Any) -> Optional[Passage]:
    text = passage_text(observation)
    if not text:
        return None
    attributes = observation.attributes or {}
    generation = attributes.get("generation") or {}
    start, end = passage_span(observation, episode)
    page = attributes.get("page_number")
    return Passage(
        id=observation.id,
        kind=observation.observation_type,
        episode_id=episode.id,
        text=text,
        title=passage_title(observation),
        environment_id=observation.environment_id or (episode.environment_ids[0] if episode.environment_ids else None),
        source_id=observation.source_id,
        modality=observation.modality,
        asset_id=observation.asset_id,
        start_time=start,
        end_time=end,
        start_offset_seconds=_number(attributes.get("start_offset_seconds")),
        end_offset_seconds=_number(attributes.get("end_offset_seconds")),
        page_number=int(page) if isinstance(page, (int, float)) else None,
        sequence_number=_int(attributes.get("segment_index")),
        observation_id=observation.id,
        model=generation.get("vision_model") or generation.get("speech_model"),
        keyframes=len(attributes.get("keyframes") or []),
    )


def episode_passages(episode: Any, events: Iterable[Event], observations: Iterable[Observation]) -> list[Passage]:
    """Los pasajes de un episodio: sus eventos y las observaciones con
    contenido que ningún evento ya recoge (habla adherida, tramo descrito)."""
    passages: list[Passage] = []
    covered: set[str] = set()
    for event in sorted(events, key=lambda item: (item.sequence_number, item.start_time or datetime.min)):
        passage = passage_from_event(event, episode)
        if passage is None:
            continue
        passages.append(passage)
        covered.update((event.metadata or {}).get("observation_ids") or [])
        for entry in (event.attributes or {}).get("speech") or ():
            if isinstance(entry, dict) and entry.get("observation_id"):
                covered.add(entry["observation_id"])
    for observation in observations:
        if observation.id in covered:
            continue
        passage = passage_from_observation(observation, episode)
        if passage is not None:
            passages.append(passage)
    return passages


def passage_title(observation: Observation) -> str:
    visual = observation.attributes.get("description")
    if isinstance(visual, dict) and visual.get("title"):
        return str(visual["title"])
    return ""


def passage_span(observation: Observation, episode: Any = None) -> tuple[Optional[datetime], Optional[datetime]]:
    """Hora absoluta del pasaje: la suya, la de captura o, si no, la del episodio."""
    if observation.start_time:
        return observation.start_time, observation.end_time or observation.start_time
    capture = observation.capture_time
    if capture is not None and capture.absolute is not None:
        return capture.absolute, capture.absolute
    if episode is not None and getattr(episode, "start_time", None):
        return episode.start_time, episode.end_time or episode.start_time
    interval = getattr(episode, "interval", None)
    if interval and interval.start and interval.start.absolute:
        end = interval.end.absolute if interval.end and interval.end.absolute else interval.start.absolute
        return interval.start.absolute, end
    return None, None


def _flatten(item: Any) -> str:
    if isinstance(item, dict):
        values = [item.get("label"), *(item.get("attributes") or []), item.get("description")]
        return " · ".join(str(value) for value in values if value)
    return str(item)


def _join(parts: Iterable[str]) -> str:
    seen: set[str] = set()
    unique = []
    for part in (str(part).strip() for part in parts):
        if part and part not in seen:
            seen.add(part)
            unique.append(part)
    return "\n".join(unique)


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# -- Resultados de búsqueda -----------------------------------------------------


@dataclass
class PassageScores:
    """Rastro de cómo llegó el pasaje a su posición."""

    dense: Optional[float] = None
    dense_rank: Optional[int] = None
    lexical: Optional[float] = None
    lexical_rank: Optional[int] = None
    fused: float = 0.0
    rerank: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dense": self.dense,
            "dense_rank": self.dense_rank,
            "lexical": self.lexical,
            "lexical_rank": self.lexical_rank,
            "fused": self.fused,
            "rerank": self.rerank,
        }


@dataclass
class PassageHit:
    passage: Passage
    scores: PassageScores
    snippet: str = ""
    # Tras reordenar: "threshold" (llega al umbral), "agreement" (ambas vías
    # coinciden) o None (descartado, o sin reordenar).
    kept_by: Optional[str] = None
    # Eventos anterior y siguiente del mismo episodio, si se pidieron.
    neighbors: Optional[dict[str, list[Passage]]] = None

    @property
    def text(self) -> str:
        return self.passage.text

    @property
    def episode_id(self) -> str:
        return self.passage.episode_id

    @property
    def offset_seconds(self) -> Optional[float]:
        return self.passage.start_offset_seconds

    @property
    def start_time(self) -> Optional[datetime]:
        return self.passage.start_time

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.passage.to_dict(),
            "snippet": self.snippet,
            "scores": self.scores.to_dict(),
            "kept_by": self.kept_by,
            "neighbors": (
                {key: [item.to_dict() for item in value] for key, value in self.neighbors.items()}
                if self.neighbors is not None
                else None
            ),
        }


@dataclass
class PassageGroup:
    """Los pasajes de un mismo episodio, en orden de relevancia."""

    episode: Any
    passages: list[PassageHit] = field(default_factory=list)

    @property
    def best(self) -> float:
        return max((order_score(hit) for hit in self.passages), default=0.0)

    def to_dict(self) -> dict[str, Any]:
        episode = self.episode
        return {
            "episode": {
                "id": episode.id,
                "title": episode.title,
                "external_id": episode.external_id,
                "environment_ids": list(episode.environment_ids),
                "start_time": _iso(episode.start_time),
                "end_time": _iso(episode.end_time),
            },
            "best_score": self.best,
            "passages": [hit.to_dict() for hit in self.passages],
        }


@dataclass
class PassageSearchRun:
    query: str
    groups: list[PassageGroup]
    lexical_query: str = ""
    dense_candidates: int = 0
    lexical_candidates: int = 0
    fused_candidates: int = 0
    rerank_model: Optional[str] = None
    rerank_applied: bool = False
    rerank_error: Optional[str] = None
    rerank_min_score: Optional[float] = None
    agreement_rank: int = 0
    discarded_low_relevance: int = 0
    not_reranked: int = 0
    took_ms: float = 0.0
    # Pasajes que hay en el índice de texto: 0 significa que falta indexar.
    indexed_passages: Optional[int] = None
    discarded_groups: list[PassageGroup] = field(default_factory=list)
    # Cómo se entendió la pregunta (intención, intervalo, texto buscado).
    plan: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "lexical_query": self.lexical_query,
            "dense_candidates": self.dense_candidates,
            "lexical_candidates": self.lexical_candidates,
            "fused_candidates": self.fused_candidates,
            "rerank": {
                "model": self.rerank_model,
                "applied": self.rerank_applied,
                "error": self.rerank_error,
                "min_score": self.rerank_min_score,
                "agreement_rank": self.agreement_rank,
                "discarded_low_relevance": self.discarded_low_relevance,
                "not_reranked": self.not_reranked,
            },
            "took_ms": round(self.took_ms, 1),
            "passage_count": sum(len(group.passages) for group in self.groups),
            "indexed_passages": self.indexed_passages,
            "groups": [group.to_dict() for group in self.groups],
            "discarded_groups": [group.to_dict() for group in self.discarded_groups],
        }


def order_score(hit: PassageHit) -> float:
    """Puntuación con la que se ordena: la del reordenador si se aplicó."""
    return hit.scores.rerank if hit.scores.rerank is not None else hit.scores.fused


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None
