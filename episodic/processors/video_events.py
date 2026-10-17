"""Vídeo -> tramos -> descripción visual detallada por tramo (Fase C).

Cada tramo produce una observación ``video_event`` con su intervalo exacto,
sus fotogramas clave (guardados como evidencia derivada) y la descripción
estructurada del modelo de visión. El evento se deriva después, a partir de
estas observaciones, para que la percepción y el dominio queden separados.
"""
from __future__ import annotations

import time
from datetime import timedelta

from episodic.application.progress import note
from episodic.application.video_segmentation import SceneSegmenter
from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind, ProvenanceKind
from episodic.ports.repository import EpisodicRepository, EvidenceStore
from episodic.ports.video import FrameSampler
from episodic.ports.vision import SegmentContext, VisionDescriber
from episodic.processors.base import BaseObservationProcessor, ObservationDraft, ResolvedTarget

VIDEO_EVENT_OBSERVATION = "video_event"


class VideoEventProcessor(BaseObservationProcessor):
    name = "video-event-processor"
    label = "descripción visual por tramos"  # nombre en el registro de ejecución
    supported_modalities = frozenset({EvidenceMediaType.VIDEO})
    processes_whole_asset = True
    """El tramo lo decide el cambio de escena, no las ventanas técnicas."""

    def __init__(
        self,
        repository: EpisodicRepository,
        evidence_store: EvidenceStore,
        sampler: FrameSampler,
        segmenter: SceneSegmenter,
        describer: VisionDescriber,
        language: str = "es",
    ) -> None:
        super().__init__(repository)
        self.evidence_store = evidence_store
        self.sampler = sampler
        self.segmenter = segmenter
        self.describer = describer
        self.language = language
        # La versión forma parte de los IDs estables: otro modelo, prompt o
        # segmentación produce observaciones distintas en lugar de pisarlas.
        self.model_version = (
            f"video-events-v1|{describer.model_id}|{describer.prompt_version}"
            f"|{segmenter.config.version}|{sampler.sample_fps}fps"
        )

    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        path = self.original_path(target)
        video = self.sampler.sample(path)
        duration = video.duration_seconds or target.asset.duration_seconds
        segments = self.segmenter.segment(video.frames, duration)
        origin = target.window.start_time if target.window else target.start_time
        note(
            f"{len(video.frames)} fotograma(s) muestreados a {self.sampler.sample_fps:g} fps"
            f" en {duration or 0:.1f} s de vídeo: {len(segments)} tramo(s) por cambio de escena"
        )

        drafts: list[ObservationDraft] = []
        previous_summary: str | None = None
        for segment in segments:
            context = SegmentContext(
                segment_index=segment.index,
                segment_count=len(segments),
                start_offset_seconds=segment.start_offset_seconds,
                end_offset_seconds=segment.end_offset_seconds,
                video_duration_seconds=duration,
                frame_offsets_seconds=tuple(item.offset_seconds for item in segment.keyframes),
                previous_summary=previous_summary,
                language=self.language,
            )
            span = (
                f"tramo {segment.index + 1}/{len(segments)}"
                f" ({segment.start_offset_seconds:.1f}–{segment.end_offset_seconds:.1f} s)"
            )
            note(
                f"{span}: describiendo {len(segment.keyframes)} fotograma(s) clave"
                f" con {self.describer.model_id}"
            )
            started = time.monotonic()
            description = self.describer.describe_segment(
                [item.jpeg for item in segment.keyframes], context
            )
            note(
                f"{span} descrito en {time.monotonic() - started:.1f} s"
                + (f": «{_short(description.title)}»" if description.title else "")
            )
            keyframes = []
            for frame in segment.keyframes:
                internal_uri, _content_hash = self.evidence_store.store(
                    frame.jpeg, f"keyframe_{frame.frame_number}.jpg"
                )
                keyframes.append(
                    {
                        "internal_uri": internal_uri,
                        "offset_seconds": frame.offset_seconds,
                        "frame_number": frame.frame_number,
                    }
                )
            start_time = origin + timedelta(seconds=segment.start_offset_seconds)
            end_time = origin + timedelta(seconds=segment.end_offset_seconds)
            drafts.append(
                ObservationDraft(
                    description=description.detailed_description,
                    observation_type=VIDEO_EVENT_OBSERVATION,
                    attributes={
                        "segment_index": segment.index,
                        "segment_count": len(segments),
                        "start_offset_seconds": segment.start_offset_seconds,
                        "end_offset_seconds": segment.end_offset_seconds,
                        "boundary_reason": segment.boundary_reason,
                        "scene_change_distance": segment.scene_change_score,
                        "sampled_frames": len(segment.frames),
                        "keyframes": keyframes,
                        "description": description.to_dict(),
                        "generation": {
                            "vision_model": self.describer.model_id,
                            "prompt_version": self.describer.prompt_version,
                            "segmentation": self.segmenter.config.version,
                            "sampler": self.sampler.version,
                            "sample_fps": self.sampler.sample_fps,
                            "time_source": video.time_source,
                            "language": self.language,
                        },
                    },
                    # Un VLM no ofrece una confianza calibrada: no se inventa.
                    confidence=None,
                    provenance_kind=ProvenanceKind.MODEL_DESCRIPTION,
                    evidence_kind=EvidenceReferenceKind.AUDIOVISUAL_INTERVAL,
                    evidence_locator={
                        "start_time": start_time,
                        "end_time": end_time,
                        "start_offset_seconds": segment.start_offset_seconds,
                        "end_offset_seconds": segment.end_offset_seconds,
                        "frame_number": segment.keyframes[0].frame_number,
                    },
                    start_time=start_time,
                    end_time=end_time,
                )
            )
            previous_summary = description.summary
        return drafts


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
