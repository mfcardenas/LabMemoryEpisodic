"""Contrato del adaptador Ollama sin red: petición, parseo y errores explícitos."""
from __future__ import annotations

import base64
import json

import pytest
import requests

from episodic.adapters.models.ollama_vision import OllamaVisionDescriber
from episodic.ports.vision import SegmentContext, VisionDescriberError

CONTEXT = SegmentContext(
    segment_index=1,
    segment_count=3,
    start_offset_seconds=4.0,
    end_offset_seconds=9.5,
    video_duration_seconds=20.0,
    frame_offsets_seconds=(4.0, 7.0, 9.0),
    previous_summary="Una persona retira la rueda trasera.",
)


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


def _content(**overrides) -> dict:
    payload = {
        "title": "Aplica lubricante a la tuerca",
        "summary": "La persona rocía lubricante sobre la tuerca agarrotada.",
        "detailed_description": "Una persona con guantes negros rocía un spray rojo...",
        "scene": "taller con luz artificial",
        "actions": ["rociar lubricante", " "],
        "actors": [{"label": "persona 1", "description": "guantes negros"}],
        "objects": [{"label": "spray", "attributes": ["rojo"]}],
        "visible_text": ["WD-40"],
        "changes_from_previous": "La rueda ya no está montada.",
        "uncertainty": "No se distingue el tamaño de la tuerca.",
    }
    payload.update(overrides)
    return {"message": {"content": json.dumps(payload)}}


def test_request_uses_schema_images_and_context() -> None:
    session = FakeSession(FakeResponse(200, _content()))
    describer = OllamaVisionDescriber(model="qwen3.5:4b", session=session)

    description = describer.describe_segment([b"jpeg-1", b"jpeg-2"], CONTEXT)

    sent = session.sent["json"]
    assert session.sent["url"] == "http://127.0.0.1:11434/api/chat"
    assert sent["model"] == "qwen3.5:4b" and sent["stream"] is False and sent["think"] is False
    assert sent["format"]["required"][:3] == ["title", "summary", "detailed_description"]
    message = sent["messages"][0]
    assert message["images"] == [base64.b64encode(b"jpeg-1").decode(), base64.b64encode(b"jpeg-2").decode()]
    assert "tramo 2 de 3" in message["content"]
    assert "Una persona retira la rueda trasera." in message["content"]
    assert description.title == "Aplica lubricante a la tuerca"
    assert description.actions == ("rociar lubricante",)
    assert description.visible_text == ("WD-40",)
    assert describer.model_id == "ollama:qwen3.5:4b"


def test_unreachable_or_invalid_model_raises_explicit_error() -> None:
    offline = OllamaVisionDescriber(session=FakeSession(error=requests.ConnectionError("refused")))
    with pytest.raises(VisionDescriberError, match="no responde"):
        offline.describe_segment([b"jpeg"], CONTEXT)

    missing = OllamaVisionDescriber(session=FakeSession(FakeResponse(404, {"error": "model not found"})))
    with pytest.raises(VisionDescriberError, match="404"):
        missing.describe_segment([b"jpeg"], CONTEXT)

    empty = OllamaVisionDescriber(session=FakeSession(FakeResponse(200, _content(detailed_description=""))))
    with pytest.raises(VisionDescriberError, match="descripción detallada"):
        empty.describe_segment([b"jpeg"], CONTEXT)
