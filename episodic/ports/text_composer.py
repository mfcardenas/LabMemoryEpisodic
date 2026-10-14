"""Puerto del modelo de texto: redacta a partir de datos dados, con salida JSON.

Lo usan los resúmenes jerárquicos (episodio, día, entorno) y, más adelante,
la reformulación de consultas. El adaptador real habla con Ollama; el falso
es determinista para las pruebas. Todo texto que produce se guarda con el
modelo, la versión del prompt y la huella de la entrada: es texto generado,
nunca una observación.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


class TextComposerError(RuntimeError):
    """El modelo no está disponible o devolvió algo inutilizable."""


@dataclass
class Composition:
    """Respuesta del modelo ya parseada como JSON."""

    data: dict[str, Any]
    model_id: str
    usage: dict[str, Any] = field(default_factory=dict)


class TextComposer(Protocol):
    @property
    def model_id(self) -> str: ...

    def compose(
        self,
        prompt: str,
        *,
        system: str = "",
        schema: Optional[dict[str, Any]] = None,
    ) -> Composition:
        """Devuelve el JSON que cumple ``schema`` para ``prompt``; lanza
        ``TextComposerError`` si el modelo no responde o no devuelve JSON."""
        ...
