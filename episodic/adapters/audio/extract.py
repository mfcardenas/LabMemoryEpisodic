"""Extracción de la pista de audio de un vídeo o audio a WAV PCM mono.

Usa PyAV, que incluye FFmpeg: sirve tanto para la pista de un MP4 como para
un MP3/M4A/WAV suelto. El resultado es un WAV en memoria listo para enviar a
un servicio de transcripción.
"""
from __future__ import annotations

import io
import wave
from pathlib import Path

from episodic.ports.speech import ExtractedAudio


class PyAVAudioExtractor:
    def __init__(self, sample_rate_hz: int = 16000) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz debe ser positivo")
        self.sample_rate_hz = sample_rate_hz

    @property
    def version(self) -> str:
        import av

        return f"pyav-{av.__version__}-pcm16-mono-{self.sample_rate_hz}"

    def extract(self, path: str | Path) -> ExtractedAudio | None:
        """Devuelve ``None`` si el archivo no tiene pista de audio."""
        import av

        with av.open(str(path)) as container:
            if not container.streams.audio:
                return None
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=self.sample_rate_hz)
            chunks: list[bytes] = []
            start_offset: float | None = None
            for frame in container.decode(stream):
                if start_offset is None and frame.time is not None:
                    start_offset = float(frame.time)
                for resampled in resampler.resample(frame):
                    chunks.append(bytes(resampled.planes[0])[: resampled.samples * 2])
            for resampled in resampler.resample(None):
                chunks.append(bytes(resampled.planes[0])[: resampled.samples * 2])

        pcm = b"".join(chunks)
        if not pcm:
            return None
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate_hz)
            output.writeframes(pcm)
        return ExtractedAudio(
            wav=buffer.getvalue(),
            sample_rate_hz=self.sample_rate_hz,
            duration_seconds=len(pcm) / 2 / self.sample_rate_hz,
            start_offset_seconds=max(start_offset or 0.0, 0.0),
        )
