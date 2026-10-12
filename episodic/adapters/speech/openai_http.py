"""Transcripción mediante un servicio HTTP compatible con la API de OpenAI.

El contrato es ``POST {base_url}/v1/audio/transcriptions`` con
``response_format=verbose_json``, que devuelve los segmentos con sus tiempos.
Lo implementan Speaches (faster-whisper), whisper.cpp server, vLLM y la propia
API de OpenAI, de modo que el servicio y el modelo se cambian por
configuración sin tocar el código.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from episodic.ports.speech import SpeechTranscriberError, Transcript, TranscriptSegment

logger = logging.getLogger(__name__)


class OpenAICompatibleTranscriber:
    backend = "http"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 600.0,
        api_key: str = "",
        prompt: str = "",
        temperature: float = 0.0,
        session: requests.Session | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("La URL del servicio de transcripción es obligatoria")
        if not model:
            raise ValueError("El modelo de transcripción es obligatorio")
        self.base_url = base_url.rstrip("/")
        self._model = model
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key
        self.prompt = prompt
        self.temperature = temperature
        self.session = session or requests.Session()

    @property
    def model_id(self) -> str:
        return self._model

    def list_models(self) -> list[str]:
        """Modelos que el servicio declara disponibles (``GET /v1/models``)."""
        try:
            response = self.session.get(
                f"{self.base_url}/v1/models", headers=self._headers(), timeout=30
            )
        except requests.RequestException as exc:
            raise SpeechTranscriberError(
                f"El servicio de transcripción no responde en {self.base_url}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise SpeechTranscriberError(
                f"GET /v1/models devolvió {response.status_code}: {response.text[:300]}"
            )
        return [str(item.get("id")) for item in response.json().get("data", [])]

    def download_model(self) -> str:
        """Pide al servicio que descargue el modelo configurado.

        Es una extensión de Speaches (``POST /v1/models/{id}``); otros
        servicios compatibles gestionan sus modelos por su cuenta.
        """
        try:
            response = self.session.post(
                f"{self.base_url}/v1/models/{self._model}",
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise SpeechTranscriberError(
                f"El servicio de transcripción no responde en {self.base_url}: {exc}"
            ) from exc
        if response.status_code not in {200, 201}:
            raise SpeechTranscriberError(
                f"No se pudo descargar {self._model}: {response.status_code} {response.text[:300]}"
            )
        return response.text.strip()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def transcribe(
        self, audio: bytes, filename: str, language: str | None = None
    ) -> Transcript:
        data: dict[str, Any] = {
            "model": self._model,
            "response_format": "verbose_json",
            "temperature": str(self.temperature),
        }
        if language:
            data["language"] = language
        if self.prompt:
            data["prompt"] = self.prompt
        headers = self._headers()
        try:
            response = self.session.post(
                f"{self.base_url}/v1/audio/transcriptions",
                data=data,
                files={"file": (filename, audio)},
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise SpeechTranscriberError(
                f"El servicio de transcripción no responde en {self.base_url}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise SpeechTranscriberError(
                f"El servicio de transcripción devolvió {response.status_code} "
                f"para {self._model}: {response.text[:300]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SpeechTranscriberError(f"Respuesta no JSON de {self._model}: {exc}") from exc
        return parse_verbose_json(payload)


def parse_verbose_json(payload: dict[str, Any]) -> Transcript:
    """Normaliza ``verbose_json``; descarta segmentos sin texto."""
    if not isinstance(payload, dict):
        raise SpeechTranscriberError("La transcripción no es un objeto JSON")
    raw_segments = payload.get("segments")
    segments: list[TranscriptSegment] = []
    if isinstance(raw_segments, list):
        for item in raw_segments:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SpeechTranscriberError(f"Segmento sin tiempos válidos: {item}") from exc
            segments.append(
                TranscriptSegment(
                    start_seconds=start,
                    end_seconds=max(end, start),
                    text=text,
                    avg_logprob=_optional_float(item.get("avg_logprob")),
                    no_speech_prob=_optional_float(item.get("no_speech_prob")),
                )
            )
    elif str(payload.get("text") or "").strip():
        # Un servicio que no devuelve segmentos no permite alinear con eventos.
        raise SpeechTranscriberError(
            "El servicio no devolvió segmentos con tiempos (¿admite verbose_json?)"
        )
    duration = _optional_float(payload.get("duration"))
    return Transcript(
        segments=tuple(segments),
        language=payload.get("language"),
        duration_seconds=duration,
        raw={key: payload[key] for key in ("language", "duration") if key in payload},
    )


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
