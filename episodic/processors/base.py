"""Base compartida para normalizar, versionar y persistir observaciones."""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from episodic.domain.entities import Observation
from episodic.domain.processing import ProcessingRun, ProcessingWindow
from episodic.domain.provenance import Asset, EvidenceReference
from episodic.domain.times import utc_now
from episodic.domain.types import (
    EvidenceMediaType,
    EvidenceReferenceKind,
    ProcessingRunState,
    ProvenanceKind,
)
from episodic.ports.processors import ProcessorExecutionError, ProcessorTarget
from episodic.ports.repository import EpisodicRepository


_PROCESSOR_NAMESPACE = uuid.UUID("569bdd67-e6dd-453d-a6e4-38ae6a9ca292")


@dataclass(frozen=True)
class ObservationDraft:
    description: str
    observation_type: str
    attributes: dict[str, Any]
    confidence: float | None
    provenance_kind: ProvenanceKind
    evidence_kind: EvidenceReferenceKind
    evidence_locator: dict[str, Any]
    start_time: datetime | None = None
    end_time: datetime | None = None


@dataclass(frozen=True)
class ResolvedTarget:
    asset: Asset
    window: ProcessingWindow | None
    start_time: datetime
    end_time: datetime

    @property
    def target_id(self) -> str:
        return self.window.id if self.window else self.asset.id

    @property
    def target_kind(self) -> str:
        return "processing_window" if self.window else "asset"


class BaseObservationProcessor(ABC):
    """Template determinista; los adaptadores solo extraen borradores."""

    name = "base-processor"
    model_version = "phase4-v1"
    supported_modalities: frozenset[EvidenceMediaType] = frozenset()

    def __init__(self, repository: EpisodicRepository) -> None:
        self.repository = repository

    def process(self, asset_or_window: ProcessorTarget) -> list[Observation]:
        target = self._resolve_target(asset_or_window)
        if target.asset.media_type not in self.supported_modalities:
            raise ValueError(
                f"{self.name} no admite modalidad {target.asset.media_type.value}"
            )

        run_id = self._stable_id(
            "run",
            target.asset.content_hash,
            target.target_kind,
            target.target_id,
        )
        started_at = utc_now()
        run = ProcessingRun(
            id=run_id,
            environment_id=target.asset.environment_id,
            source_id=target.asset.source_id,
            asset_ids=[target.asset.id],
            producer=self.name,
            model_version=self.model_version,
            state=ProcessingRunState.RUNNING,
            start_time=started_at,
            end_time=started_at,
            parameters={
                "target_kind": target.target_kind,
                "target_id": target.target_id,
            },
            metadata={"phase": 4},
        )
        self.repository.save_processing_run(run)

        try:
            drafts = self._extract(target)
            observations = [
                self._persist_draft(target, run, index, draft)
                for index, draft in enumerate(drafts)
            ]
            finished_at = utc_now()
            self.repository.save_processing_run(
                replace(
                    run,
                    state=ProcessingRunState.COMPLETED,
                    end_time=finished_at,
                    metadata={**run.metadata, "observation_count": len(observations)},
                )
            )
            return observations
        except Exception as exc:
            finished_at = utc_now()
            self.repository.save_processing_run(
                replace(
                    run,
                    state=ProcessingRunState.FAILED,
                    end_time=finished_at,
                    metadata={
                        **run.metadata,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
            )
            raise ProcessorExecutionError(
                f"{self.name} falló para {target.target_kind} {target.target_id}: {exc}"
            ) from exc

    @abstractmethod
    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        raise NotImplementedError

    def _resolve_target(self, target: ProcessorTarget) -> ResolvedTarget:
        if isinstance(target, Asset):
            persisted = self.repository.get_asset(target.id)
            if persisted is None:
                raise ValueError(f"Asset inexistente: {target.id}")
            start_time = persisted.captured_at or persisted.created_at
            duration = persisted.duration_seconds or 0.0
            end_time = start_time + timedelta(seconds=duration)
            return ResolvedTarget(persisted, None, start_time, end_time)
        if isinstance(target, ProcessingWindow):
            window = self.repository.get_processing_window(target.id)
            if window is None:
                raise ValueError(f"ProcessingWindow inexistente: {target.id}")
            asset = self.repository.get_asset(window.asset_id)
            if asset is None:
                raise ValueError(f"Asset inexistente para la ventana: {window.asset_id}")
            return ResolvedTarget(
                asset,
                window,
                window.start_time,  # type: ignore[arg-type]
                window.end_time,  # type: ignore[arg-type]
            )
        raise TypeError("process espera Asset o ProcessingWindow")

    def _persist_draft(
        self,
        target: ResolvedTarget,
        run: ProcessingRun,
        index: int,
        draft: ObservationDraft,
    ) -> Observation:
        reference_id = self._stable_id("evidence", run.id, str(index))
        reference = EvidenceReference(
            id=reference_id,
            asset_id=target.asset.id,
            reference_kind=draft.evidence_kind,
            producer=self.name,
            model_version=self.model_version,
            confidence=draft.confidence,
            metadata={
                "processing_run_id": run.id,
                "processing_window_id": target.window.id if target.window else None,
            },
            **draft.evidence_locator,
        )
        self.repository.save_evidence_reference(reference)

        start_time = draft.start_time or target.start_time
        end_time = draft.end_time or target.end_time
        observation = Observation(
            id=self._stable_id("observation", run.id, str(index)),
            content=draft.description,
            description=draft.description,
            modality=target.asset.media_type.value,
            observation_type=draft.observation_type,
            attributes=draft.attributes,
            provenance_kind=draft.provenance_kind,
            author=self.name,
            tool_version=self.model_version,
            confidence=draft.confidence,
            environment_id=target.asset.environment_id,
            source_id=target.asset.source_id,
            asset_id=target.asset.id,
            processing_run_id=run.id,
            processing_window_id=target.window.id if target.window else None,
            evidence_reference_ids=[reference.id],
            start_time=start_time,
            end_time=end_time,
            producer=self.name,
            model_version=self.model_version,
            metadata={
                "direct_evidence": True,
                "original_uri": target.asset.uri,
                "target_kind": target.target_kind,
            },
        )
        self.repository.save_observation(observation)
        return observation

    def _stable_id(self, kind: str, *parts: str) -> str:
        key = ":".join((self.name, self.model_version, kind, *parts))
        return str(uuid.uuid5(_PROCESSOR_NAMESPACE, key))

    @staticmethod
    def original_path(target: ResolvedTarget) -> Path:
        path = Path(target.asset.uri)
        if not path.is_file():
            raise FileNotFoundError(f"Archivo original no disponible: {path}")
        return path

    @staticmethod
    def audiovisual_locator(target: ResolvedTarget) -> dict[str, Any]:
        if target.window:
            return {
                "start_time": target.window.start_time,
                "end_time": target.window.end_time,
                "start_offset_seconds": target.window.start_offset_seconds,
                "end_offset_seconds": target.window.end_offset_seconds,
            }
        return {
            "start_time": target.start_time,
            "end_time": target.end_time,
            "start_offset_seconds": 0.0,
            "end_offset_seconds": target.asset.duration_seconds or 0.0,
        }
