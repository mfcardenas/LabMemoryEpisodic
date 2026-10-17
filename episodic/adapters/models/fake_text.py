"""Modelo de texto determinista para pruebas y demostraciones sin Ollama.

Rellena el esquema pedido con texto reconocible y cita lo que encuentra en
el prompt: cualquier marca ``[ev N · mm:ss]``, ``[ep:…]`` o ``[día …]`` pasa a
los ``highlights``, de modo que la verificación de citas funciona igual que
con el modelo real.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from episodic.ports.text_composer import Composition

_EVENT = re.compile(r"\[ev (\d+) · (\d{1,2}):(\d{2})\]")
_EPISODE = re.compile(r"\[ep:([^\]]+)\]")
_DAY = re.compile(r"\[día (\d{4}-\d{2}-\d{2})\]")


class FakeTextComposer:
    def __init__(self, model: str = "fake-text") -> None:
        self._model = model
        self.calls: list[dict[str, Any]] = []

    @property
    def model_id(self) -> str:
        return self._model

    def compose(
        self, prompt: str, *, system: str = "", schema: Optional[dict[str, Any]] = None
    ) -> Composition:
        self.calls.append({"prompt": prompt, "system": system, "schema": schema})
        events = _EVENT.findall(prompt)
        episodes = _EPISODE.findall(prompt)
        days = _DAY.findall(prompt)
        marks = (
            [f"[ev {seq} · {mm}:{ss}]" for seq, mm, ss in events]
            or [f"[ep:{item}]" for item in episodes]
            or [f"[día {item}]" for item in days]
        )
        data: dict[str, Any] = {
            "title": f"Resumen simulado ({len(marks)} referencia(s))",
            "summary": f"Resumen simulado a partir de {len(marks)} elemento(s) citados.",
            "narrative": " ".join(f"Ocurre algo {mark}." for mark in marks) or "Sin elementos que narrar.",
            "highlights": [],
            "open_questions": ["¿Qué no se ve en las imágenes?"],
        }
        for seq, mm, ss in events[:3]:
            data["highlights"].append({"event_sequence": int(seq), "offset_seconds": int(mm) * 60 + int(ss), "text": f"Momento del evento {seq}"})
        for item in episodes[:3]:
            data["highlights"].append({"episode_external_id": item, "text": f"Episodio {item}"})
        for item in days[:3]:
            data["highlights"].append({"day": item, "text": f"Día {item}"})
        if schema is not None:
            wanted = set((schema.get("properties") or {}).keys())
            if wanted:
                data = {key: value for key, value in data.items() if key in wanted}
                for key in wanted - set(data):
                    data[key] = [] if (schema["properties"][key].get("type") == "array") else ""
        return Composition(data=data, model_id=self.model_id, usage={"fake": True})
