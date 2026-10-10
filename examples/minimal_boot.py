"""Ejemplo mínimo: arranque del núcleo EpiGraph sin red ni modelos remotos."""
from __future__ import annotations

from tempfile import TemporaryDirectory

from episodic.bootstrap import create_app_container
from episodic.config import EpisodicConfig


def main() -> None:
    with TemporaryDirectory(prefix="epigraph-") as runtime_dir:
        config = EpisodicConfig(data_dir=runtime_dir)
        container = create_app_container(
            config,
            use_fake_encoders=True,
            use_memory_qdrant=True,
        )
        try:
            episodes, _ = container.repository.list_episodes(limit=1)
            print("EpiGraph arrancó correctamente")
            print(f"SQLite: {config.sqlite_path}")
            print(f"Episodios iniciales: {len(episodes)}")
        finally:
            container.close()


if __name__ == "__main__":
    main()
