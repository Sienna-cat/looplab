#!/usr/bin/env bash
# Install dependencies and download SenseVoice int8 model.
# Idempotent: skips work that's already done.

set -euo pipefail

CACHE_DIR="${HOME}/.cache/sherpa-onnx-models"
MODEL_NAME="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
MODEL_TARBALL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${MODEL_NAME}.tar.bz2"
MODEL_DIR="${CACHE_DIR}/${MODEL_NAME}"

echo "==> Step 1/3: ensure pip is recent"
python3 -m pip install --user --quiet --upgrade pip

echo "==> Step 2/3: install Python deps (sherpa-onnx, av, numpy)"
python3 -m pip install --user --quiet sherpa-onnx av numpy || {
    echo "First install failed, force-reinstalling for native arch..." >&2
    python3 -m pip install --user --force-reinstall --no-cache-dir \
        numpy av sherpa-onnx
}

if [[ -f "${MODEL_DIR}/model.int8.onnx" && -f "${MODEL_DIR}/tokens.txt" ]]; then
    echo "==> Step 3/3: model already present at ${MODEL_DIR}"
    exit 0
fi

echo "==> Step 3/3: downloading SenseVoice int8 model (~158 MB)"
mkdir -p "${CACHE_DIR}"
cd "${CACHE_DIR}"
curl -L -o "${MODEL_NAME}.tar.bz2" "${MODEL_TARBALL_URL}"
tar -xjf "${MODEL_NAME}.tar.bz2"
rm -f "${MODEL_NAME}.tar.bz2"

if [[ ! -f "${MODEL_DIR}/model.int8.onnx" || ! -f "${MODEL_DIR}/tokens.txt" ]]; then
    echo "ERROR: model files missing under ${MODEL_DIR}" >&2
    exit 1
fi

echo "Setup complete. Model dir: ${MODEL_DIR}"
