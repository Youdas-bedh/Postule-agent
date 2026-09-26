#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${POSTULE_ENV_FILE:-${PROJECT_DIR}/.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  printf 'Configuration introuvable : %s (copie .env.example vers .env).\n' "$ENV_FILE" >&2
  exit 2
fi
# .env is a trusted shell-style configuration maintained by the project owner.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${MODEL_NAME:-}" ]]; then
  printf 'MODEL_NAME doit être défini dans .env.\n' >&2
  exit 2
fi
: "${GPU_MEMORY_UTILIZATION:=0.85}"
: "${MAX_MODEL_LEN:=16384}"
: "${VLLM_HOST:=127.0.0.1}"
: "${VLLM_PORT:=8000}"
: "${VLLM_BIN:=vllm}"

if ! command -v "$VLLM_BIN" >/dev/null 2>&1; then
  printf 'Commande vLLM introuvable (%s). Installe vLLM dans l’environnement GPU isolé ; voir README.md.\n' "$VLLM_BIN" >&2
  exit 127
fi
VLLM_EXECUTABLE="$(command -v "$VLLM_BIN")"
VLLM_ENV_ROOT="$(dirname -- "$(dirname -- "$VLLM_EXECUTABLE")")"
VLLM_PYTHON="${VLLM_ENV_ROOT}/bin/python"
if [[ -x "$VLLM_PYTHON" ]]; then
  export PATH="${VLLM_ENV_ROOT}/bin:${PATH}"
  VLLM_SITE_PACKAGES="$("$VLLM_PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["platlib"])')"
  for CUDA_LIB_DIR in \
    "${VLLM_SITE_PACKAGES}/nvidia/cu13/lib" \
    "${VLLM_SITE_PACKAGES}/nvidia/cuda_runtime/lib" \
    "${VLLM_SITE_PACKAGES}/nvidia/cudnn/lib"; do
    if [[ -d "$CUDA_LIB_DIR" ]]; then
      export LD_LIBRARY_PATH="${CUDA_LIB_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    fi
  done
fi

if ! python - "$GPU_MEMORY_UTILIZATION" "$MAX_MODEL_LEN" <<'PY'
import sys
try:
    utilization = float(sys.argv[1])
    context_length = int(sys.argv[2])
    valid = 0.1 <= utilization <= 0.99 and context_length > 0
except ValueError:
    valid = False
sys.exit(0 if valid else 1)
PY
then
  printf 'GPU_MEMORY_UTILIZATION doit être entre 0.1 et 0.99 ; MAX_MODEL_LEN doit être positif.\n' >&2
  exit 2
fi

printf '[vLLM] Démarrage du modèle %s sur %s:%s (VRAM=%s, contexte=%s)\n' \
  "$MODEL_NAME" "$VLLM_HOST" "$VLLM_PORT" "$GPU_MEMORY_UTILIZATION" "$MAX_MODEL_LEN"
VLLM_COMMAND="$VLLM_BIN"
VLLM_BIND_HOST="$VLLM_HOST"
VLLM_BIND_PORT="$VLLM_PORT"
unset VLLM_BIN VLLM_BASE_URL VLLM_HOST
exec "$VLLM_COMMAND" serve "$MODEL_NAME" \
  --host "$VLLM_BIND_HOST" \
  --port "$VLLM_BIND_PORT" \
  --dtype auto \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --max-model-len "$MAX_MODEL_LEN" \
  --language-model-only \
  --reasoning-parser qwen3
