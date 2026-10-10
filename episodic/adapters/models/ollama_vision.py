"""Descripción de segmentos de vídeo con un modelo de visión servido por Ollama."""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Sequence

import requests

from episodic.ports.vision import SegmentContext, SegmentDescription, VisionDescriberError

logger = logging.getLogger(__name__)

PROMPT_VERSION = "video-event-es-v1"

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "detailed_description": {"type": "string"},
        "scene": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "string"}},
        "actors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["label", "description"],
            },
        },
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "attributes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "attributes"],
            },
        },
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "changes_from_previous": {"type": "string"},
        "uncertainty": {"type": "string"},
    },
    "required": [
        "title",
        "summary",
        "detailed_description",
        "scene",
        "actions",
        "actors",
        "objects",
        "visible_text",
        "changes_from_previous",
        "uncertainty",
    ],
}

_INSTRUCTIONS = """Eres el sistema de percepción de una memoria episódica. Recibes {count} fotograma(s) \
consecutivos de un mismo tramo de vídeo, del segundo {start:.1f} al {end:.1f} \
(tramo {position} de {total}). Los fotogramas corresponden a los segundos: {offsets}.
{previous}
Describe en {language} SOLO lo que se ve. No inventes intenciones, nombres ni marcas que no sean legibles.
- title: frase corta con la acción principal (máx. 10 palabras).
- summary: una frase que resuma el tramo.
- detailed_description: varios párrafos muy detallados: entorno, personas y su aspecto, qué hacen \
en orden temporal, herramientas y objetos con su estado, posición relativa, cambios a lo largo del tramo.
- scene: tipo de lugar e iluminación.
- actions: verbos en infinitivo con su objeto ("aflojar tuerca").
- actors: cada persona visible con una etiqueta neutra ("persona 1") y su descripción visual.
- objects: objetos relevantes con atributos visibles (color, material, estado).
- visible_text: texto legible tal cual aparece; lista vacía si no hay.
- changes_from_previous: qué cambia respecto al tramo anterior; vacío si es el primero.
- uncertainty: lo que no se puede asegurar con estas imágenes."""

_LANGUAGE_NAMES = {"es": "español", "en": "inglés"}


class OllamaVisionDescriber:
    """Adaptador HTTP a ``/api/chat`` con salida JSON validada por esquema."""

    def __init__(
        self,
        model: str = "qwen3.5:4b",
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 300.0,
        temperature: float = 0.2,
        session: requests.Session | None = None,
    ) -> None:
        self._model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.session = session or requests.Session()

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    def describe_segment(
        self, frames_jpeg: Sequence[bytes], context: SegmentContext
    ) -> SegmentDescription:
        if not frames_jpeg:
            raise VisionDescriberError("Un segmento requiere al menos un fotograma")
        previous = (
            f"Resumen del tramo anterior: {context.previous_summary}\n"
            if context.previous_summary
            else ""
        )
        prompt = _INSTRUCTIONS.format(
            count=len(frames_jpeg),
            start=context.start_offset_seconds,
            end=context.end_offset_seconds,
            position=context.segment_index + 1,
            total=context.segment_count,
            offsets=", ".join(f"{value:.1f}" for value in context.frame_offsets_seconds),
            previous=previous,
            language=_LANGUAGE_NAMES.get(context.language, context.language),
        )
        payload = {
            "model": self._model,
            "stream": False,
            "think": False,
            "format": _RESPONSE_SCHEMA,
            "options": {"temperature": self.temperature},
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(item).decode("ascii") for item in frames_jpeg],
                }
            ],
        }
        try:
            response = self.session.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_seconds
            )
        except requests.RequestException as exc:
            raise VisionDescriberError(
                f"Ollama no responde en {self.base_url}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise VisionDescriberError(
                f"Ollama devolvió {response.status_code} para {self._model}: {response.text[:300]}"
            )
        try:
            content = response.json()["message"]["content"]
            data = json.loads(content)
        except (KeyError, ValueError, TypeError) as exc:
            raise VisionDescriberError(f"Respuesta no válida de {self._model}: {exc}") from exc
        return parse_description(data)


def parse_description(data: dict[str, Any]) -> SegmentDescription:
    """Normaliza la respuesta del modelo; tolera campos ausentes pero no texto vacío."""
    detailed = str(data.get("detailed_description") or "").strip()
    if not detailed:
        raise VisionDescriberError("El modelo no devolvió descripción detallada")
    summary = str(data.get("summary") or "").strip() or detailed.split(". ")[0]
    title = str(data.get("title") or "").strip() or summary[:80]
    return SegmentDescription(
        title=title,
        summary=summary,
        detailed_description=detailed,
        scene=str(data.get("scene") or "").strip(),
        actions=tuple(str(item).strip() for item in data.get("actions") or [] if str(item).strip()),
        actors=tuple(
            {"label": str(item.get("label", "")), "description": str(item.get("description", ""))}
            for item in data.get("actors") or []
            if isinstance(item, dict)
        ),
        objects=tuple(
            {
                "label": str(item.get("label", "")),
                "attributes": [str(value) for value in item.get("attributes") or []],
            }
            for item in data.get("objects") or []
            if isinstance(item, dict)
        ),
        visible_text=tuple(
            str(item).strip() for item in data.get("visible_text") or [] if str(item).strip()
        ),
        changes_from_previous=str(data.get("changes_from_previous") or "").strip(),
        uncertainty=str(data.get("uncertainty") or "").strip(),
        raw=data,
    )
