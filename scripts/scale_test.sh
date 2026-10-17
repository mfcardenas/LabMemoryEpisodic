#!/usr/bin/env bash
# Prueba de escala sin intervención (Fase S): dos perfiles seguidos.
#   1) 200 000 eventos con vectores hash: mide SQLite, FTS y Qdrant.
#   2) 50 000 eventos con Nomic y el reordenador reales (lento, horas en CPU).
# Usa su propio directorio de datos y sus propias colecciones
# (episodic_memory_scale_*); nunca toca las capturas reales.
# Uso: scripts/scale_test.sh [EVENTS_HASH] [EVENTS_REAL]
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/launcher_env.sh
EVENTS_HASH="${1:-200000}"
EVENTS_REAL="${2:-50000}"
export PYTHONIOENCODING=utf-8

echo "[escala] perfil hash: $EVENTS_HASH eventos"
"$PYTHON" scripts/scale_test.py --events "$EVENTS_HASH" --fake-encoders --reset
echo "[escala] perfil real: $EVENTS_REAL eventos (Nomic + reordenador)"
"$PYTHON" scripts/scale_test.py --events "$EVENTS_REAL" --reset --profile real
echo "[escala] informes en data/eval/escala-*.json"
