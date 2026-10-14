"""El agente orquestador: bucle acotado, marcas citables y verificación de citas."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from episodic.adapters.models.fake_chat import FakeReasoningModel, scripted_turn
from episodic.application.agents.orchestrator import AnswerOrchestrator
from episodic.domain.answers import AnswerRun
from episodic.ports.reasoning import ReasoningError
from episodic.tools.contracts import MemoryToolOutput, MomentRecord, ToolDescriptor


class StubToolbox:
    """Devuelve momentos fijos para search_moments y registra las llamadas."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def describe(self):
        return [
            ToolDescriptor(name="search_moments", description="busca", input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, output_schema={"type": "object"}),
            ToolDescriptor(name="get_event", description="evento", input_schema={"type": "object", "properties": {"event_id": {"type": "string"}}}, output_schema={"type": "object"}),
            ToolDescriptor(name="compare_entities", description="no para agentes", input_schema={"type": "object"}, output_schema={"type": "object"}),
        ]

    def execute(self, name, payload):
        self.calls.append((name, payload))
        if name == "search_moments":
            if "nada" in payload.get("query", ""):
                return MemoryToolOutput(tool=name, metadata={"plan": {"time_expression": None}})
            moments = [
                MomentRecord(id="ps-1", kind="video_event", event_id="ev-1", observation_id="obs-1", episode_id="ep-1", episode_external_id="taller_1",
                             title="Cortar la pieza", text="Un mecánico corta una pieza y saltan chispas.", start_offset_seconds=0.0,
                             start_time=datetime(2026, 10, 12, 14, 21, 15, tzinfo=timezone.utc), speech=[{"text": "Mira, esto está agarrotado."}]),
                MomentRecord(id="ps-2", kind="document_text_fragment", episode_id="ep-1", episode_external_id="taller_1", text="No usar la radial cerca de latiguillos.", page_number=3),
            ]
            return MemoryToolOutput(tool=name, moments=moments, metadata={"plan": {"time_expression": "ayer"}, "discarded_episodes": []})
        if name == "get_event":
            raise ValueError(f"Evento no encontrado: {payload.get('event_id')}")
        raise KeyError(name)


class StubRepository:
    def __init__(self) -> None:
        self.saved: list[AnswerRun] = []

    def save_answer_run(self, run: AnswerRun) -> None:
        self.saved.append(run)

    def get_environment(self, environment_id: str):
        return {"id": environment_id} if environment_id == "taller" else None


class Activity:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, component, message, **data):
        self.messages.append(f"{component}: {message}")


def _agent(model, **kwargs) -> AnswerOrchestrator:
    return AnswerOrchestrator(StubRepository(), StubToolbox(), model, timezone=timezone.utc, activity=Activity(), **kwargs)


def test_tool_specs_expose_only_agent_tools_with_reduced_schemas() -> None:
    specs = _agent(FakeReasoningModel()).tool_specs()
    assert [item["function"]["name"] for item in specs] == ["search_moments", "get_event"]
    assert specs[0]["type"] == "function" and specs[0]["function"]["parameters"]["required"] == ["query"]
    # El modelo no decide tamaños, umbrales ni filtros de ambiente: no aparecen en su esquema.
    assert set(specs[0]["function"]["parameters"]["properties"]) == {"query"}


def test_model_arguments_are_sanitised_and_completed_with_defaults() -> None:
    model = FakeReasoningModel(script=[
        scripted_turn(calls=[("search_moments", {"query": "chispas", "environment_id": "taller", "per_episode": 0, "kinds": ["speech_segment"], "start_time": None, "max_chars": 200})]),
        scripted_turn("Saltan chispas [m1].\nSaltan chispas [m1].\nSaltan chispas [m1]."),
    ])
    agent = _agent(model)
    run = agent.ask("¿Chispas?")
    assert agent.toolbox.calls[0][1] == {"max_episodes": 4, "per_episode": 3, "strict": False, "max_chars": 900, "query": "chispas"}
    assert run.steps[0]["arguments"] == agent.toolbox.calls[0][1]
    assert run.answer == "Saltan chispas [m1]."  # la frase repetida en bucle, una vez


def test_moments_get_handles_and_citations_are_verified() -> None:
    model = FakeReasoningModel(script=[
        scripted_turn(calls=[("search_moments", {"query": "chispas"})]),
        scripted_turn("Saltan chispas al cortar [m1]. El manual prohíbe la radial cerca de latiguillos [m2]. Y algo inventado [m7]."),
    ])
    agent = _agent(model)
    run = agent.ask("¿Cuándo saltan chispas?", now=datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc))

    assert run.answer == "Saltan chispas al cortar [m1]. El manual prohíbe la radial cerca de latiguillos [m2]. Y algo inventado."
    assert [c["handle"] for c in run.citations] == ["m1", "m2"]
    assert run.citations[0]["event_id"] == "ev-1" and run.citations[0]["start_offset_seconds"] == 0.0
    assert run.citations[1]["page_number"] == 3
    assert run.dropped_citations == ["m7"] and run.confidence == "media" and not run.abstained
    assert [step["tool"] for step in run.steps] == ["search_moments"] and run.steps[0]["handles"] == ["m1", "m2"]
    assert run.usage["turns"] == 2 and run.model_id == "fake-reasoning"
    assert agent.repository.saved[0].id == run.id
    # El modelo vio las marcas, la hora local y lo que se dijo.
    tool_message = model.calls[1]["messages"][-1]
    payload = json.loads(tool_message["content"])
    assert tool_message["role"] == "tool" and payload["items"][0]["handle"] == "m1"
    assert payload["items"][0]["when"] == "2026-10-12 14:21" and payload["items"][0]["said"] == ["Mira, esto está agarrotado."]
    assert payload["understood"] == "ayer"
    assert "Ahora es 2026-10-14 18:00" in model.calls[0]["messages"][1]["content"]
    assert any("search_moments" in m for m in agent.activity.messages)


