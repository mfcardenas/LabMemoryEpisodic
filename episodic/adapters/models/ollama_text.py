"""Modelo de texto servido por Ollama (``/api/chat`` con salida JSON por esquema)."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests

from episodic.ports.text_composer import Composition, TextComposerError

logger = logging.getLogger(__name__)


class OllamaTextComposer:
    """Mismo contrato HTTP que el adaptador de visión, sin imágenes.

    ``reasoning`` (low | medium | high) se pasa en el mensaje de sistema, como
    espera gpt-oss; otros modelos lo ignoran. ``num_ctx`` fija la ventana de
    contexto (Ollama usa 4096 por defecto, corto para un episodio largo).
    """

    def __init__(
        self,
        model: str = "gpt-oss:latest",
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 600.0,
        temperature: float = 0.2,
        num_ctx: int = 16384,
        reasoning: str = "low",
        think: bool | str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.reasoning = reasoning
        self.think = think
        self.session = session or requests.Session()

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    def compose(
        self, prompt: str, *, system: str = "", schema: Optional[dict[str, Any]] = None
    ) -> Composition:
        messages = []
        system_text = "\n".join(part for part in (f"Reasoning: {self.reasoning}" if self.reasoning else "", system) if part)
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if schema is not None:
            payload["format"] = schema
        if self.think is not None:
            payload["think"] = self.think
        try:
            response = self.session.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise TextComposerError(f"Ollama no responde en {self.base_url}: {exc}") from exc
        if response.status_code != 200:
            raise TextComposerError(
                f"Ollama devolvió {response.status_code} para {self._model}: {response.text[:300]}"
            )
        try:
            body = response.json()
            content = body["message"]["content"]
            data = json.loads(content) if schema is not None else {"text": content}
        except (KeyError, ValueError, TypeError) as exc:
            raise TextComposerError(f"Respuesta no válida de {self._model}: {exc}") from exc
        if not isinstance(data, dict):
            raise TextComposerError(f"{self._model} no devolvió un objeto JSON")
        usage = {
            key: body.get(key)
            for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration")
            if body.get(key) is not None
        }
        return Composition(data=data, model_id=self.model_id, usage=usage)
