"""Fase D: extracción de audio y alineación del habla con los eventos de vídeo."""
from __future__ import annotations

import io
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from episodic.application.events import attach_speech
from episodic.domain.entities import Observation
from episodic.domain.processing import Event
from episodic.domain.types import ProvenanceKind

SAMPLES = Path("data_samples/phase4_input")
T0 = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)


def _event(event_id: str, start: float, end: float, asset_id: str = "video") -> Event:
    return Event(
        id=event_id,
        event_type="video_segment",
        description="tramo",
        environment_id="taller",
        start_time=T0 + timedelta(seconds=start),
        end_time=T0 + timedelta(seconds=end),
        asset_id=asset_id,
        start_offset_seconds=start,
        end_offset_seconds=end,
        producer="test",
        model_version="test-v1",
        metadata={"observation_ids": [f"obs-{event_id}"]},
    )


def _speech(
    text: str, start: float, end: float, asset_id: str = "audio", modality: str = "audio"
) -> Observation:
    return Observation(
        id=f"speech-{text}",
        content=text,
        description=text,
        modality=modality,
        observation_type="speech_segment",
        provenance_kind=ProvenanceKind.AUDIO_TRANSCRIPTION,
        environment_id="taller",
        source_id=f"taller.{modality}",
        asset_id=asset_id,
        start_time=T0 + timedelta(seconds=start),
        end_time=T0 + timedelta(seconds=end),
        attributes={"generation": {"speech_model": "modelo-x"}},
    )


def test_each_segment_goes_to_the_event_it_overlaps_most() -> None:
    events = [_event("a", 0, 5), _event("b", 5, 12)]
    speech = [_speech("uno", 0.5, 3), _speech("dos", 4, 9), _speech("fuera", 20, 22)]

    result = attach_speech(events, speech)

    assert [line["text"] for line in result[0].attributes.get("speech", [])] == ["uno"]
    # "dos" solapa 1 s con a y 4 s con b.
    assert [line["text"] for line in result[1].attributes["speech"]] == ["dos"]
    assert result[1].attributes["speech"][0]["start_offset_seconds"] == 4.0
    assert result[1].attributes["speech"][0]["speech_model"] == "modelo-x"
    assert result[1].metadata["speech_observation_ids"] == ["speech-dos"]
    # La identidad y el enlace con su observación de vídeo no cambian.
    assert result[1].id == "b"
    assert result[1].metadata["observation_ids"] == ["obs-b"]


def test_offset_shifts_only_separately_recorded_audio() -> None:
    events = [_event("a", 0, 5), _event("b", 5, 12)]
    speech = [_speech("aparte", 1, 3), _speech("pista", 1, 3, asset_id="video", modality="video")]

    result = attach_speech(events, speech, audio_offset_seconds=6.0)

    assert [line["text"] for line in result[0].attributes["speech"]] == ["pista"]
    assert [line["text"] for line in result[1].attributes["speech"]] == ["aparte"]
    assert result[1].attributes["speech"][0]["start_offset_seconds"] == 7.0


def test_track_of_another_video_is_not_attached() -> None:
    events = [_event("a", 0, 5)]

    result = attach_speech(events, [_speech("otro", 1, 3, asset_id="video-2", modality="video")])

    assert "speech" not in result[0].attributes


def test_audio_extractor_reads_mp3_and_ignores_silent_video() -> None:
    pytest.importorskip("av")
    from episodic.adapters.audio.extract import PyAVAudioExtractor

    extractor = PyAVAudioExtractor(sample_rate_hz=16000)
    audio = extractor.extract(SAMPLES / "conversacion_mecanico_15s_exact.mp3")

    assert audio is not None
    with wave.open(io.BytesIO(audio.wav), "rb") as decoded:
        assert decoded.getnchannels() == 1
        assert decoded.getframerate() == 16000
        assert decoded.getsampwidth() == 2
    assert audio.duration_seconds == pytest.approx(15.0, abs=0.2)
    # El vídeo de muestra no tiene pista de audio.
    assert extractor.extract(SAMPLES / "13653860_1080_1920_30fps.mp4") is None
