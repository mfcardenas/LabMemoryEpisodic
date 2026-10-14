"""Respuesta del agente: texto con citas verificadas y su auditoría."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from episodic.domain.model import SerializableDomainModel
from episodic.domain.times import utc_now

CONFIDENCE_LEVELS = ("alta", "media", "baja", "sin_evidencia")


@dataclass
class Citation:
    """Un momento de la memoria que respalda una frase de la respuesta."""

    handle: str                     # marca usada en el texto: m1, m2…
    passage_id: str
    kind: str
    episode_id: str
    episode_external_id: str | None = None
    episode_title: str = ""
    event_id: str | None = None
    observation_id: str | None = None
    start_time: str | None = None
    start_offset_seconds: float | None = None
    end_offset_seconds: float | None = None
    page_number: int | None = None
    title: str = ""
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class AnswerRun(SerializableDomainModel):
    id: str = ""
    question: str = ""
    answer: str = ""                # markdown con marcas [mN]
    confidence: str = "sin_evidencia"
    abstained: bool = False
    citations: list[dict[str, Any]] = field(default_factory=list)
    dropped_citations: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    model_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    took_ms: float = 0.0
    error: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
