"""Línea temporal a escala (Fase T): extensión, densidad y elementos en ventana.

El servidor decide qué cabe en pantalla. La densidad se agrega en SQLite por
cubos de tiempo local (minuto, hora o día) y nunca devuelve más de
``MAX_BUCKETS`` cubos; la ventana devuelve los elementos que se solapan con el
intervalo visible solo si caben en el tope y, si no, la densidad por fuente de
ese intervalo para pintarla como grupos.

Los instantes se guardan en UTC; los cubos se cuentan en la hora local de la
memoria (``EPIGRAPH_QUERY_TIMEZONE``). SQLite no conoce los cambios de
horario, así que cada consulta se parte en tramos de desplazamiento constante.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Optional

UTC = timezone.utc
BUCKETS = ("minute", "hour", "day")
_FORMAT = {"minute": "%Y-%m-%dT%H:%M", "hour": "%Y-%m-%dT%H", "day": "%Y-%m-%d"}
_STEP = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1)}
MAX_BUCKETS = 2000
MAX_ITEMS = 5000
# Cotas del recorrido de los índices por start_time: nada empieza antes de la
# ventana más de esto y sigue dentro de ella.
EVENT_LOOKBACK = timedelta(hours=6)
OBSERVATION_LOOKBACK = timedelta(days=1)
EPISODE_LOOKBACK = timedelta(days=2)
SPEECH_TYPE = "speech_segment"
DOCUMENT_TYPE = "document_text_fragment"


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def _iso(value: datetime) -> str:
    """El mismo formato que guarda el repositorio, para comparar como texto."""
    return _utc(value).isoformat()


def _offset_minutes(instant: datetime, tz: tzinfo) -> int:
    return int(instant.astimezone(tz).utcoffset().total_seconds() // 60)


def offset_segments(start: datetime, end: datetime, tz: tzinfo) -> list[tuple[datetime, datetime, int]]:
    """Parte ``[start, end)`` en tramos con desplazamiento UTC constante.

    Recorre el intervalo por días y, donde el desplazamiento cambia, busca el
    segundo exacto del cambio de horario.
    """
    start, end = _utc(start), _utc(end)
    segments: list[tuple[datetime, datetime, int]] = []
    seg_start, current, cursor = start, _offset_minutes(start, tz), start
    while cursor < end:
        nxt = min(end, cursor + timedelta(days=1))
        if _offset_minutes(nxt, tz) == current or nxt == end and _offset_minutes(nxt - timedelta(microseconds=1), tz) == current:
            cursor = nxt
            continue
        lo, hi = cursor, nxt
        while hi - lo > timedelta(seconds=1):
            mid = lo + (hi - lo) / 2
            if _offset_minutes(mid, tz) == current:
                lo = mid
            else:
                hi = mid
        change = lo.replace(microsecond=0) + timedelta(seconds=1)
        while _offset_minutes(change, tz) == current:
            change += timedelta(seconds=1)
        segments.append((seg_start, change, current))
        seg_start, current, cursor = change, _offset_minutes(change, tz), change
    segments.append((seg_start, end, current))
    return [segment for segment in segments if segment[1] > segment[0]]


def choose_bucket(start: datetime, end: datetime) -> str:
    span = end - start
    if span <= timedelta(hours=6):
        return "minute"
    if span <= timedelta(days=7):
        return "hour"
    return "day"


def bucket_keys(start: datetime, end: datetime, bucket: str, tz: tzinfo) -> list[tuple[str, datetime, datetime]]:
    """Cubos locales consecutivos que cubren ``[start, end)``: (clave, inicio, fin)."""
    start, end = _utc(start), _utc(end)
    keys: list[tuple[str, datetime, datetime]] = []
    if bucket == "day":
        day = start.astimezone(tz).date()
        last = (end - timedelta(microseconds=1)).astimezone(tz).date()
        while day <= last:
            following = day + timedelta(days=1)
            keys.append((
                day.isoformat(),
                datetime(day.year, day.month, day.day, tzinfo=tz),
                datetime(following.year, following.month, following.day, tzinfo=tz),
            ))
            day = following
        return keys
    step = _STEP[bucket]
    local = start.astimezone(tz)
    local = local.replace(second=0, microsecond=0) if bucket == "minute" else local.replace(minute=0, second=0, microsecond=0)
    cursor = _utc(local)
    seen: dict[str, int] = {}
    while cursor < end:
        nxt = cursor + step
        key = cursor.astimezone(tz).strftime(_FORMAT[bucket])
        if key in seen:  # la hora repetida al retrasar el reloj: un solo cubo
            k, s, _ = keys[seen[key]]
            keys[seen[key]] = (k, s, nxt.astimezone(tz))
        else:
            seen[key] = len(keys)
            keys.append((key, cursor.astimezone(tz), nxt.astimezone(tz)))
        cursor = nxt
    return keys


def _clip(text: Any, size: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


def _label(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("label") or item.get("name") or item.get("text") or "")
    return str(item)


class TimelineService:
    def __init__(self, repository: Any, tz: tzinfo, default_limit: int = 1500) -> None:
        self.repository = repository
        self.timezone = tz
        self.default_limit = default_limit
        # La extensión y la densidad de rangos amplios se repiten en cada visita
        # y cuestan cientos de ms con millones de eventos: se cachean hasta que
        # cambian los eventos.
        self._cache: OrderedDict[tuple, Any] = OrderedDict()
        self._cache_lock = threading.Lock()

    def _cached(self, key: tuple, compute):
        version = self.repository.timeline_data_version()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None and hit[0] == version:
                self._cache.move_to_end(key)
                return hit[1]
        value = compute()
        with self._cache_lock:
            self._cache[key] = (version, value)
            self._cache.move_to_end(key)
            while len(self._cache) > 32:
                self._cache.popitem(last=False)
        return value

    @property
    def timezone_name(self) -> str:
        return getattr(self.timezone, "key", None) or str(self.timezone)

    def _local(self, value: datetime) -> str:
        return _utc(value).astimezone(self.timezone).isoformat()

    # -- Extensión --------------------------------------------------------------------

    def span(self) -> dict[str, Any]:
        return self._cached(("span",), self._span)

    def _span(self) -> dict[str, Any]:
        rows = self.repository.timeline_event_span()
        environments = []
        for row in sorted(rows, key=lambda item: item["environment_id"] or ""):
            environment = self.repository.get_environment(row["environment_id"]) if row["environment_id"] else None
            environments.append({
                "id": row["environment_id"],
                "name": environment.name if environment and environment.name else row["environment_id"],
                "events": int(row["n"]),
                "first": row["first"],
                "last": row["last"],
            })
        return {
            "timezone": self.timezone_name,
            "events": sum(item["events"] for item in environments),
            "first": min((item["first"] for item in environments), default=None),
            "last": max((item["last"] for item in environments), default=None),
            "environments": environments,
        }

    # -- Densidad -----------------------------------------------------------------------

    def density(
        self,
        start: datetime,
        end: datetime,
        bucket: str = "auto",
        environment_ids: Optional[list[str]] = None,
        by_source: bool = False,
    ) -> dict[str, Any]:
        start, end = _utc(start), _utc(end)
        if end <= start:
            raise ValueError("El final del intervalo debe ser posterior al inicio")
        if bucket not in (*BUCKETS, "auto"):
            raise ValueError(f"Cubo no soportado: {bucket}; usa minute, hour, day o auto")
        bucket = choose_bucket(start, end) if bucket == "auto" else bucket
        # Si el cubo pedido da demasiados, se sube al siguiente; en días, se
        # recorta el inicio para quedarse con los últimos MAX_BUCKETS.
        while bucket != "day" and (end - start) / _STEP[bucket] > MAX_BUCKETS:
            bucket = BUCKETS[BUCKETS.index(bucket) + 1]
        if bucket == "day" and (end - start) / _STEP["day"] > MAX_BUCKETS - 1:
            start = end - _STEP["day"] * (MAX_BUCKETS - 1)
        cache_key = ("density", start, end, bucket, tuple(sorted(environment_ids or [])), by_source)
        return self._cached(cache_key, lambda: self._density(start, end, bucket, environment_ids, by_source))

    def _density(self, start: datetime, end: datetime, bucket: str, environment_ids: Optional[list[str]], by_source: bool) -> dict[str, Any]:
        keys = bucket_keys(start, end, bucket, self.timezone)
        index = {key: position for position, (key, _, _) in enumerate(keys)}
        lanes: dict[tuple[str, Optional[str]], list[int]] = {}
        for seg_start, seg_end, offset in offset_segments(start, end, self.timezone):
            for row in self.repository.timeline_event_density(
                _iso(seg_start), _iso(seg_end), _FORMAT[bucket], f"{offset:+d} minutes", environment_ids, by_source
            ):
                position = index.get(row["bucket"])
                if position is None:
                    continue
                lane = (row["environment_id"], row.get("source_id") if by_source else None)
                lanes.setdefault(lane, [0] * len(keys))[position] += int(row["n"])
        ordered = sorted(lanes.items(), key=lambda item: (item[0][0] or "", item[0][1] or ""))
        totals = [sum(counts[position] for _, counts in ordered) for position in range(len(keys))]
        return {
            "bucket": bucket,
            "timezone": self.timezone_name,
            "start": self._local(start),
            "end": self._local(end),
            "buckets": [{"key": key, "start": s.isoformat(), "end": e.isoformat()} for key, s, e in keys],
            "lanes": [
                {"environment_id": env, "source_id": source, "counts": counts, "total": sum(counts)}
                for (env, source), counts in ordered
            ],
            "totals": totals,
            "max": max((max(counts) for _, counts in ordered), default=0),
            "events": sum(totals),
        }

    # -- Elementos en ventana -----------------------------------------------------------

    def items(
        self,
        start: datetime,
        end: datetime,
        environment_ids: Optional[list[str]] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        start, end = _utc(start), _utc(end)
        if end <= start:
            raise ValueError("El final del intervalo debe ser posterior al inicio")
        limit = max(1, min(MAX_ITEMS, int(limit or self.default_limit)))
        s, e = _iso(start), _iso(end)
        total = self.repository.timeline_count_events(s, e, _iso(start - EVENT_LOOKBACK), environment_ids)
        wanted = set(environment_ids or [])
        episodes = [
            {
                "id": row["id"],
                "title": row["title"],
                "external_id": row["external_id"],
                "start": row["start_time"],
                "end": row["end_time"] or row["start_time"],
                "environment_id": (row["environment_ids"] or [None])[0],
            }
            for row in self.repository.timeline_episodes(s, e, _iso(start - EPISODE_LOOKBACK), limit)
            if not wanted or wanted.intersection(row["environment_ids"] or [])
        ]
        base = {
            "timezone": self.timezone_name,
            "start": self._local(start),
            "end": self._local(end),
            "limit": limit,
            "events_total": total,
            "episodes": episodes,
        }
        if total > limit:
            return {**base, "mode": "clusters", "density": self.density(start, end, "auto", environment_ids, by_source=True)}
        events = [self._event(payload) for payload in self.repository.timeline_events(
            s, e, _iso(start - EVENT_LOOKBACK), environment_ids, limit
        )]
        speech, documents = [], []
        for row in self.repository.timeline_observations(
            s, e, _iso(start - OBSERVATION_LOOKBACK), [SPEECH_TYPE, DOCUMENT_TYPE], environment_ids, limit * 2
        ):
            (speech if row["observation_type"] == SPEECH_TYPE else documents).append(self._observation(row))
        return {**base, "mode": "items", "events": events, "speech": speech, "documents": documents}

    @staticmethod
    def _event(payload: dict[str, Any]) -> dict[str, Any]:
        attributes = payload.get("attributes") or {}
        metadata = payload.get("metadata") or {}
        return {
            "id": payload.get("id"),
            "environment_id": payload.get("environment_id"),
            "source_id": payload.get("source_id"),
            "episode_id": payload.get("episode_id"),
            "asset_id": payload.get("asset_id"),
            "observation_id": next(iter(metadata.get("observation_ids") or []), None),
            "start": payload.get("start_time"),
            "end": payload.get("end_time") or payload.get("start_time"),
            "start_offset_seconds": payload.get("start_offset_seconds"),
            "end_offset_seconds": payload.get("end_offset_seconds"),
            "title": payload.get("title") or _clip(payload.get("description"), 80),
            "summary": _clip(payload.get("summary") or payload.get("description"), 400),
            "actions": [_label(item) for item in attributes.get("actions") or []][:12],
            "actors": [_label(item) for item in attributes.get("actors") or []][:12],
            "objects": [_label(item) for item in attributes.get("objects") or []][:16],
            "visible_text": [_label(item) for item in attributes.get("visible_text") or []][:12],
            "speech": [_clip(item.get("text"), 400) for item in attributes.get("speech") or [] if isinstance(item, dict) and item.get("text")][:6],
            "keyframes": len(payload.get("keyframes") or []),
        }

    @staticmethod
    def _observation(row: dict[str, Any]) -> dict[str, Any]:
        import json

        attributes = json.loads(row.get("attributes_json") or "{}") or {}
        return {
            "id": row["id"],
            "type": row["observation_type"],
            "environment_id": row["environment_id"],
            "source_id": row["source_id"],
            "episode_id": row["episode_id"],
            "start": row["start_time"],
            "end": row["end_time"] or row["start_time"],
            "text": _clip(row.get("content") or row.get("description"), 400),
            "page": attributes.get("page_number"),
            "start_offset_seconds": attributes.get("start_offset_seconds"),
        }
