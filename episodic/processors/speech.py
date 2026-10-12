"""Audio o pista de audio de un vídeo -> segmentos de habla con tiempos (Fase D).

Cada segmento que devuelve el servicio de transcripción produce una
observación ``speech_segment`` con su intervalo exacto dentro del archivo. La
alineación con los eventos de vídeo se hace después, al derivar los eventos.
"""
from __future__ import annotations

from datetime import timedelta

from episodic.domain.types import EvidenceMediaType, EvidenceReferenceKind, ProvenanceKind
from episodic.ports.repository import EpisodicRepository
from episodic.ports.speech import AudioExtractor, SpeechTranscriber
from episodic.processors.base import BaseObservationProcessor, ObservationDraft, ResolvedTarget

SPEECH_OBSERVATION = "speech_segment"


class SpeechTranscriptionProcessor(BaseObservationProcessor):
    name = "speech-transcription-processor"
    supported_modalities = frozenset({EvidenceMediaType.AUDIO, EvidenceMediaType.VIDEO})
    processes_whole_asset = True
    """Se transcribe el archivo completo: las frases no respetan las ventanas técnicas."""

    def __init__(
        self,
        repository: EpisodicRepository,
        extractor: AudioExtractor,
        transcriber: SpeechTranscriber,
        language: str | None = None,
    ) -> None:
        super().__init__(repository)
        self.extractor = extractor
        self.transcriber = transcriber
        self.language = language or None
        # Otro servicio, modelo o idioma produce observaciones distintas.
        self.model_version = (
            f"speech-v1|{transcriber.backend}|{transcriber.model_id}"
            f"|{self.language or 'auto'}"
        )

    def _extract(self, target: ResolvedTarget) -> list[ObservationDraft]:
        path = self.original_path(target)
        audio = self.extractor.extract(path)
        if audio is None:
            # Vídeo sin pista de audio: no hay nada que transcribir.
            return []
        transcript = self.transcriber.transcribe(
            audio.wav, f"{path.stem}.wav", self.language
        )
        origin = target.window.start_time if target.window else target.start_time
        # Dentro de un vídeo, el desfase de la pista respecto al contenedor
        # mantiene el habla alineada con los fotogramas. En un audio suelto
        # solo refleja el retardo del codificador (p. ej. 25 ms en MP3).
        track_offset = (
            audio.start_offset_seconds
            if target.asset.media_type == EvidenceMediaType.VIDEO
            else 0.0
        )
        drafts: list[ObservationDraft] = []
        for index, segment in enumerate(transcript.segments):
            start_offset = track_offset + segment.start_seconds
            end_offset = track_offset + segment.end_seconds
            start_time = origin + timedelta(seconds=start_offset)
            end_time = origin + timedelta(seconds=end_offset)
            drafts.append(
                ObservationDraft(
                    description=segment.text,
                    observation_type=SPEECH_OBSERVATION,
                    attributes={
                        "segment_index": index,
                        "segment_count": len(transcript.segments),
                        "start_offset_seconds": start_offset,
                        "end_offset_seconds": end_offset,
                        "text": segment.text,
                        "avg_logprob": segment.avg_logprob,
                        "no_speech_prob": segment.no_speech_prob,
                        "language": transcript.language or self.language,
                        "generation": {
                            "speech_backend": self.transcriber.backend,
                            "speech_model": self.transcriber.model_id,
                            "requested_language": self.language,
                            "detected_language": transcript.language,
                            "audio_extractor": self.extractor.version,
                            "audio_duration_seconds": audio.duration_seconds,
                        },
                    },
                    # Whisper no ofrece una confianza calibrada: no se inventa.
                    confidence=None,
                    provenance_kind=ProvenanceKind.AUDIO_TRANSCRIPTION,
                    evidence_kind=EvidenceReferenceKind.AUDIOVISUAL_INTERVAL,
                    evidence_locator={
                        "start_time": start_time,
                        "end_time": end_time,
                        "start_offset_seconds": start_offset,
                        "end_offset_seconds": end_offset,
                    },
                    start_time=start_time,
                    end_time=end_time,
                )
            )
        return drafts
