"""Resúmenes jerárquicos: texto generado y revisable sobre la memoria.

Un resumen describe un **episodio**, un **día** de un entorno o un **entorno**
entero a partir del nivel inferior (eventos → episodios → días). Es texto de
modelo, así que lleva modelo, versión del prompt y huella de la entrada;
regenerar crea una revisión nueva y la anterior se conserva.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from episodic.domain.model import DomainValidationError, SerializableDomainModel, validate_identifier
from episodic.domain.times import utc_now

SCOPES = ("episode", "day", "environment")


@dataclass
class Summary(SerializableDomainModel):
    id: str = ""
    scope: str = "episode"
    scope_key: str = ""           # episode_id | "<env>|YYYY-MM-DD" | env
    revision: int = 1
    is_current: bool = True
    title: str = ""
    summary: str = ""
    narrative: str = ""
    # Citas verificadas: {event_sequence, offset_seconds, text} para episodios;
    # {episode_external_id, text} para días; {day, text} para entornos.
    highlights: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    model_id: str = ""
    prompt_version: str = ""
    input_hash: str = ""
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        if self.scope not in SCOPES:
            raise DomainValidationError(f"Ámbito de resumen desconocido: {self.scope}")
        if not self.scope_key:
            raise DomainValidationError("Summary.scope_key es obligatorio")
        if self.revision < 1:
            raise DomainValidationError("Summary.revision empieza en 1")
        if not self.model_id:
            raise DomainValidationError("Summary.model_id es obligatorio: el texto generado lleva su procedencia")

    @property
    def searchable_text(self) -> str:
        parts = [self.title, self.summary, *(item.get("text", "") for item in self.highlights)]
        return "\n".join(part for part in parts if part)
