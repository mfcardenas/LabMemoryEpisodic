"""Agente orquestador: pregunta → herramientas → respuesta con citas verificadas.

Bucle acotado (vueltas, llamadas y segundos): el modelo decide qué
herramienta llamar; cada momento que devuelven recibe una marca corta
(``m1``, ``m2``…) y el modelo cita con esas marcas. Antes de devolver la
respuesta, cada marca se resuelve contra lo que realmente se recuperó: las
que no existen se eliminan y quedan anotadas. Sin citas no hay afirmación:
el agente debe decir «No consta en la memoria».
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone, tzinfo
from typing import Any, Callable, Optional

from pydantic import ValidationError

from episodic.domain.answers import AnswerRun, Citation
from episodic.ports.reasoning import ReasoningError, ReasoningModel, ToolCall, Turn
from episodic.ports.repository import EpisodicRepository
from episodic.tools.contracts import MemoryToolOutput
from episodic.tools.memory import MemoryToolbox

logger = logging.getLogger(__name__)
_UTC = timezone.utc

AGENT_TOOLS = (
    "search_moments",
    "browse_memory",
    "get_event",
    "get_event_neighbors",
    "get_episode_summary",
    "get_document_passages",
)
ABSTAIN_PREFIX = "no consta en la memoria"

# Lo que ve el modelo de cada herramienta: solo los parámetros que debe decidir.
# El resto (tamaños, umbrales, formatos) lo fija el orquestador; con el esquema
# completo gpt-oss rellenaba todos los opcionales (per_episode=0, kinds=…,
# start_time sin end_time) y perdía vueltas en errores de validación.
AGENT_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    # Sin filtro de ambiente: el modelo se inventaba ids («env1») y vaciaba la
    # búsqueda; si la pregunta nombra un ambiente, la propia búsqueda lo aplica.
    "search_moments": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "la pregunta completa o 2–4 palabras clave; las fechas, horas y ambientes van dentro del texto («ayer por la tarde en el taller», «el 12 de octubre después de las 16:30»)"},
        },
        "required": ["query"],
    },
    "browse_memory": {
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["environments", "days", "episodes", "events"]},
            "environment_id": {"type": "string", "description": "necesario para days y episodes"},
            "day": {"type": "string", "description": "AAAA-MM-DD, opcional en episodes"},
            "episode_id": {"type": "string", "description": "necesario para events"},
            "page": {"type": "integer"},
        },
        "required": ["level"],
    },
    "get_event": {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]},
    "get_event_neighbors": {
        "type": "object",
        "properties": {"event_id": {"type": "string"}, "before": {"type": "integer"}, "after": {"type": "integer"}},
        "required": ["event_id"],
    },
    "get_episode_summary": {"type": "object", "properties": {"episode_id": {"type": "string"}}, "required": ["episode_id"]},
    "get_document_passages": {
        "type": "object",
        "properties": {"episode_id": {"type": "string"}, "query": {"type": "string", "description": "palabras para ordenar los fragmentos"}},
        "required": ["episode_id"],
    },
}
# Parámetros fijados por el orquestador, fuera del alcance del modelo.
AGENT_TOOL_DEFAULTS: dict[str, dict[str, Any]] = {
    # 3 por episodio: con 2, los fragmentos de documento de un episodio
    # desplazaban al evento con el habla que respondía.
    "search_moments": {"max_episodes": 4, "per_episode": 3, "strict": False, "max_chars": 900},
    "browse_memory": {"page_size": 20},
    "get_event": {"max_chars": 2500},
    "get_event_neighbors": {"before": 2, "after": 2, "max_chars": 500},
    "get_document_passages": {"limit": 5, "max_chars": 800},
}
_CITE = re.compile(r"\[\s*(m\d+(?:\s*,\s*m\d+)*)\s*\]")
_HANDLE = re.compile(r"m\d+")

SYSTEM_PROMPT = """Eres el agente de consulta de EpiGraph, una memoria episódica de vídeos, audio y documentos \
capturados en distintos ambientes. Respondes SOLO con lo que devuelven las herramientas; nunca inventas hechos, \
horas, nombres ni intenciones.

