"""Generación determinista de ventanas técnicas de audio y vídeo."""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import timedelta

from episodic.domain.processing import ProcessingRun, ProcessingWindow
from episodic.domain.types import EvidenceMediaType, ProcessingRunState
from episodic.ports.repository import EpisodicRepository


_WINDOW_NAMESPACE = uuid.UUID("c07e4466-b207-4c1c-b6aa-79e767d39311")


@dataclass(frozen=True)
class WindowingResult:
    run: ProcessingRun
    windows: list[ProcessingWindow]


class WindowingService:
    """Aplica DeltaTime a ProcessingWindow, nunca a Episode."""

    def __init__(self, repository: EpisodicRepository) -> None:
        self.repository = repository

    def create_windows(
        self,
        asset_id: str,
        *,
        duration_seconds: float,
        overlap_seconds: float = 0.0,
        total_duration_seconds: float | None = None,
    ) -> WindowingResult:
        asset = self.repository.get_asset(asset_id)
        if asset is None:
            raise ValueError(f"Asset inexistente: {asset_id}")
        if asset.media_type not in {EvidenceMediaType.AUDIO, EvidenceMediaType.VIDEO}:
            raise ValueError("Solo los activos de audio y vídeo admiten ventanas en Fase 3")
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("--duration debe ser positivo")
        if (
            not math.isfinite(overlap_seconds)
            or overlap_seconds < 0
            or overlap_seconds >= duration_seconds
        ):
            raise ValueError("--overlap debe ser no negativo y menor que --duration")

        total = asset.duration_seconds if total_duration_seconds is None else total_duration_seconds
        if total is None:
            raise ValueError(
                "No se pudo determinar la duración del activo; "
                "indique --total-duration explícitamente"
            )
        if not math.isfinite(total) or total <= 0:
            raise ValueError("La duración total debe ser positiva")

        policy = f"{asset.id}:{duration_seconds:.9f}:{overlap_seconds:.9f}:{total:.9f}"
        run_id = str(uuid.uuid5(_WINDOW_NAMESPACE, f"run:{policy}"))
        origin = asset.captured_at or asset.created_at
        timeline_basis = "asset_captured_at" if asset.captured_at else "asset_created_at_technical_axis"
        run = ProcessingRun(
            id=run_id,
            environment_id=asset.environment_id,
            source_id=asset.source_id,
            asset_ids=[asset.id],
            producer="deterministic-windowing",
            model_version="phase3",
            state=ProcessingRunState.COMPLETED,
            start_time=origin,
            end_time=origin + timedelta(seconds=total),
            parameters={
                "window_duration_seconds": duration_seconds,
                "overlap_seconds": overlap_seconds,
                "total_duration_seconds": total,
            },
            metadata={"timeline_basis": timeline_basis},
        )
        self.repository.save_processing_run(run)

        windows: list[ProcessingWindow] = []
        step = duration_seconds - overlap_seconds
        start_offset = 0.0
        sequence = 0
        while start_offset < total:
            end_offset = min(start_offset + duration_seconds, total)
            actual_delta = end_offset - start_offset
            window_id = str(
                uuid.uuid5(
                    _WINDOW_NAMESPACE,
                    f"window:{policy}:{sequence}:{start_offset:.9f}:{end_offset:.9f}",
                )
            )
            window = ProcessingWindow(
                id=window_id,
                processing_run_id=run.id,
                environment_id=asset.environment_id,
                source_id=asset.source_id,
                asset_id=asset.id,
                start_time=origin + timedelta(seconds=start_offset),
                end_time=origin + timedelta(seconds=end_offset),
                sequence_number=sequence,
                start_offset_seconds=start_offset,
                end_offset_seconds=end_offset,
                # DeltaTime es la política de procesamiento; la última ventana
                # puede ser más corta y sus offsets conservan la duración real.
                delta_time_seconds=duration_seconds,
                overlap_seconds=overlap_seconds,
                producer="deterministic-windowing",
                model_version="phase3",
                metadata={
                    "timeline_basis": timeline_basis,
                    "is_terminal_window": end_offset == total,
                },
            )
            self.repository.save_processing_window(window)
            windows.append(window)
            if end_offset == total:
                break
            start_offset += step
            sequence += 1

        return WindowingResult(run=run, windows=windows)
