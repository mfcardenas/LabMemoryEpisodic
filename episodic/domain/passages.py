"""Pasajes: la unidad mínima de búsqueda dentro de un episodio.

Un pasaje es una observación con contenido propio (un tramo de vídeo
descrito, una frase transcrita, un fragmento de documento) y su hora
absoluta. Se indexa por separado para que una pregunta fina no compita con
todo el episodio, y cada resultado apunta al momento exacto que lo respalda.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from episodic.domain.entities import Observation

# Observaciones sin contenido que buscar (solo datos técnicos del fichero).
NON_PASSAGE_TYPES = frozenset({"audio_segment"})

# Etiquetas de la descripción visual que aportan términos buscables.
_VISUAL_LIST_KEYS = ("objects", "actions", "people", "tools", "entities")


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
    seen: set[str] = set()
    unique = []
    for part in (part.strip() for part in parts):
        if part and part not in seen:
            seen.add(part)
            unique.append(part)
    return "\n".join(unique)


def _flatten(item: Any) -> str:
    if isinstance(item, dict):
        values = [item.get("label"), *(item.get("attributes") or []), item.get("description")]
        return " · ".join(str(value) for value in values if value)
    return str(item)


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


def passage_point_id(observation_id: str) -> str:
    return f"ps_{observation_id}"


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
    observation: Observation
    text: str
    scores: PassageScores
    snippet: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    # Tras reordenar: "threshold" (llega al umbral), "agreement" (ambas vías
    # coinciden) o None (descartado, o sin reordenar).
    kept_by: Optional[str] = None

    @property
    def offset_seconds(self) -> Optional[float]:
        value = self.observation.attributes.get("start_offset_seconds")
        return float(value) if isinstance(value, (int, float)) else None

    def to_dict(self) -> dict[str, Any]:
        obs = self.observation
        attributes = obs.attributes
        return {
            "observation_id": obs.id,
            "episode_id": obs.episode_id,
            "observation_type": obs.observation_type,
            "modality": obs.modality,
            "title": passage_title(obs),
            "text": self.text,
            "snippet": self.snippet,
            "start_time": _iso(self.start_time),
            "end_time": _iso(self.end_time),
            "start_offset_seconds": self.offset_seconds,
            "end_offset_seconds": attributes.get("end_offset_seconds"),
            "page_number": attributes.get("page_number"),
            "source_id": obs.source_id,
            "environment_id": obs.environment_id,
            "asset_id": obs.asset_id,
            "keyframes": len(attributes.get("keyframes") or []),
            "model": (attributes.get("generation") or {}).get("vision_model")
            or (attributes.get("generation") or {}).get("speech_model"),
            "scores": self.scores.to_dict(),
            "kept_by": self.kept_by,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
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
