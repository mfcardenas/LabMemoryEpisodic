# Común a start.sh y reset.sh (se carga con "source"): lee de .env las
# variables de los lanzadores, aplica los valores por defecto y deja en
# PYTHON el intérprete del entorno Conda y en SERVICES los del compose.
#   EPIGRAPH_PYTHON            intérprete a usar (por defecto, el del entorno Conda)
#   EPIGRAPH_CONDA_ENV         entorno Conda (LabMemoryEpic01)
#   EPIGRAPH_COMPOSE_SERVICES  servicios que levanta start.sh ("qdrant speech"; "none")

env_value() {
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env | tail -n 1 | sed 's/[[:space:]]#.*$//; s/[[:space:]]*$//; s/^"\(.*\)"$/\1/'
}

PYTHON="${EPIGRAPH_PYTHON:-$(env_value EPIGRAPH_PYTHON)}"
CONDA_ENV="${EPIGRAPH_CONDA_ENV:-$(env_value EPIGRAPH_CONDA_ENV)}"
CONDA_ENV="${CONDA_ENV:-LabMemoryEpic01}"
SERVICES="${EPIGRAPH_COMPOSE_SERVICES:-$(env_value EPIGRAPH_COMPOSE_SERVICES)}"
SERVICES="${SERVICES:-qdrant speech}"

# Python del entorno Conda (Linux/macOS: bin/python; Git Bash: python.exe).
if [ -z "$PYTHON" ]; then
  candidates=("$HOME/.conda/envs/$CONDA_ENV/bin/python" "$HOME/.conda/envs/$CONDA_ENV/python.exe")
  if [ -n "${CONDA_EXE:-}" ]; then
    conda_root="$(dirname "$(dirname "$CONDA_EXE")")"
    candidates+=("$conda_root/envs/$CONDA_ENV/bin/python" "$conda_root/envs/$CONDA_ENV/python.exe")
  fi
  for candidate in "${candidates[@]}"; do
    if [ -x "$candidate" ]; then PYTHON="$candidate"; break; fi
  done
fi
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"
if [ -z "$PYTHON" ] || ! "$PYTHON" -c "import episodic" >/dev/null 2>&1; then
  echo "[epigraph] ERROR: '${PYTHON:-python}' no puede importar EpiGraph. Defina EPIGRAPH_PYTHON o" >&2
  echo "           instale el paquete en el entorno $CONDA_ENV: python -m pip install -e \".[dev,video]\"" >&2
  exit 1
fi
