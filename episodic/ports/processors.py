"""Contrato común de procesadores normalizados de Fase 4."""
from __future__ import annotations

from typing import Protocol, TypeAlias

from episodic.domain.entities import Observation
from episodic.domain.processing import ProcessingWindow
from episodic.domain.provenance import Asset
from episodic.domain.types import EvidenceMediaType


ProcessorTarget: TypeAlias = Asset | ProcessingWindow


class ObservationProcessor(Protocol):
    name: str
    model_version: str
    supported_modalities: frozenset[EvidenceMediaType]

    def process(self, asset_or_window: ProcessorTarget) -> list[Observation]: ...


class ProcessorExecutionError(RuntimeError):
    """Fallo localizado: el Asset original permanece en el repositorio."""
