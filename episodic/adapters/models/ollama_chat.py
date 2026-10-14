"""Modelo de razonamiento servido por Ollama (``/api/chat`` con ``tools``)."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

import requests

from episodic.ports.reasoning import ReasoningError, ToolCall, Turn

logger = logging.getLogger(__name__)


class OllamaReasoningModel:
    def __init__(
        self,
        model: str = "gpt-oss:latest",
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 600.0,
        temperature: float = 0.1,
        num_ctx: int = 16384,
        reasoning: str = "low",
        session: requests.Session | None = None,
    ) -> None:
        self._model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.reasoning = reasoning
        self.session = session or requests.Session()

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    def chat(self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None) -> Turn:
        prepared = list(messages)
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": prepared,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if self.reasoning:
            if self._model.startswith("gpt-oss"):
                # ``think`` fija el esfuerzo y mantiene el razonamiento fuera
                # de la respuesta (en ``message.thinking``).
                payload["think"] = self.reasoning
            elif prepared and prepared[0].get("role") == "system":
                prepared[0] = {**prepared[0], "content": f"Reasoning: {self.reasoning}\n{prepared[0].get('content', '')}"}
        if tools:
            payload["tools"] = tools
        try:
            response = self.session.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise ReasoningError(f"Ollama no responde en {self.base_url}: {exc}") from exc
        if response.status_code != 200:
            raise ReasoningError(f"Ollama devolvió {response.status_code} para {self._model}: {response.text[:300]}")
        try:
            body = response.json()
            message = body["message"]
        except (KeyError, ValueError, TypeError) as exc:
            raise ReasoningError(f"Respuesta no válida de {self._model}: {exc}") from exc
        calls: list[ToolCall] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {"_raw": arguments}
            calls.append(
                ToolCall(
                    id=str(item.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        usage = {
            key: body.get(key)
            for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration")
            if body.get(key) is not None
        }
        return Turn(
            content=str(message.get("content") or ""),
            tool_calls=calls,
            thinking=str(message.get("thinking") or ""),
            usage=usage,
        )
