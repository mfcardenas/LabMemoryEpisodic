"""Contrato del modelo de texto por Ollama sin red: petición, parseo y errores."""
from __future__ import annotations

import json

import pytest
import requests

from episodic.adapters.models.ollama_text import OllamaTextComposer
from episodic.ports.text_composer import TextComposerError


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body


class FakeSession:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.sent = None

    def post(self, url, json, timeout):
        self.sent = {"url": url, "json": json, "timeout": timeout}
        if self.error:
            raise self.error
        return self.response


SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


def test_request_carries_reasoning_system_prompt_schema_and_context() -> None:
    session = FakeSession(FakeResponse(200, {"message": {"content": '{"title": "Hola"}'}, "eval_count": 12, "prompt_eval_count": 40}))
    composer = OllamaTextComposer(model="gpt-oss:latest", base_url="http://ollama:11434/", num_ctx=8192, reasoning="medium", session=session)

    result = composer.compose("Resume esto", system="Eres el redactor.", schema=SCHEMA)

    assert result.data == {"title": "Hola"} and result.model_id == "ollama:gpt-oss:latest"
    assert result.usage == {"prompt_eval_count": 40, "eval_count": 12}
    sent = session.sent["json"]
    assert session.sent["url"] == "http://ollama:11434/api/chat"
    assert sent["model"] == "gpt-oss:latest" and sent["stream"] is False and sent["format"] == SCHEMA
    assert sent["options"] == {"temperature": 0.2, "num_ctx": 8192}
    assert sent["messages"][0] == {"role": "system", "content": "Reasoning: medium\nEres el redactor."}
    assert sent["messages"][1] == {"role": "user", "content": "Resume esto"}
    assert "think" not in sent


def test_without_schema_the_text_is_returned_as_is() -> None:
    session = FakeSession(FakeResponse(200, {"message": {"content": "texto libre"}}))
    result = OllamaTextComposer(session=session).compose("hola")
    assert result.data == {"text": "texto libre"} and "format" not in session.sent["json"]


@pytest.mark.parametrize(
    "session, message",
    [
        (FakeSession(error=requests.ConnectionError("rechazada")), "no responde"),
        (FakeSession(FakeResponse(500, {"error": "size overflows"})), "devolvió 500"),
        (FakeSession(FakeResponse(200, {"message": {"content": "no es json"}})), "no válida"),
        (FakeSession(FakeResponse(200, {"message": {"content": "[1, 2]"}})), "objeto JSON"),
    ],
)
def test_failures_are_explicit(session, message) -> None:
    with pytest.raises(TextComposerError, match=message):
        OllamaTextComposer(session=session).compose("hola", schema=SCHEMA)
