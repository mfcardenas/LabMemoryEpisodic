"""Red de agentes (Fase A3): planificador, localizadores en paralelo, narrador y verificador.

El agente único (``orchestrator.py``) responde bien a preguntas de un momento,
pero no a las que cruzan episodios («¿en qué capturas aparece la tarjeta y qué
se hizo después en cada una?»). La red las reparte:

1. **Enrutado**: en ``auto`` solo se planifica si la pregunta tiene señales de
   varias partes o varios episodios; si no, responde el agente único (A2).
2. **Planificador** (LLM sin herramientas, JSON validado; heurístico si falla):
   ``simple``, ``por_episodio`` (localizar un foco en n episodios y responder lo
   mismo en cada uno) o ``subpreguntas`` (2–4 preguntas distintas).
3. **Cobertura** (``find_episodes``, determinista): los episodios candidatos,
   cada uno con su mejor momento; el reordenador ve episodios distintos.
4. **Localizadores** (el bucle del agente único con presupuesto corto, en
   paralelo): uno por episodio candidato o por subpregunta; entregan hallazgos
   (frases con marcas), con un registro de citas común a toda la red.
5. **Narrador** (LLM sin herramientas): redacta solo con los hallazgos.
6. **Verificador**: citas que existen (determinista) y frases sostenidas por el
   texto de sus citas (LLM); las no sostenidas se retiran.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from episodic.application.agents.orchestrator import (
    AGENT_TOOLS,
    AnswerOrchestrator,
    CitationRegistry,
    Located,
    split_sentences,
)
from episodic.domain.answers import AnswerRun
from episodic.ports.reasoning import ReasoningError

logger = logging.getLogger(__name__)
_UTC = timezone.utc

MODES = ("auto", "simple", "red")
STRATEGIES = ("simple", "por_episodio", "subpreguntas")
AGGREGATES = ("listar", "contar", "comparar", "secuencia", "comun", "ninguna")
# Lo que puede usar un localizador dentro de un episodio (la búsqueda va acotada).
EPISODE_TOOLS = ("search_moments", "get_event", "get_event_neighbors", "get_document_passages")

# Señales de que la pregunta cruza episodios o junta varias preguntas.
_CUES = [
    (re.compile(p, re.IGNORECASE), label)
    for p, label in (
        (r"\ben\s+qu[eé]\s+(capturas|episodios|v[ií]deos|grabaciones|ambientes|ocasiones|d[ií]as)\b", "en qué capturas"),
        (r"\bcu[aá]nt[ao]s\s+(veces|capturas|ocasiones|episodios|v[ií]deos|personas|grabaciones)\b", "cuántas veces"),
        (r"\bcada\s+(captura|episodio|v[ií]deo|vez|ambiente|grabaci[oó]n|uno|una)\b", "cada"),
        (r"\ben\s+com[uú]n\b", "en común"),
        (r"\bcompar", "comparar"),
        (r"\bdiferencias?\b|\bse\s+diferencia", "diferencias"),
        (r"\btod[oa]s\s+(las|los)\s+(capturas|episodios|v[ií]deos|veces|ambientes|grabaciones)\b", "todas"),
        (r"\bsiempre\b|\bnunca\b|\bse\s+repite|\bpatr[oó]n", "repetición"),
        (r"\b(la\s+)?(primera|[uú]ltima)\s+vez\b", "primera/última vez"),
        (r"\bevoluci[oó]n\b|\ba\s+lo\s+largo\s+de\b", "evolución"),
        (r"\b(capturas|episodios|v[ií]deos|grabaciones)\s+(distint[oa]s|diferentes|donde|en\s+las?\s+que)\b", "varios episodios"),
    )
]

PLANNER_PROMPT = """Eres el planificador de la red de agentes de EpiGraph, una memoria episódica de vídeos, audio y \
documentos de varios ambientes; cada captura es un episodio. Decides CÓMO responder una pregunta; no la respondes.

Estrategias:
- "simple": basta con uno o pocos momentos concretos (qué, quién, cuándo o dónde de algo puntual), aunque no se \
sepa en qué episodio está.
- "por_episodio": hay que localizar algo en VARIOS episodios y, en cada uno, comprobar o responder lo mismo \
(«¿en qué capturas aparece X y qué se hace después?», «¿cuántas veces…?», «¿qué tienen en común las capturas \
donde…?», «¿cuándo fue la primera vez que…?»).
- "subpreguntas": la pregunta junta 2–4 preguntas distintas que se responden por separado (comparar dos cosas \
concretas, encadenar hechos).

