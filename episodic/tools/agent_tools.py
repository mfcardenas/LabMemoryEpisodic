"""Herramientas de consulta pensadas para agentes (Fase A1).

Compactas (texto acotado por ``max_chars``), paginadas y sin barridos de
tablas enteras: cada una va a SQLite o a la búsqueda por momentos con
filtros. Son de solo lectura y devuelven el mismo sobre ``MemoryToolOutput``
que las herramientas deterministas de la Fase 8, con tres listas nuevas:
``moments`` (citables), ``summaries`` (texto generado) y ``browse``
(navegación entorno → día → episodio → evento).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Optional

from episodic.application.passage_search import PassageSearchService
from episodic.domain.passages import Passage, passage_from_event
from episodic.domain.processing import Event
from episodic.domain.retrieval import HybridSearchFilters
from episodic.domain.summaries import Summary
from episodic.domain.times import Interval, TimeSpec
from episodic.ports.repository import EpisodicRepository
from episodic.tools.contracts import (
    BrowseItemRecord,
    BrowseMemoryInput,
    GetDocumentPassagesInput,
    GetEpisodeSummaryInput,
    GetEventInput,
    GetEventNeighborsInput,
    MemoryToolOutput,
    MomentRecord,
    SearchMomentsInput,
    SummaryRecord,
)

DOCUMENT_KINDS = ("document_text_fragment", "text_fragment", "structured_text_fragment")


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _labels(items: Any) -> list[str]:
    out = []
    for item in items or ():
        if isinstance(item, dict):
            values = [item.get("label"), *(item.get("attributes") or [])]
            out.append(" · ".join(str(v) for v in values if v))
        else:
            out.append(str(item))
    return out


class AgentMemoryTools:
    """Manejadores de las herramientas de agente; los registra ``MemoryToolbox``."""

    def __init__(
        self,
        repository: EpisodicRepository,
        passage_search: Optional[PassageSearchService],
        timezone: Optional[tzinfo] = None,
    ) -> None:
        self.repository = repository
        self.passage_search = passage_search
        self.timezone = timezone or timezone_utc()

    # -- search_moments -------------------------------------------------------------

    def search_moments(self, request: SearchMomentsInput) -> MemoryToolOutput:
        if self.passage_search is None:
            raise ValueError("La búsqueda por momentos no está configurada")
        interval = None
        if request.start_time and request.end_time:
            interval = Interval(
                start=TimeSpec.from_utc(request.start_time, clock_source="memory_tool"),
                end=TimeSpec.from_utc(request.end_time, clock_source="memory_tool"),
            )
        run = self.passage_search.search(
            request.query,
            HybridSearchFilters(
                environment_ids=tuple(request.environment_ids),
                source_ids=tuple(request.source_ids),
                modalities=tuple(request.modalities),
                observation_types=tuple(request.kinds),
                interval=interval,
            ),
            max_episodes=request.max_episodes,
            per_episode=request.per_episode,
            neighbors=request.neighbors,
            include_summaries=request.include_summaries,
        )
        moments: list[MomentRecord] = []
        for group in run.groups:
            for hit in group.passages:
                record = self._moment(hit.passage, group.episode, request.max_chars, snippet=hit.snippet)
                record.scores = hit.scores.to_dict()
                record.kept_by = hit.kept_by
                if hit.neighbors:
                    record.before = [self._moment(p, group.episode, 200) for p in hit.neighbors.get("before", [])]
                    record.after = [self._moment(p, group.episode, 200) for p in hit.neighbors.get("after", [])]
                moments.append(record)
        plan = run.plan.to_dict() if run.plan is not None else None
        return MemoryToolOutput(
            tool="search_moments",
            result_ids=[item.id for item in moments],
            moments=moments,
            metadata={
                "read_only": True,
                "plan": plan,
                "lexical_query": run.lexical_query,
                "candidates": {"dense": run.dense_candidates, "lexical": run.lexical_candidates, "fused": run.fused_candidates},
                "rerank": {"applied": run.rerank_applied, "discarded": run.discarded_low_relevance, "min_score": run.rerank_min_score},
                "discarded_episodes": [group.episode.external_id or group.episode.id for group in run.discarded_groups],
                "took_ms": round(run.took_ms, 1),
            },
        )

    # -- get_event / get_event_neighbors ---------------------------------------------------

    def get_event(self, request: GetEventInput) -> MemoryToolOutput:
        event = self.repository.get_event(request.event_id)
        if event is None:
            raise ValueError(f"Evento no encontrado: {request.event_id}")
        episode = self.repository.get_episode(event.episode_id) if event.episode_id else None
        record = self._event_record(event, episode, request.max_chars)
        return MemoryToolOutput(tool="get_event", result_ids=[event.id], moments=[record], metadata={"read_only": True})

    def get_event_neighbors(self, request: GetEventNeighborsInput) -> MemoryToolOutput:
        event = self.repository.get_event(request.event_id)
        if event is None:
            raise ValueError(f"Evento no encontrado: {request.event_id}")
        episode = self.repository.get_episode(event.episode_id) if event.episode_id else None
        before, after = self.repository.get_event_neighbors(event.id, request.before, request.after)
        records = [self._event_record(item, episode, request.max_chars) for item in before + [event] + after]
        return MemoryToolOutput(
            tool="get_event_neighbors",
            result_ids=[item.id for item in records],
            moments=records,
            metadata={"read_only": True, "center": event.id, "before": len(before), "after": len(after)},
        )

    # -- get_episode_summary ----------------------------------------------------------

    def get_episode_summary(self, request: GetEpisodeSummaryInput) -> MemoryToolOutput:
        if self.repository.get_episode(request.episode_id) is None:
            raise ValueError(f"Episodio no encontrado: {request.episode_id}")
        items = (
            self.repository.list_summary_revisions("episode", request.episode_id)
            if request.include_revisions
            else [s for s in [self.repository.get_current_summary("episode", request.episode_id)] if s]
        )
        return MemoryToolOutput(
            tool="get_episode_summary",
            result_ids=[item.id for item in items],
            summaries=[self._summary_record(item) for item in items],
            metadata={"read_only": True, "generated": True, "found": bool(items)},
        )

    # -- browse_memory ---------------------------------------------------------------------

    def browse_memory(self, request: BrowseMemoryInput) -> MemoryToolOutput:
        items: list[BrowseItemRecord] = []
        if request.level == "environments":
            for environment in self.repository.list_environments():
                episodes = [e for e in self.repository.list_episodes_for_environment(environment.id) if e.is_visible]
                if not episodes:
                    continue  # sin memoria que navegar (p. ej. el ambiente legado por defecto)
                overview = self.repository.get_current_summary("environment", environment.id)
                starts = [e.start_time for e in episodes if e.start_time]
                items.append(BrowseItemRecord(
                    level="environment", id=environment.id, label=environment.name or environment.id,
                    start_time=min(starts) if starts else None, end_time=max(starts) if starts else None,
                    count=len(episodes), summary_title=overview.title if overview else None,
                    summary_text=overview.summary if overview else None,
                ))
            items.sort(key=lambda item: item.id)
        elif request.level == "days":
            episodes = [e for e in self.repository.list_episodes_for_environment(request.environment_id) if e.is_visible and e.start_time]
            by_day: dict[str, list] = {}
            for episode in episodes:
                by_day.setdefault(self._day(episode.start_time), []).append(episode)
            for day, group in sorted(by_day.items()):
                digest = self.repository.get_current_summary("day", f"{request.environment_id}|{day}")
                items.append(BrowseItemRecord(
                    level="day", id=f"{request.environment_id}|{day}", label=day,
                    start_time=min(e.start_time for e in group), end_time=max(e.end_time or e.start_time for e in group),
                    count=len(group), summary_title=digest.title if digest else None,
                    summary_text=digest.summary if digest else None,
                ))
        elif request.level == "episodes":
            episodes = [e for e in self.repository.list_episodes_for_environment(request.environment_id) if e.is_visible]
            if request.day:
                episodes = [e for e in episodes if e.start_time and self._day(e.start_time) == request.day]
            episodes.sort(key=lambda e: (e.start_time or datetime.min.replace(tzinfo=timezone.utc), e.id))
            for episode in episodes:
                current = self.repository.get_current_summary("episode", episode.id)
                items.append(BrowseItemRecord(
                    level="episode", id=episode.id, label=current.title if current else episode.title,
                    external_id=episode.external_id, start_time=episode.start_time, end_time=episode.end_time,
                    count=len(self.repository.list_events(episode.id)),
                    summary_title=current.title if current else None, summary_text=current.summary if current else None,
                ))
        else:
            if self.repository.get_episode(request.episode_id) is None:
                raise ValueError(f"Episodio no encontrado: {request.episode_id}")
            for event in self.repository.list_events(request.episode_id):
                items.append(BrowseItemRecord(
                    level="event", id=event.id, label=event.title or event.summary[:80], start_time=event.start_time,
                    end_time=event.end_time, offset_seconds=event.start_offset_seconds, summary_text=_clip(event.summary, 240),
                    count=len((event.attributes or {}).get("speech") or []),
                ))
        total = len(items)
        start = (request.page - 1) * request.page_size
        page_items = items[start:start + request.page_size]
        return MemoryToolOutput(
            tool="browse_memory",
            result_ids=[item.id for item in page_items],
            browse=page_items,
            metadata={"read_only": True, "level": request.level, "page": request.page, "page_size": request.page_size,
                      "total": total, "has_more": start + request.page_size < total, "timezone": self._tz_name()},
        )

    # -- get_document_passages ----------------------------------------------------------------

    def get_document_passages(self, request: GetDocumentPassagesInput) -> MemoryToolOutput:
        episode = self.repository.get_episode(request.episode_id)
        if episode is None:
            raise ValueError(f"Episodio no encontrado: {request.episode_id}")
        passages = [p for p in self.repository.list_passages_for_episode(episode.id) if p.kind in DOCUMENT_KINDS]
        if request.query:
            terms = {t for t in request.query.casefold().split() if len(t) > 2}
            passages.sort(key=lambda p: -sum(1 for t in terms if t in p.text.casefold()))
        selected = passages[: request.limit]
        moments = [self._moment(p, episode, request.max_chars) for p in selected]
        return MemoryToolOutput(
            tool="get_document_passages",
            result_ids=[item.id for item in moments],
            moments=moments,
            metadata={"read_only": True, "total_document_passages": len(passages)},
        )

    # -- Conversión ------------------------------------------------------------------------

    def _moment(self, passage: Passage, episode: Any, max_chars: int, snippet: str = "") -> MomentRecord:
        clean_snippet = snippet.replace("\x02", "«").replace("\x03", "»") if snippet else ""
        return MomentRecord(
            id=passage.id, kind=passage.kind, event_id=passage.event_id, observation_id=passage.observation_id,
            episode_id=passage.episode_id,
            episode_external_id=getattr(episode, "external_id", None), episode_title=getattr(episode, "title", "") or "",
            environment_id=passage.environment_id, source_id=passage.source_id, title=passage.title,
            text=_clip(passage.text, max_chars), snippet=_clip(clean_snippet, 300),
            start_time=passage.start_time, end_time=passage.end_time,
            start_offset_seconds=passage.start_offset_seconds, end_offset_seconds=passage.end_offset_seconds,
            page_number=passage.page_number, sequence_number=passage.sequence_number,
            keyframes=passage.keyframes, model=passage.model,
        )

    def _event_record(self, event: Event, episode: Any, max_chars: int) -> MomentRecord:
        passage = passage_from_event(event, episode) if episode is not None else None
        attributes = event.attributes or {}
        record = (
            self._moment(passage, episode, max_chars)
            if passage is not None
            else MomentRecord(id=event.id, kind="video_event", event_id=event.id, episode_id=event.episode_id or "",
                              title=event.title, text=_clip(event.description, max_chars), start_time=event.start_time,
                              end_time=event.end_time, start_offset_seconds=event.start_offset_seconds,
                              end_offset_seconds=event.end_offset_seconds, sequence_number=event.sequence_number)
        )
        record.text = _clip("\n".join(part for part in (event.summary, event.description) if part), max_chars)
        record.actions = [str(a) for a in attributes.get("actions") or []]
        record.actors = _labels(attributes.get("actors"))
        record.objects = _labels(attributes.get("objects"))
        record.visible_text = [str(v) for v in attributes.get("visible_text") or []]
        record.speech = [
            {"text": s.get("text"), "start_offset_seconds": s.get("start_offset_seconds")}
            for s in attributes.get("speech") or [] if isinstance(s, dict)
        ]
        record.keyframes = len(event.keyframes or [])
        return record

    @staticmethod
    def _summary_record(item: Summary) -> SummaryRecord:
        return SummaryRecord(
            id=item.id, scope=item.scope, scope_key=item.scope_key, revision=item.revision, is_current=item.is_current,
            title=item.title, summary=item.summary, narrative=item.narrative, highlights=item.highlights,
            open_questions=item.open_questions, model_id=item.model_id, created_at=item.created_at,
        )

    def _day(self, value: datetime) -> str:
        return value.astimezone(self.timezone).strftime("%Y-%m-%d")

    def _tz_name(self) -> str:
        return getattr(self.timezone, "key", None) or str(self.timezone)


def timezone_utc() -> tzinfo:
    return timezone.utc
