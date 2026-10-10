"""Normalización temporal para la proyección vectorial en Qdrant."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from episodic.domain.times import Interval, TimeSpec


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def timeline_payload(
    *,
    interval: Optional[Interval] = None,
    instant: Optional[TimeSpec] = None,
) -> Dict[str, Any]:
    """Construye el payload temporal exacto y sus límites de filtrado.

    Una captura puntual conserva ``start == end`` en los campos exactos. Para
    aplicar solapamiento semiabierto en Qdrant se usa ``timeline_filter_end_ms``
    con un mínimo de un milisegundo, sin modificar el instante canónico.
    """
    if instant is not None:
        interval = Interval(start=instant, end=instant)

    start = interval.start if interval else None
    end = interval.end if interval else None
    start_abs = start.absolute if start and not start.is_unknown else None
    end_abs = end.absolute if end and not end.is_unknown else None
    unknown = start_abs is None or end_abs is None

    payload: Dict[str, Any] = {
        "timeline_unknown": unknown,
        "timeline_kind": "unknown",
    }
    if unknown:
        return payload

    start_ms = _epoch_ms(start_abs)
    end_ms = _epoch_ms(end_abs)
    is_point = start_ms == end_ms
    payload.update(
        {
            "timeline_kind": "point" if is_point else "interval",
            "timeline_start": start_abs.isoformat(),
            "timeline_end": end_abs.isoformat(),
            "timeline_start_ms": start_ms,
            "timeline_end_ms": end_ms,
            "timeline_filter_end_ms": end_ms + 1 if is_point else end_ms,
            "timeline_clock_source": start.clock_source or end.clock_source,
            "timeline_uncertainty_seconds": max(
                start.uncertainty_seconds or 0.0,
                end.uncertainty_seconds or 0.0,
            ),
        }
    )
    return payload


def absolute_interval_filter(interval: Optional[Interval]) -> Dict[str, int]:
    """Devuelve límites de solapamiento Qdrant si el intervalo es absoluto."""
    if not interval or not interval.start or not interval.end:
        return {}
    if interval.start.is_unknown or interval.end.is_unknown:
        return {}
    if interval.start.absolute is None or interval.end.absolute is None:
        return {}
    return {
        "timeline_query_start_ms": _epoch_ms(interval.start.absolute),
        "timeline_query_end_ms": _epoch_ms(interval.end.absolute),
    }