def test_answer_without_citations_is_marked_and_abstention_detected() -> None:
    run = _agent(FakeReasoningModel(script=[scripted_turn("Creo que sí, pero no estoy seguro.")])).ask("¿Pasó algo?")
    assert run.confidence == "baja" and not run.citations and not run.abstained

    run = _agent(FakeReasoningModel(script=[
        scripted_turn(calls=[("search_moments", {"query": "nada"})]),
        scripted_turn("No consta en la memoria: busqué «nada» y no hubo momentos."),
    ])).ask("¿Hubo algo de nada?")
    assert run.abstained and run.confidence == "sin_evidencia" and run.citations == []


def test_invented_environment_ids_are_rejected_with_guidance() -> None:
    model = FakeReasoningModel(script=[
        scripted_turn(calls=[("browse_memory", {"level": "days", "environment_id": "env1"})]),
        scripted_turn("No consta en la memoria."),
    ])
    agent = _agent(model)
    run = agent.ask("¿Qué pasó?")
    assert run.steps[0]["error"].startswith("ambiente desconocido: env1")
    assert agent.toolbox.calls == []  # no llegó a ejecutarse


def test_budget_forces_a_final_answer_and_tool_errors_are_reported() -> None:
    model = FakeReasoningModel(script=[
        scripted_turn(calls=[("get_event", {"event_id": "nope"}), ("compare_entities", {})]),
        scripted_turn(calls=[("search_moments", {"query": "chispas"})]),
        scripted_turn("Con lo que tengo: chispas [m1]."),
    ])
    agent = _agent(model, max_steps=2)
    run = agent.ask("¿Qué pasó?")
    assert run.steps[0]["error"].startswith("Evento no encontrado") and run.steps[1]["error"].startswith("herramienta no permitida")
    assert run.metadata["budget"] == "vueltas agotadas" and run.confidence == "alta"
    assert "presupuesto" in model.calls[-1]["messages"][-1]["content"].casefold()
    assert model.calls[-1]["tools"] == []  # la última vuelta va sin herramientas


def test_loose_citation_formats_and_leaked_reasoning_are_normalised() -> None:
    model = FakeReasoningModel(script=[
        scripted_turn(calls=[("search_moments", {"query": "chispas"})]),
        scripted_turn("We have moments m1, m2. Need answer: cite. Use citations.Saltan chispas al cortar (m1). Ver 00:00 m2."),
    ])
    run = _agent(model).ask("¿Cuándo saltan chispas?")
    assert run.answer == "Saltan chispas al cortar [m1]. Ver 00:00 [m2]."
    assert [c["handle"] for c in run.citations] == ["m1", "m2"] and run.confidence == "alta"

    spaced = _agent(FakeReasoningModel(script=[
        scripted_turn(calls=[("search_moments", {"query": "chispas"})]),
        scripted_turn("Chispas [ m1 ] y manual [m1, m2 ]."),
    ])).ask("¿Chispas?")
    assert spaced.answer == "Chispas [m1] y manual [m1, m2]." and spaced.confidence == "alta"


def test_malformed_tool_calls_are_retried_then_answered_without_tools() -> None:
    class Flaky:
        model_id = "flaky"

        def __init__(self) -> None:
            self.calls = 0
            self.seen_tools: list[bool] = []

        def chat(self, messages, tools=None):
            self.calls += 1
            self.seen_tools.append(tools is not None)
            if self.calls <= 2:
                raise ReasoningError("Ollama devolvió 500: error parsing tool call: raw='...'")
            assert "no se pudieron interpretar" in messages[-1]["content"]
            return scripted_turn("No consta en la memoria: no pude consultar las herramientas.")

    model = Flaky()
    run = _agent(model).ask("¿Qué pasó?")
    assert run.abstained and run.metadata["tool_call_retries"] == 2 and model.seen_tools == [True, True, False]


def test_model_failure_is_recorded_not_raised() -> None:
    class Broken:
        model_id = "broken"

        def chat(self, messages, tools=None):
            raise ReasoningError("Ollama no responde")

    run = _agent(Broken()).ask("¿Qué pasó?")
    assert run.error == "Ollama no responde" and run.confidence == "sin_evidencia" and "No se pudo consultar" in run.answer


def test_disabled_agent_raises_a_clear_error() -> None:
    agent = AnswerOrchestrator(StubRepository(), StubToolbox(), None)
    assert not agent.enabled
    with pytest.raises(ValueError, match="EPIGRAPH_AGENT_BACKEND"):
        agent.ask("¿Qué pasó?")
