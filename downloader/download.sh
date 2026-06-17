#!/bin/sh
set -eu

# Derive local filename from HuggingFace coordinates if not set explicitly.
# Formula: {HF_ORG}_{HF_REPO}_{HF_FILENAME}  (slash in repo path → underscore)
if [ -z "${LLAMA_MODEL:-}" ]; then
    if [ -z "${LLAMA_HF_REPO:-}" ] || [ -z "${LLAMA_HF_FILE:-}" ]; then
        echo "ERROR: Set LLAMA_MODEL or both LLAMA_HF_REPO + LLAMA_HF_FILE" >&2
        exit 1
    fi
    LLAMA_MODEL="$(echo "$LLAMA_HF_REPO" | tr '/' '_')_${LLAMA_HF_FILE}"
fi

MODEL_PATH="/models/${LLAMA_MODEL}"

if [ -f "$MODEL_PATH" ]; then
    echo "Model already cached: ${MODEL_PATH}"
    exit 0
fi

if [ -z "${LLAMA_HF_REPO:-}" ] || [ -z "${LLAMA_HF_FILE:-}" ]; then
    echo "ERROR: Model not found at ${MODEL_PATH}" >&2
    echo "       Set LLAMA_HF_REPO + LLAMA_HF_FILE in .env to enable auto-download." >&2
    exit 1
fi

HF_URL="https://huggingface.co/${LLAMA_HF_REPO}/resolve/main/${LLAMA_HF_FILE}"
TMPFILE="${MODEL_PATH}.tmp"

echo "Downloading ${HF_URL} → ${MODEL_PATH}"

WGET_ARGS="-c --show-progress -O ${TMPFILE}"
if [ -n "${HF_TOKEN:-}" ]; then
    WGET_ARGS="${WGET_ARGS} --header=Authorization:Bearer ${HF_TOKEN}"
fi

# shellcheck disable=SC2086
if wget ${WGET_ARGS} "${HF_URL}"; then
    mv "$TMPFILE" "$MODEL_PATH"
    echo "Download complete: ${MODEL_PATH}"
else
    rm -f "$TMPFILE"
    echo "ERROR: Download failed" >&2
    exit 1
fi
