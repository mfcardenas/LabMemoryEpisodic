"""Modelo temporal del dominio episódico.

Separa: tiempo de captura, tiempo relativo del medio, tiempo de ingesta.
Conserva reloj/fuente, desfase e incertidumbre.  UTC para instantes absolutos.
Intervalos con semántica [inicio, fin).  Fotografía = captura puntual.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class TimeSpec:
    """Un instante con procedencia y posible incertidumbre.

    - `absolute`: instante UTC si se conoce.
    - `relative_seconds`: offset desde el inicio del medio.
    - `clock_source`: qué reloj lo produjo (e.g. "camera_c01", "system").
    - `uncertainty_seconds`: margen de error conocido.
    - `is_unknown`: True si no hay información temporal fiable.
    """
    absolute: Optional[datetime] = None
    relative_seconds: Optional[float] = None
    clock_source: Optional[str] = None
    uncertainty_seconds: Optional[float] = None
    is_unknown: bool = False

    def __post_init__(self):
        if self.absolute is not None and self.absolute.tzinfo is None:
            # Forzar UTC explícito — nunca tiempos naive.
            object.__setattr__(
                self, "absolute",
                self.absolute.replace(tzinfo=timezone.utc),
            )

    @classmethod
    def unknown(cls) -> TimeSpec:
        """Tiempo desconocido explícito — no se fabrica un timestamp."""
        return cls(is_unknown=True)

    @classmethod
    def from_utc(cls, dt: datetime, clock_source: str = "system",
                 uncertainty: Optional[float] = None) -> TimeSpec:
        utc_dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return cls(absolute=utc_dt, clock_source=clock_source,
                   uncertainty_seconds=uncertainty)

    @classmethod
    def from_relative(cls, seconds: float,
                      clock_source: str = "media") -> TimeSpec:
        return cls(relative_seconds=seconds, clock_source=clock_source)

    def is_comparable_with(self, other: TimeSpec) -> bool:
        """¿Se pueden ordenar con certeza razonable?"""
        if self.is_unknown or other.is_unknown:
            return False
        if self.absolute and other.absolute:
            return True
        # Tiempos relativos solo son comparables si comparten fuente.
        if (self.relative_seconds is not None
                and other.relative_seconds is not None
                and self.clock_source == other.clock_source):
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "absolute": self.absolute.isoformat() if self.absolute else None,
            "relative_seconds": self.relative_seconds,
            "clock_source": self.clock_source,
            "uncertainty_seconds": self.uncertainty_seconds,
            "is_unknown": self.is_unknown,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional[TimeSpec]:
        if not d:
            return None
        abs_dt = None
        if d.get("absolute"):
            abs_dt = datetime.fromisoformat(d["absolute"])
        return cls(
            absolute=abs_dt,
            relative_seconds=d.get("relative_seconds"),
            clock_source=d.get("clock_source"),
            uncertainty_seconds=d.get("uncertainty_seconds"),
            is_unknown=d.get("is_unknown", False),
        )


@dataclass(frozen=True)
class Interval:
    """Intervalo temporal semiabierto [start, end).

    - `start` / `end` pueden ser `None` si un extremo es desconocido.
    - Una fotografía puntual tiene `start == end` (duración cero).
    """
    start: Optional[TimeSpec] = None
    end: Optional[TimeSpec] = None

    def is_point(self) -> bool:
        """¿Captura puntual (fotografía)?"""
        if self.start is None or self.end is None:
            return False
        return (self.start.absolute is not None
                and self.start.absolute == self.end.absolute)

    def overlaps(self, other: Interval) -> Optional[bool]:
        """Solapamiento.  None si no se puede determinar."""
        if (self.start is None or self.end is None
                or other.start is None or other.end is None):
            return None
        s1, e1 = self.start.absolute, self.end.absolute
        s2, e2 = other.start.absolute, other.end.absolute
        if any(t is None for t in (s1, e1, s2, e2)):
            return None
        return s1 < e2 and s2 < e1

    def contains_instant(self, instant: TimeSpec) -> Optional[bool]:
        """¿El intervalo contiene un instante dado?"""
        if instant.is_unknown or instant.absolute is None:
            return None
        if self.start is None or self.end is None:
            return None
        s, e = self.start.absolute, self.end.absolute
        if s is None or e is None:
            return None
        return s <= instant.absolute < e

    def to_dict(self) -> dict:
        return {
            "start": self.start.to_dict() if self.start else None,
            "end": self.end.to_dict() if self.end else None,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional[Interval]:
        if not d:
            return None
        return cls(
            start=TimeSpec.from_dict(d.get("start")),
            end=TimeSpec.from_dict(d.get("end")),
        )


def utc_now() -> datetime:
    """Helper para obtener el instante actual en UTC explícito."""
    return datetime.now(timezone.utc)