Cómo trabajar:
1. Empieza SIEMPRE por search_moments con la pregunta completa tal cual la escribió el usuario (la herramienta \
entiende fechas, horas e intención). No la conviertas en una frase entrecomillada ni en palabras sueltas.
2. Los momentos llegan ordenados por relevancia con su puntuación; léelos: pueden responder aunque la puntuación \
sea baja. Si ninguno responde, busca una segunda vez con 2–4 palabras clave distintas (sinónimos, el objeto, la \
acción) antes de concluir nada. Si la pregunta es amplia («¿qué pasó ayer?», «¿qué suele hacerse en el taller?») \
usa browse_memory (environments → days → episodes → events) y get_episode_summary, y luego get_event para citar.
3. Para «antes/después» usa get_event_neighbors sobre el evento encontrado. Para manuales o documentos, \
get_document_passages del episodio.
4. Cada momento lleva una marca «m1», «m2»… Cita SIEMPRE con la marca entre corchetes al final de la frase que \
respalda, con la forma «<afirmación> [m2].» Nunca escribas la marca de otra forma (ni «(m2)», ni «m2:», ni solo la \
hora). Solo puedes citar marcas que hayas recibido. Los resúmenes (get_episode_summary) son texto generado: no son \
prueba; cita momentos.
5. Solo si tras al menos dos búsquedas distintas no hay ningún momento que responda, contesta empezando \
exactamente por «No consta en la memoria» y di en una frase qué buscaste. No rellenes con suposiciones.
6. La respuesta final es solo la respuesta: en español, breve y concreta, sin razonamientos ni notas en inglés y \
sin repetir frases. Primero la respuesta, luego el detalle con la hora local que dan las herramientas y la posición \
mm:ss en el vídeo. Si la pregunta pide qué se dijo, cita las palabras tal cual aparecen en «said» o en el texto."""


class AnswerOrchestrator:
    def __init__(
        self,
        repository: EpisodicRepository,
        toolbox: MemoryToolbox,
        model: Optional[ReasoningModel],
        *,
        timezone: Optional[tzinfo] = None,
        max_steps: int = 8,
        max_tool_calls: int = 12,
        max_seconds: float = 120.0,
        tool_result_chars: int = 12000,
        tool_names: tuple[str, ...] = AGENT_TOOLS,
        activity: Any = None,
    ) -> None:
        self.repository = repository
        self.toolbox = toolbox
        self.model = model
        self.timezone = timezone or _UTC
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_seconds = max_seconds
        self.tool_result_chars = tool_result_chars
        self.tool_names = tool_names
        self.activity = activity

    @property
    def enabled(self) -> bool:
        return self.model is not None

    def tool_specs(self) -> list[dict[str, Any]]:
        """Las herramientas en el formato de ``tools`` de Ollama."""
        specs = []
        for descriptor in self.toolbox.describe():
            if descriptor.name not in self.tool_names:
                continue
            schema = AGENT_TOOL_SCHEMAS.get(descriptor.name)
            if schema is None:
                schema = dict(descriptor.input_schema)
                schema.pop("title", None)
            specs.append({"type": "function", "function": {"name": descriptor.name, "description": descriptor.description, "parameters": schema}})
        return specs

    @staticmethod
    def _arguments(name: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Argumentos efectivos: los que decide el modelo (solo los del esquema
        reducido, sin nulos) sobre los fijos del orquestador."""
        allowed = set((AGENT_TOOL_SCHEMAS.get(name) or {}).get("properties") or {})
        chosen = {
            key: value for key, value in (raw or {}).items()
            if (not allowed or key in allowed) and value not in (None, "", [], {})
        }
        return {**AGENT_TOOL_DEFAULTS.get(name, {}), **chosen}

    # -- Bucle principal ----------------------------------------------------------------

    def ask(self, question: str, *, now: Optional[datetime] = None, on_step: Optional[Callable[[dict], None]] = None) -> AnswerRun:
        question = (question or "").strip()
        if not question:
            raise ValueError("La pregunta está vacía")
        if self.model is None:
            raise ValueError("No hay modelo de razonamiento configurado (EPIGRAPH_AGENT_BACKEND)")
        started = time.perf_counter()
        now = (now or datetime.now(_UTC)).astimezone(self.timezone)
        run = AnswerRun(id=str(uuid.uuid4()), question=question, model_id=self.model.model_id)
        handles: dict[str, Citation] = {}
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "turns": 0}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{question}\n\n(Ahora es {now:%Y-%m-%d %H:%M} {self._tz_name()}.)"},
        ]
        tools = self.tool_specs()
        tool_calls_made = 0
        final: Optional[Turn] = None
        self._log(run, f"Pregunta: {question}")
        try:
            for step in range(1, self.max_steps + 1):
                if time.perf_counter() - started > self.max_seconds:
                    run.metadata["budget"] = "tiempo agotado"
                    break
                turn = self._chat_with_retry(messages, tools if tool_calls_made < self.max_tool_calls else None, run)
                usage["turns"] += 1
                usage["prompt_tokens"] += int(turn.usage.get("prompt_eval_count") or 0)
                usage["completion_tokens"] += int(turn.usage.get("eval_count") or 0)
                if not turn.tool_calls:
                    final = turn
                    break
                messages.append(turn.to_message())
                for call in turn.tool_calls:
                    if tool_calls_made >= self.max_tool_calls:
                        messages.append(self._tool_message(call, {"error": "presupuesto de herramientas agotado: responde con lo que tienes"}))
                        continue
                    tool_calls_made += 1
                    record = self._run_tool(call, handles, step)
                    run.steps.append(record["step"])
                    if on_step:
                        on_step(record["step"])
                    messages.append(self._tool_message(call, record["payload"]))
            if final is None:
                # Presupuesto agotado: una última vuelta sin herramientas para que responda con lo que tiene.
                messages.append({"role": "user", "content": "Se acabó el presupuesto de búsqueda. Responde ahora con lo que ya tienes, citando solo marcas recibidas, o di que no consta."})
                final = self._chat_with_retry(messages, None, run)
                usage["turns"] += 1
                usage["prompt_tokens"] += int(final.usage.get("prompt_eval_count") or 0)
                usage["completion_tokens"] += int(final.usage.get("eval_count") or 0)
                run.metadata.setdefault("budget", "vueltas agotadas")
        except ReasoningError as exc:
            run.error = str(exc)
            run.confidence = "sin_evidencia"
            run.answer = f"No se pudo consultar al modelo: {exc}"
        except Exception as exc:  # una herramienta rota no debe ocultar lo ya recuperado
            logger.exception("Fallo del agente")
            run.error = str(exc)
            run.answer = f"La consulta falló: {exc}"
        else:
            self._finish(run, final.content, handles)
        run.usage = usage
        run.took_ms = (time.perf_counter() - started) * 1000
        self.repository.save_answer_run(run)
        self._log(run, f"Respuesta ({run.confidence}, {len(run.citations)} cita(s), {run.took_ms / 1000:.1f} s)")
        return run

    def _chat_with_retry(self, messages: list[dict[str, Any]], tools, run: AnswerRun) -> Turn:
        """gpt-oss a veces emite una llamada a herramienta mal formada y Ollama
        responde 500 («error parsing tool call»); se reintenta con un aviso y,
        si persiste, se pide la respuesta sin herramientas."""
        attempts = 0
        while True:
            try:
                return self.model.chat(messages, tools)
            except ReasoningError as exc:
                if "parsing tool call" not in str(exc) or attempts >= 2:
                    raise
                attempts += 1
                run.metadata.setdefault("tool_call_retries", 0)
                run.metadata["tool_call_retries"] += 1
                self._log(run, f"llamada a herramienta mal formada (reintento {attempts})")
                if attempts == 2:
                    tools = None
                    messages = messages + [{"role": "user", "content": "Tus llamadas a herramientas no se pudieron interpretar. Responde ahora con lo que ya tienes, citando marcas recibidas, o di que no consta."}]
                else:
                    messages = messages + [{"role": "user", "content": "La última llamada a herramienta no se pudo interpretar: repítela con argumentos JSON válidos (claves y valores entre comillas dobles)."}]

    # -- Herramientas ------------------------------------------------------------------

    def _run_tool(self, call: ToolCall, handles: dict[str, Citation], step: int) -> dict[str, Any]:
        started = time.perf_counter()
        record: dict[str, Any] = {"step": step, "tool": call.name, "arguments": call.arguments, "handles": []}
        if call.name not in self.tool_names:
            record["error"] = f"herramienta no permitida: {call.name}"
            payload: dict[str, Any] = {"error": record["error"], "tools": list(self.tool_names)}
        else:
            try:
                arguments = self._arguments(call.name, call.arguments)
                record["arguments"] = arguments
                environment_id = arguments.get("environment_id")
                if environment_id and self.repository.get_environment(environment_id) is None:
                    raise ValueError(
                        f"ambiente desconocido: {environment_id}; usa browse_memory(level=environments) para ver los ids reales"
                    )
                output = self.toolbox.execute(call.name, arguments)
                payload = self._compact(output, handles, record)
            except (ValidationError, ValueError) as exc:
                record["error"] = str(exc)[:300]
                payload = {"error": record["error"]}
        record["took_ms"] = round((time.perf_counter() - started) * 1000, 1)
        self._log(None, f"{call.name}({json.dumps(call.arguments, ensure_ascii=False)[:160]}) → {record.get('result_count', 0)} resultado(s)")
        return {"step": record, "payload": payload}

    def _compact(self, output: MemoryToolOutput, handles: dict[str, Citation], record: dict[str, Any]) -> dict[str, Any]:
        """Lo que ve el modelo: corto, con marcas citables y horas locales."""
        items: list[dict[str, Any]] = []
        for moment in output.moments:
            handle = next((h for h, c in handles.items() if c.passage_id == moment.id), None)
            if handle is None:
                handle = f"m{len(handles) + 1}"
                handles[handle] = Citation(
                    handle=handle, passage_id=moment.id, kind=moment.kind, episode_id=moment.episode_id,
                    episode_external_id=moment.episode_external_id, episode_title=moment.episode_title,
                    event_id=moment.event_id, observation_id=moment.observation_id,
                    start_time=moment.start_time.isoformat() if moment.start_time else None,
                    start_offset_seconds=moment.start_offset_seconds, end_offset_seconds=moment.end_offset_seconds,
                    page_number=moment.page_number, title=moment.title, text=moment.text[:400],
                )
            record["handles"].append(handle)
            item = {
                "handle": handle,
                "kind": moment.kind,
                "episode": moment.episode_external_id or moment.episode_id,
                "when": self._local(moment.start_time),
                "at": _clock(moment.start_offset_seconds) if moment.start_offset_seconds is not None else None,
                "title": moment.title or None,
                # El texto completo (acotado), no el fragmento del índice: la
                # respuesta suele estar en el habla o el texto visible.
                "text": moment.text,
                "match": moment.snippet or None,
            }
            if moment.speech:
                item["said"] = [s.get("text") for s in moment.speech]
            if moment.visible_text:
                item["visible_text"] = moment.visible_text
            if moment.before or moment.after:
                item["before"] = [b.title for b in moment.before]
                item["after"] = [a.title for a in moment.after]
            if moment.page_number:
                item["page"] = moment.page_number
            items.append({k: v for k, v in item.items() if v not in (None, [], "")})
        payload: dict[str, Any] = {"tool": output.tool, "items": items}
        if output.summaries:
            payload["summaries"] = [
                {"episode_id": s.scope_key, "title": s.title, "summary": s.summary, "narrative": s.narrative[:1500],
                 "highlights": s.highlights[:6], "note": "texto generado: cita momentos, no este resumen"}
                for s in output.summaries
            ]
        if output.browse:
            payload["items"] = [
                {k: v for k, v in {"level": b.level, "id": b.id, "label": b.label, "external_id": b.external_id,
                                   "when": self._local(b.start_time), "count": b.count, "summary": b.summary_text,
                                   "at": _clock(b.offset_seconds) if b.offset_seconds is not None else None}.items() if v not in (None, "")}
                for b in output.browse
            ]
            payload["paging"] = {k: output.metadata.get(k) for k in ("page", "total", "has_more")}
        if output.tool == "search_moments":
            meta = output.metadata
            payload["understood"] = (meta.get("plan") or {}).get("time_expression")
            if meta.get("discarded_episodes"):
                payload["discarded_low_relevance_episodes"] = meta["discarded_episodes"][:5]
        record["result_count"] = len(payload.get("items") or []) + len(payload.get("summaries") or [])
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > self.tool_result_chars:
            # Recorta los textos largos, no la lista: las marcas deben llegar enteras.
            for item in payload.get("items") or []:
                if "text" in item:
                    item["text"] = item["text"][:300]
                item.pop("match", None)
            for s in payload.get("summaries") or []:
                s["narrative"] = s["narrative"][:400]
            text = json.dumps(payload, ensure_ascii=False)
            if len(text) > self.tool_result_chars:
                payload["items"] = (payload.get("items") or [])[: max(3, len(payload.get("items") or []) // 2)]
                payload["truncated"] = True
        return payload

    @staticmethod
    def _tool_message(call: ToolCall, payload: dict[str, Any]) -> dict[str, Any]:
        return {"role": "tool", "tool_name": call.name, "tool_call_id": call.id, "content": json.dumps(payload, ensure_ascii=False)}

    # -- Verificación ----------------------------------------------------------------------

    def _finish(self, run: AnswerRun, content: str, handles: dict[str, Citation]) -> None:
        text = _strip_leaked_reasoning((content or "").strip())
        if not _CITE.search(text):
            # Marcas escritas de otra forma («(m2)», «m2:», «m2»): se normalizan a [m2].
            text = re.sub(r"\(\s*(m\d+(?:\s*,\s*m\d+)*)\s*\)", r"[\1]", text)
            text = re.sub(r"(?<![\w\[])(m\d+)(?![\w\]])", r"[\1]", text)
        cited: list[str] = []
        dropped: list[str] = []

        def replace(match: re.Match) -> str:
            kept = []
            for handle in _HANDLE.findall(match.group(1)):
                if handle in handles:
                    if handle not in cited:
                        cited.append(handle)
                    kept.append(handle)
                else:
                    dropped.append(handle)
            return f"[{', '.join(kept)}]" if kept else ""

        cleaned = _CITE.sub(replace, text)
        cleaned = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", cleaned))
        run.answer = _dedupe_lines(cleaned.strip()) or "No consta en la memoria."
        run.citations = [handles[h].to_dict() for h in cited]
        run.dropped_citations = dropped
        run.abstained = run.answer.casefold().startswith(ABSTAIN_PREFIX) or (not cited and "no consta" in run.answer.casefold())
        if run.abstained:
            run.confidence = "sin_evidencia"
        elif cited and not dropped:
            run.confidence = "alta"
        elif cited:
            run.confidence = "media"
        else:
            run.confidence = "baja"  # afirma sin citar: se devuelve, pero marcado

    # -- Utilidades -------------------------------------------------------------------------

    def _local(self, value: Optional[datetime]) -> Optional[str]:
        return value.astimezone(self.timezone).strftime("%Y-%m-%d %H:%M") if value else None

    def _tz_name(self) -> str:
        return getattr(self.timezone, "key", None) or str(self.timezone)

    def _log(self, run: Optional[AnswerRun], message: str) -> None:
        if self.activity is None:
            return
        try:
            self.activity.info("agente", message)
        except Exception:  # pragma: no cover
            logger.debug("No se pudo registrar la actividad del agente", exc_info=True)


_LEAK_START = re.compile(
    r"^(?:We |I |I'll |The user |Need |Needs |Let's |Let me |Now |Provide |Use |Then |So |Answer |Must |Should |Check )"
)


def _strip_leaked_reasoning(text: str) -> str:
    """gpt-oss a veces vuelca su razonamiento en inglés antes de la respuesta
    («We have moments m1… Need answer: … Use citations.Alguien…»). Se quitan
    las frases iniciales que parecen ese razonamiento."""
    if not _LEAK_START.match(text):
        return text
    # Corte por frase: tras . ! ? con espacio, o pegado a una mayúscula («citations.Alguien»).
    sentences = re.split(r"(?<=[.!?])(?:\s+|(?=[A-ZÁÉÍÓÚÑ¿¡]))", text)
    kept = list(sentences)
    # Se descartan las frases de razonamiento y sus continuaciones en minúscula.
    while kept and (_LEAK_START.match(kept[0]) or kept[0][:1].islower()):
        kept.pop(0)
    remaining = " ".join(part.strip() for part in kept if part.strip()).strip()
    return remaining if len(remaining) >= 20 else text


def _dedupe_lines(text: str) -> str:
    """El modelo a veces repite la misma frase en bucle: se conserva una vez."""
    seen: set[str] = set()
    kept: list[str] = []
    for line in text.splitlines():
        key = line.strip().casefold()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _clock(seconds: Optional[float]) -> str:
    total = int(seconds or 0)
    return f"{total // 60:02d}:{total % 60:02d}"
