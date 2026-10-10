"""Contrato del reloj del sistema.

Permite inyectar un reloj determinista para tests.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Fuente de tiempo inyectable."""
    def now(self) -> datetime:
        """Instante actual en UTC."""
        ...
