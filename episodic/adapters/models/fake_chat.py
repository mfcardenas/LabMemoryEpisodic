"""Modelo de razonamiento determinista para pruebas.

Con ``script`` devuelve los turnos en orden. Sin guion sigue una política
mínima que imita a un agente razonable: primero busca momentos con la
pregunta, y con el resultado responde citando la primera referencia (o se
abstiene si no hubo nada).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from episodic.ports.reasoning import ToolCall, Turn

_HANDLE = re.compile(r'"handle":\s*"([^"]+)"')


class FakeReasoningModel:
    def __init__(self, script: Optional[list[Turn]] = None, model: str = "fake-reasoning") -> None:
        self.script = list(script or [])
        self._model = model
        self.calls: list[dict[str, Any]] = []

    @property
    def model_id(self) -> str:
        return self._model

    def chat(self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None) -> Turn:
        self.calls.append({"messages": [dict(item) for item in messages], "tools": [t["function"]["name"] for t in tools or []]})
        if self.script:
            return self.script.pop(0)
        last = messages[-1]
        if last.get("role") == "user" and tools:
            question = str(last.get("content", ""))
            return Turn(tool_calls=[ToolCall(id="call_1", name="search_moments", arguments={"query": question.split("\n")[0]})])
        if last.get("role") == "tool":
            handles = _HANDLE.findall(str(last.get("content", "")))
            if handles:
                return Turn(content=f"Según la memoria, ocurrió en el momento citado [{handles[0]}].", usage={"fake": True})
            return Turn(content="No consta en la memoria: no se encontró ningún momento relacionado.", usage={"fake": True})
        return Turn(content="No consta en la memoria.", usage={"fake": True})


def scripted_turn(content: str = "", calls: Optional[list[tuple[str, dict[str, Any]]]] = None) -> Turn:
    """Atajo para guiones: ``scripted_turn(calls=[("search_moments", {"query": "x"})])``."""
    return Turn(
        content=content,
        tool_calls=[ToolCall(id=f"call_{index}", name=name, arguments=args) for index, (name, args) in enumerate(calls or [], 1)],
    )


def tool_result_handles(message: dict[str, Any]) -> list[str]:
    try:
        data = json.loads(message.get("content") or "{}")
    except ValueError:
        return []
    return [item.get("handle") for item in data.get("items") or [] if item.get("handle")]