Devuelve SOLO un objeto JSON, sin texto alrededor:
{"estrategia": "simple|por_episodio|subpreguntas",
 "foco": "qué hay que encontrar en cada episodio, en 2–8 palabras (solo por_episodio)",
 "pregunta_por_episodio": "qué responder dentro de cada episodio donde aparezca; vacío si basta con saber si aparece",
 "subpreguntas": ["solo con subpreguntas: cada una completa y autónoma"],
 "agregacion": "listar|contar|comparar|secuencia|comun|ninguna",
 "motivo": "una frase"}

Ejemplos:
«¿En qué capturas aparece una tarjeta de crédito y qué hace la persona con ella?» → \
{"estrategia": "por_episodio", "foco": "tarjeta de crédito", "pregunta_por_episodio": "¿Qué hace la persona con la \
tarjeta?", "subpreguntas": [], "agregacion": "listar", "motivo": "Un objeto en varios episodios, misma pregunta en cada uno."}
«¿Quién dijo que la pieza estaba agarrotada?» → {"estrategia": "simple", "foco": "", "pregunta_por_episodio": "", \
"subpreguntas": [], "agregacion": "ninguna", "motivo": "Un momento concreto."}
«Compara lo que se hizo en el taller con lo que se hizo en la oficina» → {"estrategia": "subpreguntas", "foco": "", \
"pregunta_por_episodio": "", "subpreguntas": ["¿Qué se hizo en el taller?", "¿Qué se hizo en la oficina?"], \
"agregacion": "comparar", "motivo": "Dos ámbitos distintos que luego se comparan."}"""

NARRATOR_PROMPT = """Eres el narrador de la red de agentes de EpiGraph. Otros agentes ya han buscado en la memoria y te \
entregan HALLAZGOS: frases con marcas [mN] que remiten a momentos reales. Redacta la respuesta usando SOLO esos \
hallazgos.

Reglas:
1. Empieza por la respuesta directa en una o dos frases (en cuántos episodios y en cuáles, qué ocurrió). Si hay \
«resumen», usa sus cifras tal cual: no cuentes líneas ni momentos.
2. Después, el detalle: UNA línea por episodio (junta en ella sus hallazgos) o por subpregunta, en orden temporal, \
con la hora local y sus marcas.
3. Si la pregunta pide relacionar (en común, diferencias, orden, cuántas veces), dilo solo si los hallazgos lo \
sostienen, citando las marcas de cada parte.
4. Toda frase con hechos termina con su marca o marcas entre corchetes, tal como vienen: «… [m3, m7].» No inventes \
marcas, horas ni hechos, ni uses conocimiento externo.
5. Los episodios «descartados» no contenían lo buscado: no los cuentes. Si ningún hallazgo responde, empieza \
exactamente por «No consta en la memoria».
6. En español, claro y breve; sin razonamientos ni notas en inglés."""

VERIFIER_PROMPT = """Eres el verificador de la red de agentes de EpiGraph. Recibes frases numeradas de una respuesta y, \
para cada una, la evidencia de los momentos que cita (descripción, lo que se dijo y el texto visible). Una frase está \
SOSTENIDA si lo esencial aparece en esa evidencia, aunque esté resumido, parafraseado o con sinónimos, o repartido \
entre sus momentos. Márcala como NO sostenida solo si inventa o contradice un hecho concreto (un objeto, una acción, \
una persona, una cantidad, una hora o unas palabras) que la evidencia no contiene. Ante la duda, sostenida.

