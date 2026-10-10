"""Primitivas comunes de validación y serialización del dominio."""
from __future__ import annotations

import types
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar, Union, get_args, get_origin, get_type_hints


class DomainValidationError(ValueError):
    """Un objeto no cumple una invariante del dominio EpiGraph."""


def validate_identifier(value: str, field_name: str = "id") -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} debe ser un identificador no vacío")


def validate_timestamp(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} debe incluir zona horaria")


def validate_optional_timestamp(value: datetime | None, field_name: str) -> None:
    if value is not None:
        validate_timestamp(value, field_name)


def validate_interval(
    start_time: datetime | None,
    end_time: datetime | None,
    *,
    required: bool = False,
) -> None:
    if required and (start_time is None or end_time is None):
        raise DomainValidationError("start_time y end_time son obligatorios")
    if (start_time is None) != (end_time is None):
        raise DomainValidationError("start_time y end_time deben declararse juntos")
    if start_time is None:
        return
    validate_timestamp(start_time, "start_time")
    validate_timestamp(end_time, "end_time")  # type: ignore[arg-type]
    if end_time < start_time:  # type: ignore[operator]
        raise DomainValidationError("end_time no puede ser anterior a start_time")


def validate_confidence(confidence: float | None) -> None:
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise DomainValidationError("confidence debe estar entre 0 y 1")


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        validate_timestamp(value, "datetime")
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if hasattr(value, "to_dict") and not isinstance(value, SerializableDomainModel):
        return value.to_dict()
    if is_dataclass(value):
        return {item.name: _encode(getattr(value, item.name)) for item in fields(value)}
    raise TypeError(f"No se puede serializar {type(value).__name__}")


def _decode(annotation: Any, value: Any) -> Any:
    if value is None or annotation is Any:
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode(option, value)
            except (TypeError, ValueError):
                continue
        return value
    if origin is list:
        item_type = args[0] if args else Any
        return [_decode(item_type, item) for item in value]
    if origin is dict:
        value_type = args[1] if len(args) > 1 else Any
        return {key: _decode(value_type, item) for key, item in value.items()}
    if annotation is datetime:
        parsed = datetime.fromisoformat(value)
        validate_timestamp(parsed, "datetime")
        return parsed
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if isinstance(annotation, type) and hasattr(annotation, "from_dict"):
        return annotation.from_dict(value)
    return value


_ModelT = TypeVar("_ModelT", bound="SerializableDomainModel")


class SerializableDomainModel:
    """Mixin sin estado para round-trip JSON de dataclasses del dominio."""

    def to_dict(self) -> dict[str, Any]:
        if not is_dataclass(self):
            raise TypeError("SerializableDomainModel requiere una dataclass")
        payload = {
            item.name: _encode(getattr(self, item.name))
            for item in fields(self)
        }
        return {"kind": type(self).__name__, **payload}

    @classmethod
    def from_dict(cls: type[_ModelT], payload: dict[str, Any]) -> _ModelT:
        kind = payload.get("kind")
        if kind is not None and kind != cls.__name__:
            raise DomainValidationError(
                f"Se esperaba kind={cls.__name__}, se recibió {kind}"
            )
        hints = get_type_hints(cls)
        values: dict[str, Any] = {}
        for item in fields(cls):
            if item.name in payload:
                values[item.name] = _decode(hints.get(item.name, Any), payload[item.name])
        return cls(**values)
