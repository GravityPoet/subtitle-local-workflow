#!/bin/bash
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  SOURCE_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in
    /*) ;;
    *) SOURCE="$SOURCE_DIR/$SOURCE" ;;
  esac
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
DEFAULT_MODEL_CACHE_ROOT="${HOME}/Tools/Local-LLM"
MODEL_CACHE_ROOT="${SUBTITLE_MODEL_CACHE_ROOT:-$DEFAULT_MODEL_CACHE_ROOT}"

case "${1:-}" in
  -h|--help|"")
    cat <<'EOF'
usage: whisper <audio_file> --model <ignored> --output_format srt --output_dir <dir> --language <auto|en>

SmartSub-compatible Whisper CLI shim backed by Parakeet TDT 0.6B v2.
Only English transcription and SRT output are supported.
EOF
    exit 0
    ;;
esac

HF_CACHE_ROOT="${MODEL_CACHE_ROOT}/huggingface"
mkdir -p "$HF_CACHE_ROOT/home" "$HF_CACHE_ROOT/hub" "$HF_CACHE_ROOT/xet" "$HF_CACHE_ROOT/transformers"
export HF_HOME="$HF_CACHE_ROOT/home"
export HF_HUB_CACHE="$HF_CACHE_ROOT/hub"
export HF_XET_CACHE="$HF_CACHE_ROOT/xet"
export TRANSFORMERS_CACHE="$HF_CACHE_ROOT/transformers"

exec uv run --python 3.11 --with parakeet-mlx \
  python "$SCRIPT_DIR/smartsub_parakeet_whisper.py" \
  --model-cache-root "$MODEL_CACHE_ROOT" \
  "$@"
