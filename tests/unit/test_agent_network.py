"""Red de agentes (Fase A3): enrutado, plan, mapa por episodio, narrador y verificador."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone

import pytest

from episodic.application.agents.network import (
    AgentNetwork,
    composite_cues,
    heuristic_plan,
    parse_plan,
)
from episodic.application.agents.orchestrator import AnswerOrchestrator
from episodic.domain.answers import AnswerRun
from episodic.ports.reasoning import ToolCall, Turn
from episodic.tools.contracts import MemoryToolOutput, MomentRecord, ToolDescriptor

T0 = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
EPISODES = {
    "ep-a": ("A001", "captura_a", 0, "La persona sostiene una tarjeta de crédito frente a la tablet."),
    "ep-b": ("A002", "captura_b", 10, "Alguien guarda la tarjeta en la cartera."),
    "ep-c": ("A003", "captura_c", 20, "Un mecánico corta una pieza; no hay tarjeta."),
}


def _moment(episode_id: str, suffix: str = "") -> MomentRecord:
    env, external, minute, text = EPISODES[episode_id]
    return MomentRecord(id=f"ps-{episode_id}{suffix}", kind="video_event", event_id=f"ev-{episode_id}{suffix}",
                        episode_id=episode_id, episode_external_id=external, environment_id=env, title=text[:30],
                        text=text, start_time=T0.replace(minute=minute), start_offset_seconds=3.0)


class StubToolbox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.lock = threading.Lock()

    def describe(self):
        names = ["search_moments", "browse_memory", "get_event", "get_event_neighbors", "get_episode_summary",
                 "get_document_passages", "find_episodes"]
        return [ToolDescriptor(name=n, description=n, input_schema={"type": "object", "properties": {}}, output_schema={"type": "object"})
                for n in names]

    def execute(self, name, payload):
        with self.lock:
            self.calls.append((name, payload))
        if name == "find_episodes":
            moments = [_moment(e) for e in EPISODES]
            episodes = [{"episode_id": e, "external_id": EPISODES[e][1], "environment_id": EPISODES[e][0],
                         "start_time": T0.replace(minute=EPISODES[e][2]).isoformat(),
                         "end_time": T0.replace(minute=EPISODES[e][2], second=30).isoformat(), "matches": 1} for e in EPISODES]
            return MemoryToolOutput(tool=name, moments=moments, metadata={"episodes": episodes, "more_candidates": 2})
        if name == "search_moments":
            env = (payload.get("environment_ids") or ["A001"])[0]
            episode = next(e for e, value in EPISODES.items() if value[0] == env)
            return MemoryToolOutput(tool=name, moments=[_moment(episode, "-x")], metadata={})
        if name == "get_event_neighbors":
            episode = payload["event_id"].removeprefix("ev-")
            return MemoryToolOutput(tool=name, moments=[_moment(episode, "-after")], metadata={})
        raise KeyError(name)


class StubRepository:
    def __init__(self) -> None:
        self.saved: list[AnswerRun] = []

    def save_answer_run(self, run: AnswerRun) -> None:
        self.saved.append(run)

    def get_environment(self, environment_id):
        return {"id": environment_id}


class RoleModel:
    """Responde según el papel (el prompt de sistema) y registra quién habló."""

    model_id = "fake-red"

    def __init__(self, plan: str | None = None, narrator: str | None = None, verifier: str = '{"no_sostenidas": []}') -> None:
        self.plan = plan
        self.narrator = narrator
        self.verifier = verifier
        self.roles: list[str] = []
        self.lock = threading.Lock()
        self.messages: dict[str, list] = {}

    def chat(self, messages, tools=None):
        system = messages[0]["content"]
        role = ("planificador" if system.startswith("Eres el planificador") else "localizador" if system.startswith("Eres un localizador")
                else "narrador" if system.startswith("Eres el narrador") else "verificador" if system.startswith("Eres el verificador")
                else "agente")
        with self.lock:
            self.roles.append(role)
            self.messages.setdefault(role, []).append([dict(m) for m in messages])
        if role == "planificador":
            return Turn(content=self.plan or "", usage={"eval_count": 5})
        if role == "narrador":
            return Turn(content=self.narrator or "Aparece en dos capturas [m1, m2].")
        if role == "verificador":
            return Turn(content=self.verifier)
        if role == "localizador":
            return self._locate(messages, tools)
        # agente único: busca y cita la primera marca
        if messages[-1]["role"] == "user":
            return Turn(tool_calls=[ToolCall(id="c1", name="search_moments", arguments={"query": "x"})])
        handles = re.findall(r'"handle":\s*"(m\d+)"', messages[-1]["content"])
        return Turn(content=f"Ocurrió así [{handles[0]}].")

    def _locate(self, messages, tools):
        first = messages[1]["content"]
        handles = re.findall(r'"handle":\s*"(m\d+)"', first)
        if "captura_c" in first:
            # El localizador de C busca en su alcance antes de concluir.
            if len(messages) == 2:
                return Turn(tool_calls=[ToolCall(id="c1", name="search_moments", arguments={"query": "tarjeta"})])
            return Turn(content="No consta en este alcance: busqué la tarjeta en la captura.")
        if "captura_b" in first and len(messages) == 2:
            # Pide lo que pasó después pasando la marca como event_id.
            return Turn(tool_calls=[ToolCall(id="c1", name="get_event_neighbors", arguments={"event_id": handles[0]})])
        last = re.findall(r'"handle":\s*"(m\d+)"', messages[-1]["content"]) if messages[-1]["role"] == "tool" else []
        cites = ", ".join(handles + last)
        return Turn(content=f"La tarjeta aparece y se usa [{cites}].\nNota sin marca que se ignora.")


def _network(model, **kwargs) -> tuple[AgentNetwork, StubToolbox, StubRepository]:
    toolbox, repo = StubToolbox(), StubRepository()
    orchestrator = AnswerOrchestrator(repo, toolbox, model, timezone=timezone.utc)
    return AgentNetwork(orchestrator, **{"workers": 1, **kwargs}), toolbox, repo


PLAN_MAP = json.dumps({"estrategia": "por_episodio", "foco": "tarjeta de crédito",
                       "pregunta_por_episodio": "¿Qué se hace con la tarjeta?", "subpreguntas": [],
                       "agregacion": "listar", "motivo": "varios episodios"})
COMPOSITE = "¿En qué capturas aparece una tarjeta de crédito y qué se hace con ella en cada una?"


def test_cues_and_plans_are_parsed_and_validated() -> None:
    assert composite_cues(COMPOSITE) == ["en qué capturas", "cada"]
    assert composite_cues("¿Quién dijo que la pieza estaba agarrotada?") == []
    assert heuristic_plan("¿Cuántas veces se usa la radial?").aggregate == "contar"
    assert heuristic_plan("¿Quién habló?").strategy == "simple"
    plan = parse_plan(f"Plan: {PLAN_MAP} listo", COMPOSITE)
    assert (plan.strategy, plan.focus, plan.aggregate) == ("por_episodio", "tarjeta de crédito", "listar")
    with pytest.raises(ValueError):
        parse_plan('{"estrategia": "subpreguntas", "subpreguntas": ["solo una"]}', COMPOSITE)
    with pytest.raises(ValueError):
        parse_plan("no es json", COMPOSITE)


def test_auto_mode_sends_simple_questions_to_the_single_agent_without_planning() -> None:
    model = RoleModel()
    network, _, repo = _network(model)
    run = network.ask("¿Quién dijo que la pieza estaba agarrotada?")
    assert model.roles == ["agente", "agente"]  # ni planificador ni red
    assert run.metadata["mode"] == "simple" and run.metadata["routing"]["decided"] == "simple"
    assert [item["kind"] for item in run.trace][:2] == ["question", "route"] and repo.saved == [run]


def test_map_by_episode_confirms_discards_verifies_and_shares_citations() -> None:
    narrator = ("Aparece en dos capturas: captura_a y captura_b [m1, m2].\n"
                "En captura_a la persona sostiene la tarjeta frente a la tablet [m1].\n"
                "En captura_b alguien la guarda en la cartera [m2, m4].\n"
                "En captura_c la tarjeta se rompe [m3].")
    model = RoleModel(plan=PLAN_MAP, narrator=narrator, verifier='{"no_sostenidas": [4], "motivos": {"4": "no se rompe"}}')
    network, toolbox, repo = _network(model)
    run = network.ask(COMPOSITE, run_id="red-0001")

    assert run.metadata["mode"] == "red" and run.metadata["plan"]["focus"] == "tarjeta de crédito"
    branches = {b["external_id"]: b for b in run.metadata["branches"]}
    assert [b["external_id"] for b in run.metadata["branches"]] == ["captura_a", "captura_b", "captura_c"]
    assert branches["captura_a"]["status"] == "confirmado" and branches["captura_c"]["status"] == "descartado"
    # Hallazgos: solo las frases con marca válida; la nota sin marca se ignora.
    assert [f["text"] for f in branches["captura_a"]["findings"]] == ["La tarjeta aparece y se usa [m1]."]
    # El localizador de B pasó la marca m2 como event_id: la herramienta recibió el id real.
    assert ("get_event_neighbors", {"before": 2, "after": 2, "max_chars": 500, "event_id": "ev-ep-b"}) in toolbox.calls
    # El de C buscó dentro de su alcance (ambiente e intervalo del episodio), aunque el modelo no lo pidió.
    search = next(payload for name, payload in toolbox.calls if name == "search_moments")
    assert search["environment_ids"] == ["A003"] and search["max_episodes"] == 1 and search["start_time"].startswith("2026-10-12T16:19:59")
    # Marcas comunes a toda la red: m1..m3 de la cobertura, m4 el vecino de B, m5 la búsqueda de C.
    assert branches["captura_b"]["findings"][0]["handles"] == ["m2", "m4"]
    # El verificador retiró la frase no sostenida; la confianza baja a media.
    assert "se rompe" not in run.answer and run.metadata["verification"]["removed"] == ["En captura_c la tarjeta se rompe [m3]."]
    assert run.confidence == "media" and [c["handle"] for c in run.citations] == ["m1", "m2", "m4"]
    assert run.metadata["coverage"] == {"focus": "tarjeta de crédito", "candidates": 3, "more_candidates": 2, "below_floor": 0,
                                        "took_ms": run.metadata["coverage"]["took_ms"], "confirmed": 2, "discarded": 1}
    assert "quedan 2 más sin revisar" in run.answer
    roles = [agent["role"] for agent in run.metadata["agents"]]
    assert roles == ["planificador", "localizador", "localizador", "localizador", "narrador", "verificador"]
    assert run.metadata["agent_count"] == 6 and run.usage["turns"] >= 8
    # La traza dice quién hizo qué.
    kinds = [item["kind"] for item in run.trace]
    assert kinds[:4] == ["question", "route", "plan", "map"] and kinds[-1] == "answer" and "narrate" in kinds
    assert any(item.get("agent") == "localizador 2 · captura_b" and item["kind"] == "tool" for item in run.trace)
    assert repo.saved == [run]


def test_planner_failure_falls_back_to_the_heuristic_plan() -> None:
    model = RoleModel(plan="esto no es un plan")
    network, _, _ = _network(model)
    run = network.ask(COMPOSITE)
    plan = run.metadata["plan"]
    assert plan["source"] == "heuristica" and plan["strategy"] == "por_episodio" and plan["focus"] == "una tarjeta de crédito"
    assert plan["per_episode_question"] == "¿Qué se hace con ella?"
    assert run.metadata["mode"] == "red"


def test_planner_can_decide_the_question_is_simple() -> None:
    model = RoleModel(plan=json.dumps({"estrategia": "simple", "agregacion": "ninguna", "motivo": "un momento"}))
    network, _, repo = _network(model)
    run = network.ask("¿Cuántas veces dijo la palabra latiguillo en la captura del taller?", mode="red")
    assert [a["role"] for a in run.metadata["agents"]] == ["planificador", "agente"]
    assert run.metadata["routing"]["decided"] == "simple" and sum(1 for i in run.trace if i["kind"] == "question") == 1
    assert run.citations and repo.saved == [run]


def test_without_findings_the_network_abstains() -> None:
    model = RoleModel(plan=PLAN_MAP)
    network, toolbox, _ = _network(model)
    toolbox.execute = lambda name, payload: MemoryToolOutput(tool=name, metadata={"episodes": [], "more_candidates": 0})
    run = network.ask(COMPOSITE)
    assert run.abstained and run.confidence == "sin_evidencia" and run.answer.startswith("No consta en la memoria")
    assert "narrador" not in model.roles


def test_sequence_subquestions_pass_what_is_already_known() -> None:
    plan = json.dumps({"estrategia": "subpreguntas", "subpreguntas": ["¿Qué pasó en captura_a?", "¿Y después en captura_b?"],
                       "agregacion": "secuencia"})
    model = RoleModel(plan=plan, narrator="Primero se sostuvo la tarjeta [m1] y luego se guardó [m2].")
    network, _, _ = _network(model)

    def locate(messages, tools):
        handle = "m1" if "captura_a" in messages[1]["content"] and "Ya se sabe" not in messages[1]["content"] else "m2"
        if len(messages) == 2:
            env = "A001" if handle == "m1" else "A002"
            return Turn(tool_calls=[ToolCall(id="c", name="search_moments", arguments={"query": "x", "environment_ids": [env]})])
        found = re.findall(r'"handle":\s*"(m\d+)"', messages[-1]["content"])
        return Turn(content=f"Hecho encontrado [{found[0]}].")

    model._locate = locate
    run = network.ask("Primero, ¿qué pasó en captura_a? Y luego, en todas las capturas, ¿qué vino después?")
    second = next(m[1]["content"] for m in model.messages["localizador"] if "Y después" in m[1]["content"])
    assert "Ya se sabe: Hecho encontrado [m1]." in second
    assert [b["status"] for b in run.metadata["branches"]] == ["respondida", "respondida"]


def test_parallel_locators_give_the_same_answer() -> None:
    narrator = "Aparece en captura_a [m1] y en captura_b [m2]."
    serial, _, _ = _network(RoleModel(plan=PLAN_MAP, narrator=narrator), workers=1)
    parallel, _, _ = _network(RoleModel(plan=PLAN_MAP, narrator=narrator), workers=3)
    a, b = serial.ask(COMPOSITE), parallel.ask(COMPOSITE)
    assert [x["status"] for x in a.metadata["branches"]] == [x["status"] for x in b.metadata["branches"]]
    assert {c["handle"] for c in a.citations} == {c["handle"] for c in b.citations} == {"m1", "m2"}


def test_rerank_head_takes_at_most_k_passages_per_episode() -> None:
    from types import SimpleNamespace

    from episodic.application.passage_search import _rerank_head

    hits = [SimpleNamespace(episode_id=e, n=i) for i, e in enumerate("aaaaabbbbc")]
    assert [h.episode_id for h in _rerank_head(hits, 4, None)] == ["a", "a", "a", "a"]
    assert [h.episode_id for h in _rerank_head(hits, 4, 1)] == ["a", "b", "c"]
    assert [h.episode_id for h in _rerank_head(hits, 5, 2)] == ["a", "a", "b", "b", "c"]


def test_a_verifier_that_rejects_too_much_is_inconclusive_and_removes_nothing() -> None:
    narrator = "Aparece en captura_a [m1].\nY en captura_b [m2]."
    model = RoleModel(plan=PLAN_MAP, narrator=narrator, verifier='{"no_sostenidas": [1, 2]}')
    network, _, _ = _network(model)
    run = network.ask(COMPOSITE)
    verification = run.metadata["verification"]
    assert verification["inconclusive"] and verification["removed"] == [] and len(verification["flagged"]) == 2
    assert "[m1]" in run.answer and "[m2]" in run.answer and run.confidence == "media"


def test_verifier_and_narrator_see_what_the_locator_saw() -> None:
    model = RoleModel(plan=PLAN_MAP, narrator="Aparece en captura_a [m1].")
    network, _, _ = _network(model)
    network.ask(COMPOSITE)
    evidence = json.loads(model.messages["verificador"][0][1]["content"])[0]["evidencia"]["m1"]
    assert "tarjeta de crédito frente a la tablet" in evidence
    narrated = json.loads(model.messages["narrador"][0][1]["content"])
    assert narrated["resumen"] == {"episodios_revisados": 3, "episodios_donde_aparece": 2,
                                   "episodios_donde_no_aparece": 1, "episodios_sin_revisar": 2}
