#!/usr/bin/env bash
# Setup venv for inner voices service.
# Usage: bash setup_venv.sh [openvino|mlx|cpu|auto]
# Default: auto-detect based on platform.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# Detect provider if not specified
PROVIDER="${1:-auto}"
if [ "$PROVIDER" = "auto" ]; then
    OS="$(uname -s)"
    ARCH="$(uname -m)"
    if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
        PROVIDER="mlx"
    elif [ "$OS" = "Linux" ]; then
        PROVIDER="openvino"
    else
        PROVIDER="cpu"
    fi
fi

echo "[inner_voices] Provider: $PROVIDER"
echo "[inner_voices] Creating venv at $VENV_DIR"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

# Base deps
"$VENV_DIR/bin/pip" install pyyaml --quiet

case "$PROVIDER" in
    openvino)
        echo "[inner_voices] Installing openvino_genai + optimum-intel..."
        "$VENV_DIR/bin/pip" install \
            openvino-genai>=2025.0 \
            optimum-intel>=1.20.0 \
            --quiet
        ;;
    mlx)
        echo "[inner_voices] Installing mlx_lm..."
        "$VENV_DIR/bin/pip" install mlx mlx-lm --quiet
        ;;
    cpu)
        echo "[inner_voices] Installing transformers + torch (CPU)..."
        "$VENV_DIR/bin/pip" install torch transformers --quiet
        ;;
    *)
        echo "[inner_voices] Unknown provider: $PROVIDER. Use: openvino | mlx | cpu | auto"
        exit 1
        ;;
esac

echo "[inner_voices] Setup complete. Run with:"
echo "  $VENV_DIR/bin/python $SCRIPT_DIR/jing_runner.py"
echo "  $VENV_DIR/bin/python $SCRIPT_DIR/jang_runner.py"
