"""Adaptadores reales de encoders basados en FastEmbed.

Implementan EpisodeTextEncoder, ImageEncoder y AlignedTextEncoder.
- Registran EncoderInfo con modelo, revisión, dimensión y normalización.
- Carga diferida (lazy loading) para no bloquear la inicialización del sistema.
- Compatible con caché configurable local para ejecución sin red.
- Dispositivo ``auto`` | ``cuda`` | ``cpu``: con ``onnxruntime-gpu`` (extra
  ``[gpu]``) Nomic pasa de ~8 a ~120 textos/s en una RTX 5080.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from episodic.ports.encoders import (
    AlignedTextEncoder,
    EncoderInfo,
    EpisodeTextEncoder,
    ImageEncoder,
    PassageReranker,
)

logger = logging.getLogger(__name__)

_resolved_device: Optional[str] = None


def resolve_device(device: str = "auto") -> str:
    """``cpu`` o ``cuda``. Con ``auto``, CUDA si onnxruntime la ofrece (y las
    librerías NVIDIA se pueden precargar); se resuelve una sola vez."""
    global _resolved_device
    device = (device or "auto").lower()
    if device == "cpu":
        return "cpu"
    if device == "cuda":
        _preload_cuda()
        return "cuda"
    if _resolved_device is None:
        try:
            import onnxruntime

            _preload_cuda()
            available = "CUDAExecutionProvider" in onnxruntime.get_available_providers()
        except Exception:  # pragma: no cover - sin onnxruntime no hay encoders reales
            available = False
        _resolved_device = "cuda" if available else "cpu"
        logger.info("Encoders en %s", _resolved_device.upper())
    return _resolved_device


def _preload_cuda() -> None:
    """Carga cuDNN/cuBLAS desde los paquetes ``nvidia-*`` de pip (onnxruntime
    ≥ 1.21) y baja el nivel de registro de ORT, que avisa por cada nodo que
    deja en CPU."""
    try:
        import onnxruntime

        onnxruntime.set_default_logger_severity(3)
        preload = getattr(onnxruntime, "preload_dlls", None)
        if preload is not None:
            preload()
    except Exception:  # pragma: no cover - sin librerías, el proveedor CUDA no estará disponible
        logger.debug("No se pudieron precargar las librerías CUDA", exc_info=True)


def _load_options(device: str) -> dict:
    return {"cuda": True} if resolve_device(device) == "cuda" else {}


class FastEmbedEpisodeTextEncoder:
    """Encoder de texto para documentos y queries basado en Nomic Embed."""

    def __init__(
        self,
        model_id: str = "nomic-ai/nomic-embed-text-v1.5",
        revision: str = "v1.5",
        dimension: int = 768,
        cache_dir: Optional[str] = None,
        threads: int = 2,
        document_prefix: str = "",
        query_prefix: str = "",
        batch_size: int = 1,
        device: str = "auto",
    ) -> None:
        self._model_id = model_id
        self._revision = revision
        self._dimension = dimension
        self._cache_dir = cache_dir
        self._threads = threads
        # Nomic v1.5 se entrenó con prefijos de tarea y FastEmbed no los añade.
        self._document_prefix = document_prefix
        self._query_prefix = query_prefix
        # Un lote se rellena hasta su texto más largo y la atención crece con
        # el cuadrado de la longitud: con textos de longitud dispar, lotes
        # pequeños son varias veces más rápidos en CPU. En GPU conviene 16–32.
        self._batch_size = max(1, batch_size)
        self._device = device
        self._model = None

    @property
    def info(self) -> EncoderInfo:
        return EncoderInfo(
            model_id=self._model_id,
            revision=self._revision,
            dimension=self._dimension,
            # FastEmbed entrega Nomic sin normalización L2 explícita. Qdrant
            # aplica la normalización propia de la distancia cosine al indexar.
            normalized=False,
            runtime=f"fastembed-onnx-{resolve_device(self._device)}",
        )

    def _ensure_loaded(self):
        if self._model is None:
            logger.info("Cargando modelo de texto Nomic: %s (%s)", self._model_id, resolve_device(self._device))
            from fastembed import TextEmbedding
            self._model = TextEmbedding(
                model_name=self._model_id,
                cache_dir=self._cache_dir,
                threads=self._threads,
                **_load_options(self._device),
            )
        return self._model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        model = self._ensure_loaded()
        documents = [self._document_prefix + text for text in texts]
        return [v.tolist() for v in model.embed(documents, batch_size=self._batch_size)]

    def embed_query(self, text: str) -> List[float]:
        model = self._ensure_loaded()
        text = self._query_prefix + text
        try:
            return next(model.query_embed([text])).tolist()
        except (AttributeError, NotImplementedError):
            return next(model.embed([text])).tolist()


class FastEmbedImageEncoder:
    """Encoder visual basado en CLIP ViT-B/32 vision tower."""

    def __init__(
        self,
        model_id: str = "Qdrant/clip-ViT-B-32-vision",
        revision: str = "v1",
        dimension: int = 512,
        cache_dir: Optional[str] = None,
        threads: int = 2,
        device: str = "auto",
    ) -> None:
        self._model_id = model_id
        self._revision = revision
        self._dimension = dimension
        self._cache_dir = cache_dir
        self._threads = threads
        self._device = device
        self._model = None

    @property
    def info(self) -> EncoderInfo:
        return EncoderInfo(
            model_id=self._model_id,
            revision=self._revision,
            dimension=self._dimension,
            normalized=True,
            runtime=f"fastembed-onnx-{resolve_device(self._device)}",
        )

    def _ensure_loaded(self):
        if self._model is None:
            logger.info("Cargando modelo CLIP vision: %s (%s)", self._model_id, resolve_device(self._device))
            from fastembed import ImageEmbedding
            self._model = ImageEmbedding(
                model_name=self._model_id,
                cache_dir=self._cache_dir,
                threads=self._threads,
                **_load_options(self._device),
            )
        return self._model

    def embed_images(self, image_paths: List[str]) -> List[List[float]]:
        if not image_paths:
            return []
        model = self._ensure_loaded()
        return [v.tolist() for v in model.embed(list(image_paths))]


class FastEmbedAlignedTextEncoder:
    """Encoder de texto alineado con el espacio visual basado en CLIP text tower."""

    def __init__(
        self,
        model_id: str = "Qdrant/clip-ViT-B-32-text",
        revision: str = "v1",
        dimension: int = 512,
        cache_dir: Optional[str] = None,
        threads: int = 2,
        device: str = "auto",
    ) -> None:
        self._model_id = model_id
        self._revision = revision
        self._dimension = dimension
        self._cache_dir = cache_dir
        self._threads = threads
        self._device = device
        self._model = None

    @property
    def info(self) -> EncoderInfo:
        return EncoderInfo(
            model_id=self._model_id,
            revision=self._revision,
            dimension=self._dimension,
            normalized=True,
            runtime=f"fastembed-onnx-{resolve_device(self._device)}",
        )

    def _ensure_loaded(self):
        if self._model is None:
            logger.info("Cargando modelo CLIP text: %s (%s)", self._model_id, resolve_device(self._device))
            from fastembed import TextEmbedding
            self._model = TextEmbedding(
                model_name=self._model_id,
                cache_dir=self._cache_dir,
                threads=self._threads,
                **_load_options(self._device),
            )
        return self._model

    def embed_query(self, text: str) -> List[float]:
        model = self._ensure_loaded()
        try:
            return next(model.query_embed([text])).tolist()
        except (AttributeError, NotImplementedError, Exception):
            return next(model.embed([text])).tolist()


class FastEmbedPassageReranker:
    """Cross-encoder de FastEmbed (ONNX); carga diferida en la primera consulta."""

    def __init__(
        self,
        model_id: str,
        cache_dir: Optional[str] = None,
        threads: Optional[int] = None,
        device: str = "auto",
    ) -> None:
        self._model_id = model_id
        self._cache_dir = cache_dir
        self._threads = threads
        self._device = device
        self._model = None

    @property
    def model_id(self) -> str:
        return self._model_id

    def _ensure_loaded(self):
        if self._model is None:
            logger.info("Cargando reordenador: %s (%s)", self._model_id, resolve_device(self._device))
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            self._model = TextCrossEncoder(
                model_name=self._model_id,
                cache_dir=self._cache_dir,
                threads=self._threads,
                **_load_options(self._device),
            )
        return self._model

    def rerank(self, query: str, passages: List[str]) -> List[float]:
        if not passages:
            return []
        return [float(score) for score in self._ensure_loaded().rerank(query, list(passages))]
