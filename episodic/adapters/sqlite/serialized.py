"""Conexión SQLite compartida entre hilos sin mezclar resultados.

FastAPI atiende las rutas síncronas en un pool de hilos. Un único
``sqlite3.Connection`` usado a la vez desde varios hilos intercala cursores y
devuelve filas de otra consulta. Este envoltorio ejecuta cada sentencia bajo
un ``RLock`` y materializa sus filas antes de liberarlo; una transacción
explícita mantiene el lock hasta su ``COMMIT`` o ``ROLLBACK``.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Iterable, Iterator, Sequence


class _Result:
    """Resultado ya leído de una sentencia: seguro fuera del lock."""

    def __init__(self, rows: list[Any], rowcount: int, lastrowid: int | None) -> None:
        self._rows = rows
        self._position = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self) -> Any:
        if self._position >= len(self._rows):
            return None
        row = self._rows[self._position]
        self._position += 1
        return row

    def fetchall(self) -> list[Any]:
        rows = self._rows[self._position:]
        self._position = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[Any]:
        return iter(self.fetchall())


class _SerializedCursor:
    """API mínima de cursor que usa el repositorio (execute + fetch)."""

    def __init__(self, connection: "SerializedConnection") -> None:
        self._connection = connection
        self._result = _Result([], -1, None)

    def execute(self, sql: str, parameters: Sequence[Any] | dict = ()) -> "_SerializedCursor":
        self._result = self._connection.execute(sql, parameters)
        return self

    def fetchone(self) -> Any:
        return self._result.fetchone()

    def fetchall(self) -> list[Any]:
        return self._result.fetchall()

    @property
    def rowcount(self) -> int:
        return self._result.rowcount

    def __iter__(self) -> Iterator[Any]:
        return iter(self._result)


class SerializedConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.lock = threading.RLock()

    def execute(self, sql: str, parameters: Sequence[Any] | dict = ()) -> _Result:
        with self.lock:
            cursor = self._connection.execute(sql, parameters)
            rows = cursor.fetchall() if cursor.description is not None else []
            return _Result(rows, cursor.rowcount, cursor.lastrowid)

    def executemany(self, sql: str, parameters: Iterable[Sequence[Any]]) -> _Result:
        with self.lock:
            cursor = self._connection.executemany(sql, parameters)
            return _Result([], cursor.rowcount, cursor.lastrowid)

    def cursor(self) -> _SerializedCursor:
        return _SerializedCursor(self)

    def close(self) -> None:
        with self.lock:
            self._connection.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)
