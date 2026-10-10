"""Observaciones acústicas deterministas y localizadas por intervalo."""
from __future__ import annotations

import math
import wave
from array import array

from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind, ProvenanceKind
from episodic.processors.base import BaseObservationProcessor, ObservationDraft, ResolvedTarget


class AudioProcessor(BaseObservationProcessor):
    name = "audio-processor"
    model_version = "phase4-audio-metrics-v1"
    supported_modalities = frozenset({EvidenceMediaType.AUDIO})

    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        path = self.original_path(target)
        duration = (target.end_time - target.start_time).total_seconds()
        attributes: dict[str, object] = {
            "duration_seconds": duration,
            "transcription": None,
        }
        if target.asset.mime_type == "audio/wav":
            attributes.update(_wav_interval_metrics(path, target))

        rms = attributes.get("rms")
        rms_detail = f", RMS {rms:.2f}" if isinstance(rms, float) else ""
        return [
            ObservationDraft(
                description=f"Segmento de audio de {duration:.3f} s{rms_detail}.",
                observation_type="audio_segment",
                attributes=attributes,
                confidence=1.0,
                provenance_kind=ProvenanceKind.SYSTEM_DERIVED,
                evidence_kind=EvidenceReferenceKind.AUDIOVISUAL_INTERVAL,
                evidence_locator=self.audiovisual_locator(target),
            )
        ]


def _wav_interval_metrics(path, target: ResolvedTarget) -> dict[str, object]:
    with wave.open(str(path), "rb") as audio:
        rate = audio.getframerate()
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()
        start_offset = target.window.start_offset_seconds if target.window else 0.0
        end_offset = (
            target.window.end_offset_seconds
            if target.window
            else target.asset.duration_seconds or 0.0
        )
        start_frame = min(int(start_offset * rate), audio.getnframes())
        end_frame = min(int(end_offset * rate), audio.getnframes())
        audio.setpos(start_frame)
        raw = audio.readframes(max(end_frame - start_frame, 0))

    result: dict[str, object] = {
        "sample_rate_hz": rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
    }
    if sample_width == 2 and raw:
        samples = array("h")
        samples.frombytes(raw)
        if samples:
            result["rms"] = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
            result["sample_count"] = len(samples) // max(channels, 1)
    return result
