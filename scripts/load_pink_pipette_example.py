"""Carga idempotentemente el fixture de dominio de la pipeta rosada."""
from __future__ import annotations

import argparse

from episodic.adapters.sqlite.repository import SqliteEpisodicRepository
from episodic.config import EpisodicConfig
from episodic.examples.pink_pipette import build_pink_pipette_fixture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        default=".runtime/fase2-pink-pipette",
        help="Directorio de datos relativo a la raíz del proyecto",
    )
    args = parser.parse_args()
    config = EpisodicConfig(data_dir=args.data)
    repository = SqliteEpisodicRepository(config.sqlite_path)
    try:
        fixture = build_pink_pipette_fixture()
        fixture.persist(repository)
        print(f"SQLite: {config.sqlite_path}")
        print(f"Ambientes de ejemplo: {len(fixture.environments)}")
        print(f"Fuentes de ejemplo: {len(fixture.sources)}")
        print(f"Activos originales: {len(fixture.assets)}")
        print(f"Observaciones: {len(fixture.observations)}")
        print("Fixture de la pipeta rosada persistido correctamente")
    finally:
        repository.close()


if __name__ == "__main__":
    main()
