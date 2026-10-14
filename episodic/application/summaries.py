"""Memoria jerárquica: resúmenes de episodio, de día y de entorno.

Cada nivel se redacta solo a partir del inferior (eventos → episodios → días)
con el modelo de texto, y se guarda como texto generado con procedencia y
revisión. Permite razonar sobre miles de episodios sin leerlos: un agente
baja de entorno a día, de día a episodio y de episodio al evento exacto.

Las citas de la narrativa se **verifican** contra la memoria: una cita a un
evento que no existe se elimina y queda anotada en ``metadata``.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Optional

from episodic.application.progress import note
from episodic.domain.captures import Job, StageState
from episodic.domain.summaries import Summary
from episodic.ports.repository import EpisodicRepository
from episodic.ports.text_composer import TextComposer, TextComposerError

logger = logging.getLogger(__name__)

PROMPT_VERSION = "summaries-es-v1"
SUMMARY_JOB_KIND = "summarize"
SUMMARY_STAGES: tuple[tuple[str, str], ...] = (("compose", "Redacción"), ("index", "Indexación"))
DIGEST_JOB_KIND = "digest"
DIGEST_STAGES: tuple[tuple[str, str], ...] = (("day", "Resumen del día"), ("environment", "Resumen del entorno"))

_NAMESPACE = uuid.UUID("7c1e4f52-9a3b-4d6e-8f10-2b5c7d9e1a34")
_EVENT_CITE = re.compile(r"\[ev (\d+) · (\d{1,2}):(\d{2})\]")
_EPISODE_CITE = re.compile(r"\[ep:([^\]]+)\]")
_DAY_CITE = re.compile(r"\[día (\d{4}-\d{2}-\d{2})\]")

_SYSTEM = (
    "Eres el redactor de una memoria episódica. Escribes en español, claro y concreto, "
    "SOLO a partir de los datos que recibes: no inventas hechos, nombres, horas ni intenciones. "
    "Si algo no consta, dilo en open_questions. Cada afirmación de la narrativa cita su origen "
    "con la marca indicada, tal cual aparece en los datos."
)

_EPISODE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "narrative": {"type": "string"},
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_sequence": {"type": "integer"},
                    "offset_seconds": {"type": "number"},
                    "text": {"type": "string"},
                },
                "required": ["event_sequence", "offset_seconds", "text"],
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "narrative", "highlights", "open_questions"],
}
_DAY_SCHEMA = {
    **_EPISODE_SCHEMA,
    "properties": {
        **_EPISODE_SCHEMA["properties"],
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"episode_external_id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["episode_external_id", "text"],
            },
        },
    },
}
_ENVIRONMENT_SCHEMA = {
    **_EPISODE_SCHEMA,
    "properties": {
        **_EPISODE_SCHEMA["properties"],
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"day": {"type": "string"}, "text": {"type": "string"}},
                "required": ["day", "text"],
            },
        },
    },
}


def _clock(seconds: Optional[float]) -> str:
    total = int(seconds or 0)
    return f"{total // 60:02d}:{total % 60:02d}"


def day_key(environment_id: str, day: datetime) -> str:
    return f"{environment_id}|{day:%Y-%m-%d}"


@dataclass
class SummaryInputs:
    prompt: str
    schema: dict
    # Citas válidas, para verificar lo que devuelva el modelo.
    valid_events: dict[int, float]
    valid_episodes: set[str]
    valid_days: set[str]
    metadata: dict[str, Any]


class SummaryService:
    def __init__(
        self,
        repository: EpisodicRepository,
        composer: Optional[TextComposer],
        *,
        timezone: Optional[tzinfo] = None,
        max_input_chars: int = 16000,
        environment_days: int = 30,
    ) -> None:
        self.repository = repository
        self.composer = composer
        self.timezone = timezone or timezone_utc()
        self.max_input_chars = max_input_chars
        self.environment_days = environment_days

    @property
    def enabled(self) -> bool:
        return self.composer is not None

    # -- Entradas -------------------------------------------------------------------

    def episode_inputs(self, episode_id: str) -> Optional[SummaryInputs]:
        episode = self.repository.get_episode(episode_id)
        if episode is None:
            return None
        events = self.repository.list_events(episode.id)
        passages = self.repository.list_passages_for_episode(episode.id)
        lines = [
            f"Captura: {episode.title or episode.external_id or episode.id}",
            f"Ambiente: {', '.join(episode.environment_ids) or 'desconocido'}",
            f"Inicio: {_local(episode.start_time, self.timezone)} · Fin: {_local(episode.end_time, self.timezone)}",
            "",
            "EVENTOS DEL VÍDEO (cita cada uno como [ev N · mm:ss]):",
        ]
        valid_events: dict[int, float] = {}
        for event in events:
            seq = int(event.sequence_number or 0)
            start = float(event.start_offset_seconds or 0.0)
            valid_events[seq] = start
            attributes = event.attributes or {}
            lines.append(f"[ev {seq} · {_clock(start)}] ({_clock(start)}–{_clock(event.end_offset_seconds)}) {event.title}")
            if event.summary:
                lines.append(f"  resumen: {event.summary}")
            if event.description:
                lines.append(f"  descripción: {event.description}")
            for key, label in (("actions", "acciones"), ("visible_text", "texto visible")):
                values = attributes.get(key) or []
                if values:
                    lines.append(f"  {label}: {', '.join(str(item) for item in values)}")
            objects = [item.get("label") if isinstance(item, dict) else str(item) for item in attributes.get("objects") or []]
            if objects:
                lines.append(f"  objetos: {', '.join(str(item) for item in objects if item)}")
            for entry in attributes.get("speech") or []:
                if isinstance(entry, dict) and entry.get("text"):
                    lines.append(f"  se dijo ({_clock(entry.get('start_offset_seconds'))}): «{entry['text']}»")
        loose = [item for item in passages if item.kind == "speech_segment"]
        if loose:
            lines += ["", "HABLA FUERA DE LOS EVENTOS:"]
            lines += [f"  ({_clock(item.start_offset_seconds)}) «{item.text}»" for item in loose]
        documents = [item for item in passages if item.kind in {"document_text_fragment", "text_fragment", "structured_text_fragment"}]
        if documents:
            lines += ["", "DOCUMENTOS ADJUNTOS (fragmentos):"]
            lines += [f"  {'pág. ' + str(item.page_number) + ': ' if item.page_number else ''}{item.text}" for item in documents]
        if not events and not loose and not documents:
            return None
        prompt = self._trim("\n".join(lines)) + (
            "\n\nRedacta: title (≤ 12 palabras), summary (2–3 frases), narrative (párrafos en orden cronológico, "
            "cada hecho con su marca [ev N · mm:ss]), highlights (los 3–6 momentos clave con event_sequence y "
            "offset_seconds exactos de la lista) y open_questions (lo que no consta)."
        )
        return SummaryInputs(prompt, _EPISODE_SCHEMA, valid_events, set(), set(),
                             {"episode_id": episode.id, "events": len(events), "passages": len(passages)})

    def day_inputs(self, environment_id: str, day: datetime) -> Optional[SummaryInputs]:
        day = day.astimezone(self.timezone).replace(hour=0, minute=0, second=0, microsecond=0)
        start, end = day.astimezone(timezone.utc), (day + timedelta(days=1)).astimezone(timezone.utc)
        episodes = [
            item for item in self.repository.list_episodes_for_environment(environment_id)
            if item.is_visible and item.start_time and start <= item.start_time < end
        ]
        episodes.sort(key=lambda item: item.start_time)
        if not episodes:
            return None
        lines = [f"Entorno: {environment_id}", f"Día: {day:%Y-%m-%d} ({self._tz_name()})", "",
                 "EPISODIOS DEL DÍA (cita cada uno como [ep:<clave>]):"]
        valid: set[str] = set()
        for episode in episodes:
            key = episode.external_id or episode.id
            valid.add(key)
            current = self.repository.get_current_summary("episode", episode.id)
            lines.append(f"[ep:{key}] {_local(episode.start_time, self.timezone)} — {current.title if current else episode.title}")
            lines.append(f"  {current.summary if current else (episode.indexable_text[:400] or 'sin resumen')}")
        prompt = self._trim("\n".join(lines)) + (
            "\n\nRedacta: title, summary (2–3 frases sobre el día), narrative (qué pasó a lo largo del día, cada "
            "hecho con su marca [ep:<clave>]), highlights (episodios clave con episode_external_id exacto) y open_questions."
        )
        return SummaryInputs(prompt, _DAY_SCHEMA, {}, valid, set(),
                             {"environment_id": environment_id, "day": f"{day:%Y-%m-%d}", "episodes": len(episodes)})

    def environment_inputs(self, environment_id: str, now: Optional[datetime] = None) -> Optional[SummaryInputs]:
        digests = self.repository.list_current_summaries("day", prefix=f"{environment_id}|")
        if not digests:
            return None
        digests = sorted(digests, key=lambda item: item.scope_key)[-self.environment_days:]
        lines = [f"Entorno: {environment_id}", f"Días con actividad: {len(digests)}", "",
                 "RESÚMENES DIARIOS (cita cada uno como [día AAAA-MM-DD]):"]
        valid: set[str] = set()
        for digest in digests:
            day = digest.scope_key.split("|", 1)[1]
            valid.add(day)
            lines.append(f"[día {day}] {digest.title}")
            lines.append(f"  {digest.summary}")
        prompt = self._trim("\n".join(lines)) + (
            "\n\nRedacta: title, summary (qué caracteriza a este entorno y qué suele ocurrir), narrative (evolución "
            "por días, cada hecho con su marca [día AAAA-MM-DD]), highlights (días clave con day exacto) y open_questions."
        )
        return SummaryInputs(prompt, _ENVIRONMENT_SCHEMA, {}, set(), valid,
                             {"environment_id": environment_id, "days": len(digests)})

    # -- Redacción ------------------------------------------------------------------

    def summarize(self, scope: str, scope_key: str, *, force: bool = False, reason: str = "") -> Optional[Summary]:
        """Redacta (o reutiliza) el resumen vigente de ese ámbito.

        Sin modelo configurado devuelve ``None``. Si la entrada no cambió y el
        modelo y el prompt son los mismos, no se vuelve a llamar al modelo salvo
        ``force``; regenerar crea ``revision + 1``.
        """
        if self.composer is None:
            return None
        inputs = self._inputs(scope, scope_key)
        if inputs is None:
            return None
        input_hash = hashlib.sha256(inputs.prompt.encode("utf-8")).hexdigest()
        current = self.repository.get_current_summary(scope, scope_key)
        if (
            current is not None
            and not force
            and current.input_hash == input_hash
            and current.model_id == self.composer.model_id
            and current.prompt_version == PROMPT_VERSION
        ):
            return current
        revision = (current.revision + 1) if current else 1
        note(f"redactando resumen de {scope} {scope_key} (revisión {revision}) con {self.composer.model_id}")
        composition = self.composer.compose(inputs.prompt, system=_SYSTEM, schema=inputs.schema)
        data = composition.data
        highlights, dropped = self._verify_highlights(scope, data.get("highlights") or [], inputs)
        narrative, dropped_cites = self._verify_narrative(scope, str(data.get("narrative") or ""), inputs)
        summary = Summary(
            id=str(uuid.uuid5(_NAMESPACE, f"{scope}|{scope_key}|{revision}")),
            scope=scope,
            scope_key=scope_key,
            revision=revision,
            title=str(data.get("title") or "").strip() or (current.title if current else scope_key),
            summary=str(data.get("summary") or "").strip(),
            narrative=narrative,
            highlights=highlights,
            open_questions=[str(item).strip() for item in data.get("open_questions") or [] if str(item).strip()],
            model_id=composition.model_id,
            prompt_version=PROMPT_VERSION,
            input_hash=input_hash,
            metadata={
                **inputs.metadata,
                "provenance_kind": "model_generated",
                "reason": reason,
                "usage": composition.usage,
                "dropped_highlights": dropped,
                "dropped_citations": dropped_cites,
                "input_chars": len(inputs.prompt),
            },
        )
        self.repository.save_summary(summary)
        return summary

    def _inputs(self, scope: str, scope_key: str) -> Optional[SummaryInputs]:
        if scope == "episode":
            return self.episode_inputs(scope_key)
        if scope == "day":
            environment_id, _, day = scope_key.partition("|")
            return self.day_inputs(environment_id, datetime.fromisoformat(day).replace(tzinfo=self.timezone))
        if scope == "environment":
            return self.environment_inputs(scope_key)
        raise ValueError(f"Ámbito desconocido: {scope}")

    # -- Verificación de citas ---------------------------------------------------------

    @staticmethod
    def _verify_highlights(scope: str, items: list, inputs: SummaryInputs) -> tuple[list[dict], list]:
        kept, dropped = [], []
        for item in items:
            if not isinstance(item, dict):
                dropped.append(item)
                continue
            text = str(item.get("text") or "").strip()
            if scope == "episode":
                seq = item.get("event_sequence")
                if isinstance(seq, (int, float)) and int(seq) in inputs.valid_events and text:
                    kept.append({"event_sequence": int(seq), "offset_seconds": inputs.valid_events[int(seq)], "text": text})
                    continue
            elif scope == "day":
                key = str(item.get("episode_external_id") or "")
                if key in inputs.valid_episodes and text:
                    kept.append({"episode_external_id": key, "text": text})
                    continue
            else:
                day = str(item.get("day") or "")
                if day in inputs.valid_days and text:
                    kept.append({"day": day, "text": text})
                    continue
            dropped.append(item)
        return kept, dropped

    @staticmethod
    def _verify_narrative(scope: str, narrative: str, inputs: SummaryInputs) -> tuple[str, list[str]]:
        dropped: list[str] = []

        def check_event(match: re.Match) -> str:
            seq = int(match.group(1))
            if seq in inputs.valid_events:
                return f"[ev {seq} · {_clock(inputs.valid_events[seq])}]"
            dropped.append(match.group(0))
            return ""

        def check_episode(match: re.Match) -> str:
            if match.group(1) in inputs.valid_episodes:
                return match.group(0)
            dropped.append(match.group(0))
            return ""

        def check_day(match: re.Match) -> str:
            if match.group(1) in inputs.valid_days:
                return match.group(0)
            dropped.append(match.group(0))
            return ""

        if scope == "episode":
            narrative = _EVENT_CITE.sub(check_event, narrative)
        elif scope == "day":
            narrative = _EPISODE_CITE.sub(check_episode, narrative)
        else:
            narrative = _DAY_CITE.sub(check_day, narrative)
        return re.sub(r"[ \t]{2,}", " ", narrative).strip(), dropped

    # -- Trabajos -------------------------------------------------------------------

    def run_summarize_job(self, job: Job, report, index: Optional[Callable[[str], None]] = None) -> dict:
        """Manejador del trabajo «summarize»: target_id = «<scope>|<clave>»."""
        scope, _, key = job.target_id.partition("|")
        report("compose", StageState.RUNNING, "")
        try:
            summary = self.summarize(scope, key, force=True, reason="manual")
        except TextComposerError as exc:
            report("compose", StageState.FAILED, str(exc))
            raise
        if summary is None:
            report("compose", StageState.COMPLETED, "sin modelo de texto o sin contenido que resumir")
            report("index", StageState.COMPLETED, "nada que indexar")
            return {"summary_id": None}
        report("compose", StageState.COMPLETED, f"revisión {summary.revision} · {summary.model_id}")
        report("index", StageState.RUNNING, "")
        if scope == "episode" and index is not None:
            index(key)
            report("index", StageState.COMPLETED, "resumen indexado como pasaje")
        else:
            report("index", StageState.COMPLETED, "los resúmenes de día y entorno se consultan por navegación")
        return {"summary_id": summary.id, "revision": summary.revision}

    def run_digest_job(self, job: Job, report) -> dict:
        """Manejador del trabajo «digest»: target_id = «<entorno>|AAAA-MM-DD»."""
        environment_id, _, day = job.target_id.partition("|")
        result: dict[str, Any] = {}
        report("day", StageState.RUNNING, "")
        try:
            digest = self.summarize("day", job.target_id, reason="captura nueva")
            result["day"] = digest.id if digest else None
            report("day", StageState.COMPLETED, f"revisión {digest.revision}" if digest else "sin episodios ese día o sin modelo")
            report("environment", StageState.RUNNING, "")
            overview = self.summarize("environment", environment_id, reason="captura nueva")
            result["environment"] = overview.id if overview else None
            report("environment", StageState.COMPLETED, f"revisión {overview.revision}" if overview else "sin resúmenes diarios o sin modelo")
        except TextComposerError as exc:
            report("environment" if "day" in result else "day", StageState.FAILED, str(exc))
            raise
        return result

    # -- Utilidades -------------------------------------------------------------------

    def _trim(self, text: str) -> str:
        if len(text) <= self.max_input_chars:
            return text
        return text[: self.max_input_chars] + "\n[… entrada recortada por longitud …]"

    def _tz_name(self) -> str:
        return getattr(self.timezone, "key", None) or str(self.timezone)


def timezone_utc() -> tzinfo:
    return timezone.utc


def _local(value: Optional[datetime], tz: tzinfo) -> str:
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M") if value else "desconocido"