Devuelve SOLO JSON, con motivos de menos de 12 palabras: {"no_sostenidas": [números], "motivos": {"número": "motivo"}}"""


@dataclass
class Plan:
    strategy: str = "simple"
    focus: str = ""
    per_episode_question: str = ""
    subquestions: list[str] = field(default_factory=list)
    aggregate: str = "ninguna"
    reason: str = ""
    source: str = "modelo"  # modelo | heuristica

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def composite_cues(question: str) -> list[str]:
    """Señales de que la pregunta cruza episodios o junta varias preguntas."""
    return list(dict.fromkeys(label for pattern, label in _CUES if pattern.search(question or "")))


def heuristic_plan(question: str, reason: str = "") -> Plan:
    """Plan sin modelo: si hay señales de varios episodios, mapa por episodio
    con la pregunta entera como foco; si no, agente único."""
    cues = composite_cues(question)
    if not cues:
        return Plan(strategy="simple", reason=reason or "sin señales de varios episodios", source="heuristica")
    text = question.casefold()
    aggregate = (
        "contar" if "cuántas" in text or "cuántos" in text or "cuantas" in text
        else "comun" if "en común" in text or "en comun" in text
        else "comparar" if "compar" in text or "diferencia" in text
        else "secuencia" if "primera vez" in text or "última vez" in text or "evoluci" in text
        else "listar"
    )
    focus, per_episode = _focus_from(question)
    return Plan(strategy="por_episodio", focus=focus, per_episode_question=per_episode,
                aggregate=aggregate, reason=reason or f"señales: {', '.join(cues)}", source="heuristica")


_FOCUS = re.compile(
    r"\b(?:aparecen?|se\s+ven?|hay|salen?|se\s+usan?|usan?|se\s+mencionan?|se\s+habla\s+de|se\s+dice|se\s+hace|hacen?|ocurre)\s+(.+)",
    re.IGNORECASE,
)


def _focus_from(question: str) -> tuple[str, str]:
    """Foco y pregunta por episodio sin modelo: «¿En qué capturas aparece una
    tarjeta y qué se hace con ella en cada una?» → («una tarjeta», «¿Qué se
    hace con ella?»). Si no se reconoce, la pregunta entera para ambos."""
    text = question.strip().strip("¿?").strip()
    match = _FOCUS.search(text)
    if not match:
        return question.strip(), question.strip()
    rest = match.group(1)
    parts = re.split(r"\s+y\s+(?=(?:qu[eé]|c[oó]mo|qui[eé]n|cu[aá]ndo|d[oó]nde|por\s+qu[eé])\b)", rest, maxsplit=1, flags=re.IGNORECASE)
    focus = parts[0].strip(" ,.")
    if len(parts) > 1:
        follow = re.sub(r"\s*\ben\s+cada\s+(una|uno|captura|episodio|v[ií]deo)\b", "", parts[1], flags=re.IGNORECASE).strip(" ,.")
        follow = f"¿{follow[0].upper()}{follow[1:]}?" if follow else ""
    else:
        follow = ""
    return focus or question.strip(), follow


def parse_plan(text: str, question: str) -> Plan:
    """El JSON del planificador, validado; si no sirve, ``ValueError``."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("el planificador no devolvió JSON")
    data = json.loads(match.group(0))
    strategy = str(data.get("estrategia") or "").strip().lower()
    if strategy not in STRATEGIES:
        raise ValueError(f"estrategia desconocida: {strategy!r}")
    aggregate = str(data.get("agregacion") or "ninguna").strip().lower()
    plan = Plan(
        strategy=strategy,
        focus=str(data.get("foco") or "").strip(),
        per_episode_question=str(data.get("pregunta_por_episodio") or "").strip(),
        subquestions=[str(item).strip() for item in (data.get("subpreguntas") or []) if str(item).strip()][:4],
        aggregate=aggregate if aggregate in AGGREGATES else "ninguna",
        reason=str(data.get("motivo") or "").strip()[:300],
    )
    if plan.strategy == "por_episodio" and not plan.focus:
        plan.focus = question.strip()
    if plan.strategy == "subpreguntas" and len(plan.subquestions) < 2:
        raise ValueError("subpreguntas necesita al menos dos")
    return plan


