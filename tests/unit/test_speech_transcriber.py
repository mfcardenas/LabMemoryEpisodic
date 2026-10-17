"""Contrato del transcriptor HTTP compatible con OpenAI, sin red."""
from __future__ import annotations

import json

import pytest
import requests

from episodic.adapters.speech.openai_http import OpenAICompatibleTranscriber, parse_verbose_json
from episodic.ports.speech import SpeechTranscriberError


class FakeResponse:
    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("no es JSON")
        return self._body


class FakeSession:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.sent = None

    def post(self, url, data, files, headers, timeout):
        self.sent = {
            "url": url,
            "data": data,
            "files": files,
            "headers": headers,
            "timeout": timeout,
        }
        if self.error:
            raise self.error
        return self.response


VERBOSE = {
    "language": "es",
    "duration": 12.0,
    "text": "Mira, esto está agarrotado. Vale.",
    "segments": [
        {"start": 0.0, "end": 5.36, "text": " Mira, esto está agarrotado.", "avg_logprob": -0.2,
         "no_speech_prob": 0.01},
        {"start": 5.36, "end": 6.0, "text": "   "},
        {"start": 6.0, "end": 7.84, "text": " Vale."},
    ],
}


def _transcriber(session, **kwargs) -> OpenAICompatibleTranscriber:
    return OpenAICompatibleTranscriber(
        base_url="http://speech:8000/", model="modelo-x", session=session, **kwargs
    )


def test_request_follows_openai_contract_with_configured_parameters() -> None:
    session = FakeSession(FakeResponse(200, VERBOSE))
    transcriber = _transcriber(
        session, timeout_seconds=42, api_key="secreto", prompt="latiguillo, radial"
    )

    transcript = transcriber.transcribe(b"RIFF...", "pista.wav", "es")

    assert session.sent["url"] == "http://speech:8000/v1/audio/transcriptions"
    assert session.sent["data"]["model"] == "modelo-x"
    assert session.sent["data"]["response_format"] == "verbose_json"
    assert session.sent["data"]["language"] == "es"
    assert session.sent["data"]["prompt"] == "latiguillo, radial"
    assert session.sent["files"] == {"file": ("pista.wav", b"RIFF...")}
    assert session.sent["headers"] == {"Authorization": "Bearer secreto"}
    assert session.sent["timeout"] == 42
    assert transcriber.model_id == "modelo-x"
    assert transcriber.backend == "http"
    # El segmento sin texto se descarta; los tiempos se conservan.
    assert [(item.start_seconds, item.end_seconds, item.text) for item in transcript.segments] == [
        (0.0, 5.36, "Mira, esto está agarrotado."),
        (6.0, 7.84, "Vale."),
    ]
    assert transcript.segments[0].avg_logprob == -0.2
    assert transcript.language == "es"
    assert transcript.text == "Mira, esto está agarrotado. Vale."


def test_language_and_key_are_optional() -> None:
    session = FakeSession(FakeResponse(200, VERBOSE))

    _transcriber(session).transcribe(b"x", "a.wav", None)

    assert "language" not in session.sent["data"]
    assert "prompt" not in session.sent["data"]
    assert session.sent["headers"] == {}


@pytest.mark.parametrize(
    "session, message",
    [
        (FakeSession(error=requests.ConnectionError("rechazada")), "no responde"),
        (FakeSession(FakeResponse(404, "model not found")), "404"),
        (FakeSession(FakeResponse(200, "texto plano")), "no JSON"),
        (FakeSession(FakeResponse(200, {"text": "sin segmentos"})), "segmentos con tiempos"),
    ],
)
def test_failures_are_explicit(session, message) -> None:
    with pytest.raises(SpeechTranscriberError, match=message):
        _transcriber(session).transcribe(b"x", "a.wav", "es")


def test_silence_yields_empty_transcript() -> None:
    transcript = parse_verbose_json({"text": "", "segments": [], "language": "es"})

    assert transcript.segments == ()


def test_segment_without_times_is_rejected() -> None:
    with pytest.raises(SpeechTranscriberError, match="tiempos"):
        parse_verbose_json({"segments": [{"text": "hola"}]})


def test_url_and_model_are_required() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleTranscriber(base_url="", model="m")
    with pytest.raises(ValueError):
        OpenAICompatibleTranscriber(base_url="http://x", model="")
