"""Contrato del servicio que transcribe el habla de un audio."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class TranscriptSegment:
    """Fragmento de habla con su intervalo relativo al inicio del audio."""

    start_seconds: float
    end_seconds: float
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "text": self.text,
            "avg_logprob": self.avg_logprob,
            "no_speech_prob": self.no_speech_prob,
        }


@dataclass(frozen=True)
class Transcript:
    """Transcripción generada por un modelo: nunca se presenta como verificada."""

    segments: tuple[TranscriptSegment, ...]
    language: str | None = None
    duration_seconds: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(item.text for item in self.segments).strip()


@dataclass(frozen=True)
class ExtractedAudio:
    """Audio PCM mono en WAV, listo para enviar al servicio de transcripción."""

    wav: bytes
    sample_rate_hz: int
    duration_seconds: float
    start_offset_seconds: float
    """Tiempo de la primera muestra respecto al inicio del contenedor."""


class AudioExtractor(Protocol):
    @property
    def version(self) -> str: ...

    def extract(self, path: str | Path) -> ExtractedAudio | None:
        """Devuelve ``None`` si el archivo no tiene pista de audio."""
        ...


class SpeechTranscriberError(RuntimeError):
    """El servicio de transcripción no está disponible o respondió algo inválido."""


class SpeechTranscriber(Protocol):
    @property
    def model_id(self) -> str:
        """Identificador del modelo, registrado como procedencia."""
        ...

    @property
    def backend(self) -> str:
        """Tipo de backend (``http``, ``fake``…), registrado como procedencia."""
        ...

    def transcribe(
        self, audio: bytes, filename: str, language: str | None = None
    ) -> Transcript:
        """Transcribe ``audio`` (cualquier formato que el servicio admita)."""
        ...
