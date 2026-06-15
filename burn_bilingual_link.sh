#!/bin/bash
set -euo pipefail

# Low-risk local wrapper: no backup needed because it only creates a new run
# directory under OUTPUT_ROOT and never overwrites source videos.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_OUTPUT_ROOT="${HOME}/Downloads/bilingual-output"
OUTPUT_ROOT="${SUBTITLE_OUTPUT_ROOT:-$DEFAULT_OUTPUT_ROOT}"
TRANSCRIBER="${SUBTITLE_TRANSCRIBER:-auto}"
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

uv run --python 3.11 --with yt-dlp --with deep-translator --with mlx-whisper --with faster-whisper \
  python "$SCRIPT_DIR/video_link_bilingual_burn.py" \
  "$VIDEO_URL" \
  --output-root "$OUTPUT_ROOT" \
  --transcriber "$TRANSCRIBER" \
  --subtitle-profile news-box \
  "${LOCAL_ARGS[@]}" \
  "$@"
