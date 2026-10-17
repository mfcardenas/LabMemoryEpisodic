#!/usr/bin/env bash
# Arranca EpiGraph: servicios del docker-compose (Qdrant, transcripción) y
# después la API con el front, que vigila la carpeta de watch.toml. Los
# argumentos se pasan a "epigraph serve", p. ej.:  ./start.sh --port 8200
# Variables: ver scripts/launcher_env.sh
set -euo pipefail
cd "$(dirname "$0")"
source scripts/launcher_env.sh

if [ "$SERVICES" != "none" ]; then
  if command -v docker >/dev/null 2>&1; then
    echo "[start] Levantando servicios: $SERVICES"
    # shellcheck disable=SC2086  # lista de servicios separada por espacios
    if ! docker compose up -d --wait --wait-timeout 180 $SERVICES; then
      echo "[start] ERROR: no se pudieron levantar $SERVICES." >&2
      echo "        Si el puerto de Qdrant lo ocupa otro contenedor, ver docs/qdrant.md." >&2
      echo "        Para arrancar sin el compose: EPIGRAPH_COMPOSE_SERVICES=none ./start.sh" >&2
      exit 1
    fi
  else
    echo "[start] AVISO: docker no está disponible; se arranca EpiGraph sin levantar $SERVICES." >&2
  fi
fi

echo "[start] EpiGraph con $PYTHON"
exec "$PYTHON" -m episodic serve "$@"
