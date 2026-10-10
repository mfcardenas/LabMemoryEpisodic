"""Pruebas de aislamiento de configuración de la CLI."""

from argparse import Namespace

import argparse
import sys

import pytest

from episodic.interfaces.cli import commands
from episodic.interfaces.cli.commands import _config_from_args, _parse_time_spec


def test_cli_uses_explicit_qdrant_collection() -> None:
    args = Namespace(
        data="temporary-data",
        qdrant_host="qdrant.test",
        qdrant_port=7333,
        qdrant_collection="isolated_test_collection",
    )

    config = _config_from_args(args)

    assert config.data_dir == "temporary-data"
    assert config.vector_backend == "edge"
    assert config.qdrant_host == "qdrant.test"
    assert config.qdrant_port == 7333
    assert config.qdrant_collection == "isolated_test_collection"


def test_cli_parses_iso_utc_interval() -> None:
    parsed = _parse_time_spec("2026-01-01T18:23:20Z")

    assert parsed.absolute is not None
    assert parsed.absolute.isoformat() == "2026-01-01T18:23:20+00:00"
    assert parsed.clock_source == "cli"
    assert parsed.is_unknown is False


def test_cli_rejects_invalid_interval_value() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_time_spec("01/01/2026 18:23")


@pytest.mark.parametrize(
    "argv",
    [
        ["episodic", "--data", "isolated", "--qdrant-collection", "isolated_col", "init"],
        ["episodic", "init", "--data", "isolated", "--qdrant-collection", "isolated_col"],
    ],
)
def test_common_cli_options_work_before_or_after_subcommand(monkeypatch, argv) -> None:
    captured = {}

    def capture(args):
        captured.update(vars(args))

    monkeypatch.setattr(commands, "cmd_init", capture)
    monkeypatch.setattr(sys, "argv", argv)

    commands.main()

    assert captured["data"] == "isolated"
    assert captured["qdrant_collection"] == "isolated_col"