class AgentNetwork:
    """Responde con el agente único o con la red, según la pregunta y el modo."""

    def __init__(
        self,
        orchestrator: AnswerOrchestrator,
        *,
        workers: int = 2,
        map_episodes: int = 8,
        max_seconds: float = 300.0,
        verify: bool = True,
        locator_steps: int = 4,
        locator_tool_calls: int = 4,
    ) -> None:
        self.orchestrator = orchestrator
        self.workers = max(1, workers)
        self.map_episodes = max(1, map_episodes)
        self.max_seconds = max_seconds
        self.verify = verify
        self.locator_steps = locator_steps
        self.locator_tool_calls = locator_tool_calls

    @property
    def enabled(self) -> bool:
        return self.orchestrator.enabled

    @property
    def model(self):
        return self.orchestrator.model

    @property
    def timezone(self):
        return self.orchestrator.timezone

    def describe(self) -> dict[str, Any]:
        return {
            "modes": list(MODES), "workers": self.workers, "map_episodes": self.map_episodes,
            "max_seconds": self.max_seconds, "verify": self.verify,
            "roles": ["planificador", "localizador", "narrador", "verificador"],
        }

    # -- Entrada ---------------------------------------------------------------------------

    def ask(
        self,
        question: str,
        *,
        mode: str = "auto",
        run_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> AnswerRun:
        question = (question or "").strip()
        if not question:
            raise ValueError("La pregunta está vacía")
        if mode not in MODES:
            raise ValueError(f"Modo desconocido: {mode} (use {', '.join(MODES)})")
        if self.model is None:
            raise ValueError("No hay modelo de razonamiento configurado (EPIGRAPH_AGENT_BACKEND)")
        cues = composite_cues(question)
        routing = {"requested": mode, "cues": cues}
        if mode == "simple" or (mode == "auto" and not cues):
            run = AnswerRun(id=run_id or str(uuid.uuid4()), question=question, model_id=self.model.model_id)
            routing["decided"] = "simple"
            run.metadata["routing"] = routing
            self._trace(run, "question", f"Pregunta: {question}", model=self.model.model_id)
            self._trace(run, "route", "Enrutado: agente único" + (" (modo elegido)" if mode == "simple" else " (sin señales de varios episodios)"),
                        decided="simple", cues=cues)
            return self.orchestrator.ask(question, now=now, run=run)
        return self._network(question, mode, routing, run_id, now)

    # -- Red -------------------------------------------------------------------------------

    def _network(self, question: str, mode: str, routing: dict, run_id: Optional[str], now: Optional[datetime]) -> AnswerRun:
        started = time.perf_counter()
        deadline = started + self.max_seconds
        now = (now or datetime.now(_UTC)).astimezone(self.timezone)
        run = AnswerRun(id=run_id or str(uuid.uuid4()), question=question, model_id=self.model.model_id)
        run.metadata["routing"] = routing
        run.metadata["agents"] = []
        self._trace(run, "question", f"Pregunta: {question}", model=self.model.model_id)
        self._trace(run, "route", "Enrutado: red de agentes" + (" (modo elegido)" if mode == "red" else f" (señales: {', '.join(routing['cues'])})"),
                    decided="red", cues=routing["cues"])
        plan = self._plan(run, question, now)
        run.metadata["plan"] = plan.to_dict()
        if plan.strategy == "simple":
            routing["decided"] = "simple"
            run.took_ms = (time.perf_counter() - started) * 1000
            self._trace(run, "route", "El planificador la ve simple: responde el agente único", decided="simple")
            return self.orchestrator.ask(question, now=now, run=run)

        routing["decided"] = "red"
        run.metadata["mode"] = "red"
        handles = CitationRegistry()
        # Los localizadores dejan tiempo para el narrador y el verificador.
        locate_deadline = started + self.max_seconds * 0.75
        if plan.strategy == "por_episodio":
            branches = self._map_episodes(run, plan, handles, now, locate_deadline)
        else:
            branches = self._subquestions(run, plan, handles, now, locate_deadline)
        run.metadata["branches"] = branches

        content = self._narrate(run, question, plan, branches, handles)
        if content is None:
            run.took_ms = (time.perf_counter() - started) * 1000
            self._finalize(run)
            return run
        self.orchestrator._finish(run, content, handles)
        self.orchestrator._trace_verified(run)
        if self.verify and run.citations and time.perf_counter() < deadline:
            self._verify(run, handles)
        self._grade(run)
        note = self._coverage_note(run)
        if note:
            run.answer = f"{run.answer}\n\n{note}"
        run.took_ms = (time.perf_counter() - started) * 1000
        self._finalize(run)
        return run

    # -- Planificador ---------------------------------------------------------------------

    def _plan(self, run: AnswerRun, question: str, now: datetime) -> Plan:
        started = time.perf_counter()
        messages = [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": f"{question}\n\n(Ahora es {now:%Y-%m-%d %H:%M}.)"},
        ]
        usage = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0}
        try:
            turn = self.orchestrator._chat_with_retry(messages, None, run, "planificador")
            self._add_usage(usage, turn.usage)
            plan = parse_plan(turn.content, question)
        except (ReasoningError, ValueError) as exc:
            plan = heuristic_plan(question, reason=f"plan del modelo no válido ({exc}); heurístico")
            self._trace(run, "retry", f"planificador: {plan.reason}", agent="planificador")
        took = round((time.perf_counter() - started) * 1000, 1)
        detail = {
            "simple": "responder con el agente único",
            "por_episodio": f"localizar «{plan.focus}» en varios episodios" + (f" y, en cada uno: {plan.per_episode_question}" if plan.per_episode_question else ""),
            "subpreguntas": " · ".join(plan.subquestions),
        }[plan.strategy]
        self._trace(run, "plan", f"planificador: {plan.strategy} — {detail} (agregación: {plan.aggregate}; {took / 1000:.1f} s)",
                    agent="planificador", plan=plan.to_dict(), took_ms=took)
        run.metadata["agents"].append({"name": "planificador", "role": "planificador", "model": self.model.model_id,
                                       "turns": usage["turns"], "tool_calls": 0, "took_ms": took, "source": plan.source})
        self._merge_usage(run, usage)
        return plan

    # -- Mapa por episodio ----------------------------------------------------------------

    def _map_episodes(self, run: AnswerRun, plan: Plan, handles: CitationRegistry, now: datetime, deadline: float) -> list[dict]:
        started = time.perf_counter()
        output = self.orchestrator.toolbox.execute(
            "find_episodes", {"query": plan.focus, "max_episodes": self.map_episodes, "per_episode": 2}
        )
        episodes = output.metadata.get("episodes", [])
        more = int(output.metadata.get("more_candidates") or 0)
        by_episode: dict[str, list] = {}
        for moment in output.moments:
            by_episode.setdefault(moment.episode_id, []).append(moment)
        took = round((time.perf_counter() - started) * 1000, 1)
        below = int(output.metadata.get("below_floor") or 0)
        run.metadata["coverage"] = {"focus": plan.focus, "candidates": len(episodes), "more_candidates": more,
                                    "below_floor": below, "took_ms": took}
        names = ", ".join(f"{e.get('external_id') or e['episode_id']} ({e.get('best_score')})" for e in episodes) or "ninguno"
        self._trace(run, "map", f"Cobertura: {len(episodes)} episodio(s) candidato(s) para «{plan.focus}»"
                    + (f", {below} por debajo del umbral de relevancia" if below else "") + (f", +{more} sin revisar" if more else "")
                    + f": {names} · {took / 1000:.1f} s",
                    episodes=[e["episode_id"] for e in episodes], more_candidates=more, below_floor=below, took_ms=took)
        if not episodes:
            return []
        question = (
            f"¿Aparece {plan.focus} en este episodio? Si aparece, {plan.per_episode_question}"
            if plan.per_episode_question else f"¿Aparece {plan.focus} en este episodio? Si aparece, ¿cuándo y cómo?"
        )
        jobs = []
        for index, episode in enumerate(episodes, 1):
            context = self.orchestrator.moment_items(by_episode.get(episode["episode_id"], []), handles)
            jobs.append((index, episode, context))

        def work(job):
            index, episode, context = job
            agent = f"localizador {index} · {episode.get('external_id') or episode['episode_id']}"
            located = self.orchestrator.locate(
                question, run=run, handles=handles, agent=agent, now=now, context=context,
                scope=self._episode_scope(episode), scope_note=self._episode_note(episode),
                tool_names=EPISODE_TOOLS, max_steps=self.locator_steps, max_tool_calls=self.locator_tool_calls,
                deadline=deadline,
            )
            return episode, located

        branches = []
        for episode, located in self._parallel(work, jobs):
            branches.append({
                "kind": "episode", "episode_id": episode["episode_id"], "external_id": episode.get("external_id"),
                "environment_id": episode.get("environment_id"), "start_time": episode.get("start_time"),
                "status": "error" if located.error else ("descartado" if located.abstained else "confirmado"),
                **self._branch(run, located),
            })
        branches.sort(key=lambda item: item.get("start_time") or "")
        confirmed = sum(1 for item in branches if item["status"] == "confirmado")
        run.metadata["coverage"].update({"confirmed": confirmed, "discarded": len(branches) - confirmed})
        return branches

    def _episode_scope(self, episode: dict) -> dict[str, dict[str, Any]]:
        start = datetime.fromisoformat(episode["start_time"]) if episode.get("start_time") else None
        end = datetime.fromisoformat(episode["end_time"]) if episode.get("end_time") else start
        search: dict[str, Any] = {"max_episodes": 1, "per_episode": 4}
        if episode.get("environment_id"):
            search["environment_ids"] = [episode["environment_id"]]
        if start is not None:
            search["start_time"] = (start - timedelta(seconds=1)).isoformat()
            search["end_time"] = ((end or start) + timedelta(seconds=1)).isoformat()
        return {"search_moments": search, "get_document_passages": {"episode_id": episode["episode_id"]}}

    def _episode_note(self, episode: dict) -> str:
        when = ""
        if episode.get("start_time"):
            when = datetime.fromisoformat(episode["start_time"]).astimezone(self.timezone).strftime("%Y-%m-%d %H:%M")
        label = episode.get("external_id") or episode["episode_id"]
        return f"solo el episodio {label} (ambiente {episode.get('environment_id') or '—'}, {when})"

    # -- Subpreguntas -----------------------------------------------------------------------

    def _subquestions(self, run: AnswerRun, plan: Plan, handles: CitationRegistry, now: datetime, deadline: float) -> list[dict]:
        def locate(index: int, question: str, context_note: str = "") -> Located:
            return self.orchestrator.locate(
                question, run=run, handles=handles, agent=f"localizador {index}", now=now,
                scope_note=context_note, tool_names=AGENT_TOOLS,
                max_steps=self.locator_steps + 1, max_tool_calls=self.locator_tool_calls + 1, deadline=deadline,
            )

        results: list[tuple[int, str, Located]] = []
        if plan.aggregate == "secuencia":
            # Encadenadas: cada una sabe lo que encontraron las anteriores.
            known: list[str] = []
            for index, question in enumerate(plan.subquestions, 1):
                note = ("Ya se sabe: " + " ".join(known)) if known else ""
                located = locate(index, question, note)
                known += [finding["text"] for finding in located.findings]
                results.append((index, question, located))
        else:
            jobs = list(enumerate(plan.subquestions, 1))
            results = [(index, question, located) for (index, question), located in
                       zip(jobs, self._parallel(lambda job: locate(*job), jobs))]
        return [{
            "kind": "subquestion", "index": index, "question": question,
            "status": "error" if located.error else ("sin hallazgos" if located.abstained else "respondida"),
            **self._branch(run, located),
        } for index, question, located in results]

    # -- Narrador ------------------------------------------------------------------------------

    def _narrate(self, run: AnswerRun, question: str, plan: Plan, branches: list[dict], handles: CitationRegistry) -> Optional[str]:
        started = time.perf_counter()
        useful = [b for b in branches if b.get("findings")]
        if not useful:
            run.answer = (
                "No consta en la memoria: ningún episodio candidato contenía lo buscado."
                if plan.strategy == "por_episodio" else "No consta en la memoria: ninguna subpregunta encontró momentos."
            )
            run.abstained = True
            run.confidence = "sin_evidencia"
            self._trace(run, "narrate", "narrador: sin hallazgos que contar; no consta", agent="narrador")
            return None
        cited = {h for b in useful for f in b["findings"] for h in f["handles"]}
        moments = {}
        for handle in sorted(cited, key=lambda h: int(h[1:])):
            citation = handles[handle]
            moments[handle] = {k: v for k, v in {
                "cuando": self._local(citation.start_time), "en_video": _clock(citation.start_offset_seconds),
                "episodio": citation.episode_external_id or citation.episode_id,
                "evidencia": handles.evidence(handle, 320),
            }.items() if v}
        payload = {
            "pregunta": question,
            "agregacion": plan.aggregate,
            "ramas": [{k: v for k, v in {
                "episodio": b.get("external_id") or b.get("episode_id"), "subpregunta": b.get("question"),
                "ambiente": b.get("environment_id"), "cuando": self._local(b.get("start_time")),
                "estado": b["status"], "hallazgos": [f["text"] for f in b.get("findings", [])],
            }.items() if v} for b in branches],
            "momentos": moments,
        }
        coverage = run.metadata.get("coverage") or {}
        if plan.strategy == "por_episodio":
            # Las cifras se calculan aquí: el modelo no debe contar líneas ni momentos.
            payload["resumen"] = {
                "episodios_revisados": len(branches),
                "episodios_donde_aparece": sum(1 for b in branches if b["status"] == "confirmado"),
                "episodios_donde_no_aparece": sum(1 for b in branches if b["status"] == "descartado"),
                "episodios_sin_revisar": coverage.get("more_candidates", 0),
            }
        messages = [
            {"role": "system", "content": NARRATOR_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        usage = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0}
        try:
            turn = self.orchestrator._chat_with_retry(messages, None, run, "narrador")
            self._add_usage(usage, turn.usage)
            content = turn.content
        except ReasoningError as exc:
            # Sin narrador, la respuesta son los hallazgos tal cual, por rama.
            self._trace(run, "error", f"narrador: el modelo falló ({exc}); se devuelven los hallazgos", agent="narrador")
            content = "\n".join(f"- {f['text']}" for b in useful for f in b["findings"])
        took = round((time.perf_counter() - started) * 1000, 1)
        self._trace(run, "narrate", f"narrador: redacta con {sum(len(b['findings']) for b in useful)} hallazgo(s) de {len(useful)} rama(s) ({took / 1000:.1f} s)",
                    agent="narrador", took_ms=took)
        run.metadata["agents"].append({"name": "narrador", "role": "narrador", "model": self.model.model_id,
                                       "turns": usage["turns"], "tool_calls": 0, "took_ms": took})
        self._merge_usage(run, usage)
        return content

    # -- Verificador ----------------------------------------------------------------------------

    def _verify(self, run: AnswerRun, handles: CitationRegistry) -> None:
        started = time.perf_counter()
        sentences = split_sentences(run.answer)
        checked = []
        for index, sentence in enumerate(sentences, 1):
            cited = re.findall(r"m\d+", " ".join(re.findall(r"\[([^\]]+)\]", sentence)))
            cited = [h for h in dict.fromkeys(cited) if h in handles]
            if cited:
                checked.append({"n": index, "frase": sentence,
                                "evidencia": {h: handles.evidence(h) for h in cited}})
        if not checked:
            return
        messages = [
            {"role": "system", "content": VERIFIER_PROMPT},
            {"role": "user", "content": json.dumps(checked, ensure_ascii=False)},
        ]
        usage = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0}
        unsupported: list[int] = []
        reasons: dict[str, str] = {}
        try:
            turn = self.orchestrator._chat_with_retry(messages, None, run, "verificador")
            self._add_usage(usage, turn.usage)
            match = re.search(r"\{.*\}", turn.content or "", re.DOTALL)
            data = json.loads(match.group(0)) if match else {}
            valid = {item["n"] for item in checked}
            unsupported = sorted({int(n) for n in data.get("no_sostenidas") or [] if str(n).isdigit() and int(n) in valid})
            reasons = {str(k): str(v)[:200] for k, v in (data.get("motivos") or {}).items() if str(k).isdigit() and int(k) in unsupported}
        except (ReasoningError, ValueError, TypeError) as exc:
            self._trace(run, "retry", f"verificador: no se pudo verificar ({exc}); se mantiene la respuesta", agent="verificador")
        # Salvaguarda: si rechaza más de un tercio de lo contrastado, el fallo es más
        # probable en el verificador que en la respuesta; no se borra nada y se avisa.
        inconclusive = len(unsupported) > max(1, len(checked) // 3)
        removed = [] if inconclusive else [sentences[n - 1] for n in unsupported]
        if removed:
            kept = [s for i, s in enumerate(sentences, 1) if i not in unsupported]
            self.orchestrator._finish(run, "\n".join(kept), handles)
        took = round((time.perf_counter() - started) * 1000, 1)
        run.metadata["verification"] = {
            "checked": len(checked), "removed": removed, "reasons": reasons, "took_ms": took,
            "inconclusive": inconclusive, "flagged": [sentences[n - 1] for n in unsupported] if inconclusive else [],
        }
        verdict = (
            f"no concluyente: rechaza {len(unsupported)} de {len(checked)}; se mantiene la respuesta" if inconclusive
            else f"{len(removed)} retirada(s) por no estar sostenidas" if removed else "todas sostenidas"
        )
        self._trace(run, "verify", f"verificador: {len(checked)} frase(s) contrastadas con sus citas; {verdict} ({took / 1000:.1f} s)",
                    agent="verificador", checked=len(checked), removed=len(removed), inconclusive=inconclusive, took_ms=took)
        run.metadata["agents"].append({"name": "verificador", "role": "verificador", "model": self.model.model_id,
                                       "turns": usage["turns"], "tool_calls": 0, "took_ms": took})
        self._merge_usage(run, usage)

    def _grade(self, run: AnswerRun) -> None:
        """Confianza de la red: retirar frases o afirmar sin cita la baja a media."""
        if run.abstained or not run.citations:
            return
        sentences = split_sentences(run.answer)
        confirmed = (run.metadata.get("coverage") or {}).get("confirmed")
        if sentences and confirmed is not None and str(confirmed) in sentences[0]:
            # La frase de cabecera da la cifra que calculó la red, no un hecho a citar.
            sentences = sentences[1:]
        uncited = [s for s in sentences if "[" not in s and len(s) > 60 and not s.rstrip().endswith(":") and not s.startswith("**")]
        run.metadata["uncited_sentences"] = len(uncited)
        verification = run.metadata.get("verification") or {}
        if verification.get("removed") or verification.get("inconclusive") or uncited:
            run.confidence = "media" if run.confidence == "alta" else run.confidence

    def _coverage_note(self, run: AnswerRun) -> str:
        coverage = run.metadata.get("coverage") or {}
        if coverage.get("more_candidates"):
            return (f"(Se revisaron {coverage.get('candidates', 0)} episodios candidatos; quedan "
                    f"{coverage['more_candidates']} más sin revisar. Afine la pregunta o amplíe EPIGRAPH_AGENT_MAP_EPISODES.)")
        return ""

    # -- Utilidades ---------------------------------------------------------------------------

    def _branch(self, run: AnswerRun, located: Located) -> dict:
        run.metadata["agents"].append({
            "name": located.agent, "role": "localizador", "model": self.model.model_id,
            "turns": located.usage.get("turns", 0), "tool_calls": located.tool_calls, "took_ms": located.took_ms,
            "findings": len(located.findings), "error": located.error,
        })
        self._merge_usage(run, located.usage)
        return {"agent": located.agent, "findings": located.findings, "text": located.text, "error": located.error,
                "took_ms": located.took_ms}

    def _parallel(self, work, jobs: list) -> list:
        if self.workers == 1 or len(jobs) <= 1:
            return [work(job) for job in jobs]
        with ThreadPoolExecutor(max_workers=min(self.workers, len(jobs)), thread_name_prefix="epigraph-agente") as pool:
            return list(pool.map(work, jobs))

    def _finalize(self, run: AnswerRun) -> None:
        agents = run.metadata.get("agents", [])
        run.metadata["agent_count"] = len(agents)
        self._trace(run, "answer", f"Respuesta ({run.confidence}, {len(run.citations)} cita(s), {len(agents)} agente(s), "
                    f"{run.usage.get('turns', 0)} vuelta(s) del modelo, {run.took_ms / 1000:.1f} s)",
                    confidence=run.confidence, citations=len(run.citations), agents=len(agents),
                    turns=run.usage.get("turns", 0), took_ms=round(run.took_ms, 1))
        self.orchestrator.repository.save_answer_run(run)

    def _trace(self, run: AnswerRun, kind: str, message: str, **data: Any) -> None:
        self.orchestrator._trace(run, kind, message, **data)

    @staticmethod
    def _add_usage(usage: dict, turn_usage: dict) -> None:
        usage["turns"] += 1
        usage["prompt_tokens"] += int(turn_usage.get("prompt_eval_count") or 0)
        usage["completion_tokens"] += int(turn_usage.get("eval_count") or 0)

    @staticmethod
    def _merge_usage(run: AnswerRun, usage: dict) -> None:
        for key in ("turns", "prompt_tokens", "completion_tokens"):
            run.usage[key] = run.usage.get(key, 0) + int(usage.get(key, 0) or 0)

    def _local(self, value: Any) -> Optional[str]:
        if not value:
            return None
        moment = datetime.fromisoformat(value) if isinstance(value, str) else value
        return moment.astimezone(self.timezone).strftime("%Y-%m-%d %H:%M")


def _clock(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"
