"""Transcriptor determinista para pruebas sin servicio de habla."""
from __future__ import annotations

import io
import wave

from episodic.ports.speech import Transcript, TranscriptSegment


class FakeSpeechTranscriber:
    """Produce un segmento simulado cada ``segment_seconds`` del WAV recibido."""

    backend = "fake"
    model_id = "fake-speech"

    def __init__(self, segment_seconds: float = 4.0) -> None:
        self.segment_seconds = segment_seconds
        self.calls: list[tuple[str, str | None]] = []

    def transcribe(
        self, audio: bytes, filename: str, language: str | None = None
    ) -> Transcript:
        self.calls.append((filename, language))
        with wave.open(io.BytesIO(audio), "rb") as source:
            duration = source.getnframes() / source.getframerate()
        segments = []
        start = 0.0
        while start < duration:
            end = min(start + self.segment_seconds, duration)
            segments.append(
                TranscriptSegment(
                    start_seconds=round(start, 3),
                    end_seconds=round(end, 3),
                    text=f"Habla simulada del segundo {start:.1f} al {end:.1f}.",
                )
            )
            start = end
        return Transcript(
            segments=tuple(segments), language=language, duration_seconds=duration
        )
