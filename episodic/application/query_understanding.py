"""Comprensión determinista de la pregunta: qué se busca, cuándo y dónde.

Sin modelos: expresiones regulares sobre el texto sin tildes. Convierte
«¿qué pasó ayer por la tarde en el taller después de cortar la pieza?» en
un plan con la intención (antes/después), el intervalo absoluto (ayer,
14:00–21:00 en la zona horaria configurada), el ambiente si se nombra y el
texto que de verdad hay que buscar («pasó … taller … cortar la pieza»).
La expresión temporal se retira del texto para que no contamine la búsqueda:
el tiempo ya va como filtro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable, Optional

from episodic.domain.times import Interval, TimeSpec

_ACCENTS = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")

MONTHS = {
    name: index
    for index, name in enumerate(
        "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(), 1
    )
}
MONTHS["setiembre"] = 9
WEEKDAYS = {name: index for index, name in enumerate("lunes martes miercoles jueves viernes sabado domingo".split())}
NUMBER_WORDS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "quince": 15, "veinte": 20, "treinta": 30, "media": 0.5,
}
# Partes del día (hora local): [inicio, fin).
DAY_PARTS = {
    "madrugada": (0, 6), "manana": (6, 12), "mediodia": (12, 14), "tarde": (14, 21), "noche": (21, 24),
}

_NUM = r"(\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|quince|veinte|treinta|media)"
_UNIT = r"(minutos?|horas?|dias?|semanas?|meses|mes|anos?)"
_PART = r"(madrugada|manana|mediodia|tarde|noche)"
_TIME = r"(\d{1,2})(?:[:.h](\d{2}))?\s*(?:h\b|horas?\b)?"

# Orden: primero las expresiones largas, para que «ayer por la tarde» no se
# parta en «ayer» + «por la tarde».
_DAY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(hoy|ayer|anteayer|antes de ayer)\s+(?:por|de|en)\s+la\s+" + _PART + r"\b", "day_part"),
    (r"\besta\s+" + _PART + r"\b", "this_part"),
    (r"\b(hoy|ayer|anteayer|antes de ayer)\b", "day"),
    (r"\b(?:esta|la presente)\s+semana\b", "this_week"),
    (r"\bla\s+semana\s+pasada\b", "last_week"),
    (r"\beste\s+mes\b", "this_month"),
    (r"\bel\s+mes\s+pasado\b", "last_month"),
    (r"\beste\s+ano\b", "this_year"),
    (r"\bel\s+ano\s+pasado\b", "last_year"),
    (r"\bhace\s+" + _NUM + r"\s+" + _UNIT + r"\b", "ago"),
    (r"\b(?:en\s+)?(?:las|los)\s+ultim[oa]s\s+" + _NUM + r"\s+" + _UNIT + r"\b", "last_n"),
    (r"\b(?:en\s+)?(?:la|el)\s+ultim[oa]\s+(hora|dia|semana|mes|ano)\b", "last_one"),
    (r"\b(?:el\s+|este\s+|el\s+pasado\s+)?(lunes|martes|miercoles|jueves|viernes|sabado|domingo)(?:\s+pasado)?"
     r"(?:\s+(?:por|de)\s+la\s+" + _PART + r")?\b", "weekday"),
    (r"\b(?:el\s+)?(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
     r"noviembre|diciembre)(?:\s+(?:de|del)\s+(\d{4}))?(?:\s+(?:por|de)\s+la\s+" + _PART + r")?\b", "date_words"),
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", "date_iso"),
    (r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", "date_slash"),
    (r"\b(?:por|de|en)\s+la\s+" + _PART + r"\b", "part_only"),
]
_TIME_PATTERNS: list[tuple[str, str]] = [
    (r"\bentre\s+las?\s+" + _TIME + r"\s+y\s+las?\s+" + _TIME, "between"),
    (r"\b(?:despues|a partir|pasadas?)\s+de\s+las?\s+" + _TIME, "after"),
    (r"\bantes\s+de\s+las?\s+" + _TIME, "before"),
    (r"\b(?:sobre|hacia|a eso de|en torno a)\s+las?\s+" + _TIME, "around"),
    (r"\ba\s+las?\s+" + _TIME, "at"),
]
_INTENTS: list[tuple[str, str]] = [
    ("antes_despues", r"\b(justo\s+)?(despues|luego|tras|a continuacion|antes|previamente|anteriormente|seguidamente)\b"),
    ("cuando", r"\b(cuando|en que momento|a que hora|que dia|en que fecha)\b"),
    ("quien", r"\b(quien|quienes)\b"),
    ("donde", r"\b(donde|en que (captura|video|episodio|lugar|sitio|sala|grabacion))\b"),
    ("cuanto", r"\b(cuanto|cuantos|cuantas)\b"),
    ("documento", r"\b(manual|documento|procedimiento|pagina|pdf|instrucciones|especificacion|especificaciones|ficha)\b"),
    ("que", r"\b(que|cual|cuales)\b"),
]


@dataclass
class QueryPlan:
    text: str
    search_text: str
    intent: str = "libre"
    interval: Optional[Interval] = None
    time_expression: Optional[str] = None
    timezone: str = "UTC"
    phrases: list[str] = field(default_factory=list)
    environment_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    # Vecinos (eventos anterior/siguiente) que conviene devolver.
    neighbors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "search_text": self.search_text,
            "intent": self.intent,
            "interval": {
                "start": self.interval.start.absolute.isoformat(),
                "end": self.interval.end.absolute.isoformat(),
            }
            if self.interval and self.interval.start and self.interval.end
            else None,
            "time_expression": self.time_expression,
            "timezone": self.timezone,
            "phrases": list(self.phrases),
            "environment_ids": list(self.environment_ids),
            "source_ids": list(self.source_ids),
            "neighbors": self.neighbors,
        }


def fold(text: str) -> str:
    """Minúsculas sin tildes, conservando la longitud (para recortar por posición)."""
    return text.lower().translate(_ACCENTS)


def parse_query(
    text: str,
    *,
    now: Optional[datetime] = None,
    tz: Optional[tzinfo] = None,
    environments: Iterable[tuple[str, str]] = (),
    sources: Iterable[tuple[str, str]] = (),
) -> QueryPlan:
    """``environments``/``sources``: pares (id, nombre) conocidos; si la pregunta
    los nombra, se convierten en filtro y salen del texto buscado."""
    tz = tz or timezone.utc
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    original = text.strip()
    folded = fold(original)
    plan = QueryPlan(text=original, search_text=original, timezone=_tz_name(tz))
    plan.phrases = re.findall(r'"([^"]+)"', original)

    spans: list[tuple[int, int]] = []
    day: Optional[tuple[datetime, datetime]] = None
    part: Optional[str] = None
    for pattern, kind in _DAY_PATTERNS:
        match = re.search(pattern, folded)
        if not match:
            continue
        resolved = _resolve_day(kind, match, now)
        if resolved is None:
            continue
        day, part = resolved
        spans.append(match.span())
        break
    clock: Optional[tuple[Optional[float], Optional[float]]] = None
    clock_span: Optional[tuple[int, int]] = None
    for pattern, kind in _TIME_PATTERNS:
        match = re.search(pattern, folded)
        if match:
            clock = _resolve_clock(kind, match)
            clock_span = match.span()
            break

    # Una hora sin día («terminar antes de las 6») suele ser contenido, no
    # filtro: solo cuenta acompañada de un día.
    interval = _build_interval(day, part, clock) if day is not None else None
    if interval is not None:
        if clock_span is not None:
            spans.append(clock_span)
        plan.interval = interval
        plan.time_expression = " · ".join(original[a:b].strip() for a, b in sorted(spans))

    # Ambientes y fuentes nombrados tal cual (id o nombre).
    for known, target in ((environments, plan.environment_ids), (sources, plan.source_ids)):
        for identifier, name in known:
            for candidate in {identifier, name}:
                if not candidate:
                    continue
                pattern = r"(?<!\w)" + re.escape(fold(candidate)) + r"(?!\w)"
                match = re.search(pattern, folded)
                if match:
                    target.append(identifier)
                    spans.append(match.span())
                    break

    remaining = _strip(original, spans)
    # Para la intención, una hora suelta tampoco es «antes/después» de un hecho.
    intent_spans = spans + ([clock_span] if clock_span is not None and clock_span not in spans else [])
    remaining_folded = fold(_strip(original, intent_spans))
    for intent, pattern in _INTENTS:
        if re.search(pattern, remaining_folded):
            plan.intent = intent
            break
    if plan.intent == "antes_despues":
        plan.neighbors = 2
    plan.search_text = remaining or original
    return plan


# -- Resolución temporal ---------------------------------------------------------


def _resolve_day(kind: str, match: re.Match, now: datetime):
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    groups = match.groups()
    if kind in {"day", "day_part"}:
        offset = {"hoy": 0, "ayer": 1, "anteayer": 2, "antes de ayer": 2}[groups[0]]
        day_start = start_of_day - timedelta(days=offset)
        return (day_start, day_start + timedelta(days=1)), (groups[1] if kind == "day_part" else None)
    if kind == "this_part":
        return (start_of_day, start_of_day + timedelta(days=1)), groups[0]
    if kind == "part_only":
        # Sin día: se entiende hoy.
        return (start_of_day, start_of_day + timedelta(days=1)), groups[0]
    if kind == "this_week":
        monday = start_of_day - timedelta(days=now.weekday())
        return (monday, monday + timedelta(days=7)), None
    if kind == "last_week":
        monday = start_of_day - timedelta(days=now.weekday() + 7)
        return (monday, monday + timedelta(days=7)), None
    if kind == "this_month":
        first = start_of_day.replace(day=1)
        return (first, _add_months(first, 1)), None
    if kind == "last_month":
        first = _add_months(start_of_day.replace(day=1), -1)
        return (first, _add_months(first, 1)), None
    if kind == "this_year":
        first = start_of_day.replace(month=1, day=1)
        return (first, first.replace(year=first.year + 1)), None
    if kind == "last_year":
        first = start_of_day.replace(year=now.year - 1, month=1, day=1)
        return (first, first.replace(year=first.year + 1)), None
    if kind in {"ago", "last_n", "last_one"}:
        if kind == "last_one":
            amount, unit = 1.0, groups[0]
        else:
            amount, unit = _number(groups[0]), groups[1]
        delta = _delta(amount, unit)
        if delta is None:
            return None
        if kind == "ago" and unit.startswith(("dia", "semana", "mes", "ano")):
            # «hace tres días»: ese día entero.
            day_start = start_of_day - delta
            return (day_start, day_start + timedelta(days=1)), None
        return (now - delta, now + timedelta(minutes=1)), None
    if kind == "weekday":
        target = WEEKDAYS[groups[0]]
        back = (now.weekday() - target) % 7
        day_start = start_of_day - timedelta(days=back)
        return (day_start, day_start + timedelta(days=1)), groups[1]
    if kind == "date_words":
        day_number, month, year, day_part = int(groups[0]), MONTHS[groups[1]], groups[2], groups[3]
        try:
            candidate = start_of_day.replace(year=int(year) if year else now.year, month=month, day=day_number)
        except ValueError:
            return None
        if not year and candidate > now:
            candidate = candidate.replace(year=candidate.year - 1)
        return (candidate, candidate + timedelta(days=1)), day_part
    if kind == "date_iso":
        year, month, day_number = (int(value) for value in groups)
    else:  # date_slash
        day_number, month = int(groups[0]), int(groups[1])
        year = int(groups[2]) if groups[2] else now.year
        if year < 100:
            year += 2000
    try:
        candidate = start_of_day.replace(year=year, month=month, day=day_number)
    except ValueError:
        return None
    return (candidate, candidate + timedelta(days=1)), None


def _resolve_clock(kind: str, match: re.Match) -> tuple[Optional[float], Optional[float]]:
    groups = match.groups()
    first = _hours(groups[0], groups[1])
    if kind == "between":
        return first, _hours(groups[2], groups[3])
    if kind == "after":
        return first, None
    if kind == "before":
        return None, first
    if kind == "around":
        return first - 0.5, first + 0.5
    return first, first + 1.0  # «a las 9»: esa hora


def _build_interval(day, part, clock) -> Optional[Interval]:
    start, end = day
    if part:
        hours = DAY_PARTS[part]
        start, end = day[0] + timedelta(hours=hours[0]), day[0] + timedelta(hours=hours[1])
    if clock:
        lower, upper = clock
        if lower is not None:
            start = max(start, day[0] + timedelta(hours=lower))
        if upper is not None:
            end = min(end, day[0] + timedelta(hours=upper))
    if end <= start:
        return None
    return Interval(
        start=TimeSpec.from_utc(start.astimezone(timezone.utc), clock_source="query"),
        end=TimeSpec.from_utc(end.astimezone(timezone.utc), clock_source="query"),
    )


def _hours(hour: str, minute: Optional[str]) -> float:
    return int(hour) + (int(minute) / 60 if minute else 0.0)


def _number(value: str) -> float:
    return float(value) if value.isdigit() else NUMBER_WORDS.get(value, 1)


def _delta(amount: float, unit: str) -> Optional[timedelta]:
    if unit.startswith("minuto"):
        return timedelta(minutes=amount)
    if unit.startswith("hora"):
        return timedelta(hours=amount)
    if unit.startswith("dia"):
        return timedelta(days=amount)
    if unit.startswith("semana"):
        return timedelta(weeks=amount)
    if unit.startswith("mes"):
        return timedelta(days=30 * amount)
    if unit.startswith("ano"):
        return timedelta(days=365 * amount)
    return None


def _add_months(value: datetime, months: int) -> datetime:
    month = value.month - 1 + months
    year = value.year + month // 12
    return value.replace(year=year, month=month % 12 + 1, day=1)


def _strip(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    cleaned = "".join(chars)
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Preposiciones que quedan colgando al retirar el tiempo («capturas del»).
    cleaned = re.sub(r"\b(del|de|en|a|por|desde|hasta)\s*(?=[?¿.!]|$)", "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" ,;")


def _tz_name(tz: tzinfo) -> str:
    return getattr(tz, "key", None) or str(tz)
