"""Puerto del modelo de razonamiento: conversación con llamadas a herramientas.

Los mensajes siguen el formato de chat habitual (``role``/``content``, más
``tool_calls`` en los del asistente y ``tool_name`` en los de herramienta).
El adaptador real habla con Ollama; el falso sigue un guion o una política
mínima para las pruebas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


class ReasoningError(RuntimeError):
    """El modelo no está disponible o devolvió algo inutilizable."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Turn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in self.tool_calls
            ]
        return message


class ReasoningModel(Protocol):
    @property
    def model_id(self) -> str: ...

    def chat(self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None) -> Turn: ...
