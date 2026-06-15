#!/bin/bash
set -euo pipefail

# Low-risk local wrapper: no backup needed because it only creates a new run
# directory under OUTPUT_ROOT and never overwrites source videos.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_OUTPUT_ROOT="${HOME}/Downloads/视频字幕输出"
DEFAULT_MODEL_CACHE_ROOT="${HOME}/Tools/Local-LLM"
OUTPUT_ROOT="${SUBTITLE_OUTPUT_ROOT:-$DEFAULT_OUTPUT_ROOT}"
MODEL_CACHE_ROOT="${SUBTITLE_MODEL_CACHE_ROOT:-$DEFAULT_MODEL_CACHE_ROOT}"
TRANSCRIBER="${SUBTITLE_TRANSCRIBER:-auto}"
QUALITY="${SUBTITLE_QUALITY:-accurate}"
TRANSLATION_REFINE="${SUBTITLE_TRANSLATION_REFINE:-auto}"
LLM_BASE_URL="${SUBTITLE_LLM_BASE_URL:-}"
LLM_MODEL="${SUBTITLE_LLM_MODEL:-}"
FFMPEG_FULL="/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <video-url-or-local-path> [extra video_link_bilingual_burn.py args...]" >&2
  exit 2
fi

VIDEO_INPUT="$1"
shift

LOCAL_ARGS=()
case "$VIDEO_INPUT" in
  http://*|https://*) VIDEO_URL="$VIDEO_INPUT" ;;
  *)
    if [ ! -f "$VIDEO_INPUT" ]; then
      echo "[error] local file not found: $VIDEO_INPUT" >&2
      exit 2
    fi
    VIDEO_URL="$VIDEO_INPUT"
    LOCAL_ARGS=(--local "$VIDEO_INPUT")
    ;;
esac

mkdir -p "$OUTPUT_ROOT"
mkdir -p "$MODEL_CACHE_ROOT"

HF_CACHE_ROOT="${MODEL_CACHE_ROOT}/huggingface"
mkdir -p "$HF_CACHE_ROOT/home" "$HF_CACHE_ROOT/hub" "$HF_CACHE_ROOT/xet" "$HF_CACHE_ROOT/transformers"
export HF_HOME="$HF_CACHE_ROOT/home"
export HF_HUB_CACHE="$HF_CACHE_ROOT/hub"
export HF_XET_CACHE="$HF_CACHE_ROOT/xet"
export TRANSFORMERS_CACHE="$HF_CACHE_ROOT/transformers"
export SUBTITLE_TRANSLATION_REFINE="$TRANSLATION_REFINE"
export SUBTITLE_LLM_BASE_URL="$LLM_BASE_URL"
export SUBTITLE_LLM_MODEL="$LLM_MODEL"

NEEDS_PARAKEET=0
if [ "$QUALITY" = "accurate" ] || [ "$TRANSCRIBER" = "parakeet-v2" ]; then
  NEEDS_PARAKEET=1
fi

PREVIOUS_ARG=""
for ARG in "$@"; do
  if [ "$PREVIOUS_ARG" = "--quality" ] && [ "$ARG" = "accurate" ]; then
    NEEDS_PARAKEET=1
  fi
  if [ "$PREVIOUS_ARG" = "--transcriber" ] && [ "$ARG" = "parakeet-v2" ]; then
    NEEDS_PARAKEET=1
  fi
  case "$ARG" in
    --quality=accurate|--transcriber=parakeet-v2) NEEDS_PARAKEET=1 ;;
  esac
  PREVIOUS_ARG="$ARG"
done

if [ -x "$FFMPEG_FULL" ]; then
  # Subshell disables pipefail to avoid SIGPIPE false-positive (bash 3.2 + set -o pipefail)
  if ! (set +o pipefail; "$FFMPEG_FULL" -hide_banner -filters 2>/dev/null | grep -Eq '(^|[[:space:]])ass[[:space:]]'); then
    echo "[error] ffmpeg-full exists but does not expose the libass ass filter: $FFMPEG_FULL" >&2
    exit 1
  fi
elif ! command -v ffmpeg >/dev/null 2>&1 || ! (set +o pipefail; ffmpeg -hide_banner -filters 2>/dev/null | grep -Eq '(^|[[:space:]])ass[[:space:]]'); then
  echo "[error] no ffmpeg with libass/ass filter found." >&2
  echo "Install once: brew install libass ffmpeg-full" >&2
  echo "Expected: /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg" >&2
  exit 1
fi

cd "$SCRIPT_DIR"

UV_PACKAGES=(--with yt-dlp --with deep-translator --with mlx-whisper --with faster-whisper)
if [ "$NEEDS_PARAKEET" -eq 1 ]; then
  UV_PACKAGES+=(--with parakeet-mlx)
fi

uv run --python 3.11 "${UV_PACKAGES[@]}" \
  python "$SCRIPT_DIR/video_link_bilingual_burn.py" \
  "$VIDEO_URL" \
  --output-root "$OUTPUT_ROOT" \
  --model-cache-root "$MODEL_CACHE_ROOT" \
  --transcriber "$TRANSCRIBER" \
  --quality "$QUALITY" \
  --translation-refine "$TRANSLATION_REFINE" \
  --subtitle-profile news-box \
  ${LOCAL_ARGS[@]+"${LOCAL_ARGS[@]}"} \
  "$@"
