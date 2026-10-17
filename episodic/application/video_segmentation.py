"""Segmentación determinista de un vídeo muestreado en tramos candidatos a evento.

Un corte se produce cuando la escena cambia de forma apreciable entre dos
muestras consecutivas (y el tramo ya dura lo mínimo) o cuando el tramo alcanza
su duración máxima. Las ventanas resultantes no son detecciones de acciones:
solo delimitan qué fotogramas se describen juntos.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from episodic.ports.video import SampledFrame


@dataclass(frozen=True)
class SegmentationConfig:
    min_segment_seconds: float = 2.0
    max_segment_seconds: float = 15.0
    scene_change_threshold: float = 0.12
    """Diferencia media normalizada (0–1) entre miniaturas que se considera cambio."""
    max_keyframes: int = 3

    def __post_init__(self) -> None:
        if self.min_segment_seconds <= 0 or self.max_segment_seconds < self.min_segment_seconds:
            raise ValueError("Duraciones de segmento inválidas")
        if not 0 < self.scene_change_threshold <= 1:
            raise ValueError("scene_change_threshold debe estar en (0, 1]")
        if self.max_keyframes < 1:
            raise ValueError("max_keyframes debe ser al menos 1")

    @property
    def version(self) -> str:
        return (
            f"scene-v1:{self.min_segment_seconds}-{self.max_segment_seconds}"
            f"@{self.scene_change_threshold}"
        )


@dataclass(frozen=True)
class VideoSegment:
    index: int
    start_offset_seconds: float
    end_offset_seconds: float
    frames: tuple[SampledFrame, ...]
    keyframes: tuple[SampledFrame, ...]
    boundary_reason: str
    """Por qué empieza el tramo: ``start``, ``scene_change`` o ``max_duration``."""
    scene_change_score: float


def signature_distance(left: Sequence[int], right: Sequence[int]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Las firmas deben tener la misma longitud")
    return sum(abs(a - b) for a, b in zip(left, right)) / (255 * len(left))


class SceneSegmenter:
    def __init__(self, config: SegmentationConfig | None = None) -> None:
        self.config = config or SegmentationConfig()

    def segment(
        self, frames: Sequence[SampledFrame], duration_seconds: float | None
    ) -> list[VideoSegment]:
        if not frames:
            return []
        ordered = sorted(frames, key=lambda item: item.offset_seconds)
        groups: list[tuple[list[SampledFrame], str, float]] = [([ordered[0]], "start", 0.0)]
        for previous, frame in zip(ordered, ordered[1:]):
            current, _reason, _score = groups[-1]
            elapsed = frame.offset_seconds - current[0].offset_seconds
            change = signature_distance(previous.signature, frame.signature)
            if elapsed >= self.config.max_segment_seconds:
                groups.append(([frame], "max_duration", change))
            elif (
                change >= self.config.scene_change_threshold
                and elapsed >= self.config.min_segment_seconds
            ):
                groups.append(([frame], "scene_change", change))
            else:
                current.append(frame)

        end_of_video = max(duration_seconds or 0.0, ordered[-1].offset_seconds)
        segments: list[VideoSegment] = []
        for index, (group, reason, score) in enumerate(groups):
            start = group[0].offset_seconds
            end = groups[index + 1][0][0].offset_seconds if index + 1 < len(groups) else end_of_video
            segments.append(
                VideoSegment(
                    index=index,
                    start_offset_seconds=round(start, 3),
                    end_offset_seconds=round(max(end, start), 3),
                    frames=tuple(group),
                    keyframes=_keyframes(group, self.config.max_keyframes),
                    boundary_reason=reason,
                    scene_change_score=round(score, 4),
                )
            )
        return segments


def _keyframes(frames: Sequence[SampledFrame], limit: int) -> tuple[SampledFrame, ...]:
    """Primer, último e intermedios repartidos uniformemente."""
    if len(frames) <= limit:
        return tuple(frames)
    if limit == 1:
        return (frames[len(frames) // 2],)
    step = (len(frames) - 1) / (limit - 1)
    return tuple(frames[round(step * position)] for position in range(limit))
