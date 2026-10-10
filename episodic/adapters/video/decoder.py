"""Muestreo de fotogramas con PyAV usando los tiempos reales del decoder.

PyAV incluye las bibliotecas de FFmpeg en su wheel: no requiere FFmpeg del
sistema. Se importa de forma diferida para que el núcleo arranque sin él.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from episodic.ports.video import SampledFrame, SampledVideo

_SIGNATURE_SIZE = (32, 32)


def video_decoding_available() -> bool:
    try:
        import av  # noqa: F401
    except ImportError:
        return False
    return True


class PyAVFrameSampler:
    """Lee el vídeo de forma incremental y conserva un fotograma por intervalo."""

    def __init__(self, sample_fps: float = 1.0, max_side: int = 768, jpeg_quality: int = 85) -> None:
        if sample_fps <= 0:
            raise ValueError("sample_fps debe ser positivo")
        self.sample_fps = sample_fps
        self.max_side = max_side
        self.jpeg_quality = jpeg_quality

    @property
    def version(self) -> str:
        import av

        return f"pyav-{av.__version__}"

    def sample(self, path: str | Path) -> SampledVideo:
        import av

        interval = 1.0 / self.sample_fps
        frames: list[SampledFrame] = []
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            average_fps = float(stream.average_rate) if stream.average_rate else None
            duration = _duration_seconds(container, stream)
            time_source = "decoder_pts"
            next_offset = 0.0
            for index, frame in enumerate(container.decode(stream)):
                if frame.time is not None:
                    offset = float(frame.time)
                elif average_fps:
                    offset = index / average_fps
                    time_source = "frame_index"
                else:
                    raise ValueError("El vídeo no expone tiempos ni frecuencia de fotogramas")
                if offset + 1e-6 < next_offset:
                    continue
                image = frame.to_image()
                frames.append(self._sample(image, offset, index))
                next_offset = offset + interval
            width, height = stream.codec_context.width, stream.codec_context.height
        if not frames:
            raise ValueError("No se pudo decodificar ningún fotograma")
        return SampledVideo(
            duration_seconds=duration,
            average_fps=average_fps,
            width=width,
            height=height,
            frames=tuple(frames),
            time_source=time_source,
        )

    def _sample(self, image: Image.Image, offset: float, frame_number: int) -> SampledFrame:
        image = image.convert("RGB")
        signature = tuple(image.convert("L").resize(_SIGNATURE_SIZE).tobytes())
        image.thumbnail((self.max_side, self.max_side))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=self.jpeg_quality)
        return SampledFrame(
            offset_seconds=round(offset, 3),
            frame_number=frame_number,
            jpeg=buffer.getvalue(),
            signature=signature,
        )


def _duration_seconds(container, stream) -> float | None:
    if stream.duration is not None and stream.time_base is not None:
        return float(stream.duration * stream.time_base)
    if container.duration is not None:
        return container.duration / 1_000_000
    return None
