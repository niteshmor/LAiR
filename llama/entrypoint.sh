#!/bin/bash
set -euo pipefail

# Derive local filename from HuggingFace coordinates if LLAMA_MODEL not set.
# Formula: {HF_ORG}_{HF_REPO}_{HF_FILENAME}  (slash in repo path → underscore)
# Must match the same derivation in downloader/download.sh.
if [[ -z "${LLAMA_MODEL:-}" ]]; then
    if [[ -z "${LLAMA_HF_REPO:-}" ]] || [[ -z "${LLAMA_HF_FILE:-}" ]]; then
        echo "ERROR: Set LLAMA_MODEL or both LLAMA_HF_REPO + LLAMA_HF_FILE" >&2
        exit 1
    fi
    LLAMA_MODEL="${LLAMA_HF_REPO//\//_}_${LLAMA_HF_FILE}"
fi

MODEL_PATH="/models/${LLAMA_MODEL}"
if [[ ! -f "${MODEL_PATH}" ]]; then
    echo "ERROR: model file not found at ${MODEL_PATH}" >&2
    echo "The model-downloader init container should have fetched it — check its logs." >&2
    exit 1
fi

# LLAMA_EXTRA_ARGS is intentionally unquoted so that it splits into separate
# argv entries. Use it for any flag this entrypoint doesn't model explicitly
# (e.g. --jinja, --kv-unified, --chat-template-kwargs '{...}').
# shellcheck disable=SC2086
exec llama-server \
    --host 0.0.0.0 \
    --port 8080 \
    --model "${MODEL_PATH}" \
    ${LLAMA_ALIAS:+--alias "${LLAMA_ALIAS}"} \
    --ctx-size "${LLAMA_CTX_SIZE}" \
    --temp "${LLAMA_TEMP}" \
    --top-p "${LLAMA_TOP_P}" \
    --min-p "${LLAMA_MIN_P}" \
    --batch-size "${LLAMA_BATCH_SIZE}" \
    --ubatch-size "${LLAMA_UBATCH_SIZE}" \
    --cache-type-k "${LLAMA_CACHE_TYPE_K}" \
    --cache-type-v "${LLAMA_CACHE_TYPE_V}" \
    --flash-attn "${LLAMA_FLASH_ATTN}" \
    --sleep-idle-seconds "${LLAMA_SLEEP_IDLE_SECONDS}" \
    ${LLAMA_EXTRA_ARGS}
