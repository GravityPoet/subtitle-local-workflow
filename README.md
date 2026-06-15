# Subtitle Local Workflow

Local-first tools for turning a video file or video URL into English/Chinese subtitle drafts, plus an optional hard-subtitled bilingual MP4.

The one-command URL workflow produces:

- downloaded source video
- English SRT
- Chinese draft SRT
- English-on-top / Chinese-on-bottom bilingual SRT
- ASS subtitle file
- burned MP4
- review text
- verification frames
- `manifest.json`

## Requirements

- macOS or Linux
- Python 3.11
- `uv`
- `ffmpeg` with the `ass` filter from libass
- network access for URL downloads and the default Google translation backend

On macOS, the wrapper prefers Homebrew `ffmpeg-full` when available:

```bash
brew install libass ffmpeg-full
```

## Quick Start

From this repository:

```bash
./burn_bilingual_link.sh "https://example.com/video-url"
```

For a local video file:

```bash
./burn_bilingual_link.sh "/absolute/path/to/video.mp4"
```

By default, output goes to:

```text
$HOME/Downloads/bilingual-output
```

Override it with:

```bash
SUBTITLE_OUTPUT_ROOT="/absolute/path/to/output" ./burn_bilingual_link.sh "https://example.com/video-url"
```

Downloaded local ASR models are cached under:

```text
$HOME/Tools/Local-LLM
```

Override it with:

```bash
SUBTITLE_MODEL_CACHE_ROOT="/absolute/path/to/local-models" ./burn_bilingual_link.sh "https://example.com/video-url" --quality accurate
```

The wrapper also sets Hugging Face cache variables under that root so ASR model downloads stay grouped:

```text
$SUBTITLE_MODEL_CACHE_ROOT/huggingface/home
$SUBTITLE_MODEL_CACHE_ROOT/huggingface/hub
$SUBTITLE_MODEL_CACHE_ROOT/huggingface/xet
$SUBTITLE_MODEL_CACHE_ROOT/huggingface/transformers
```

The wrapper intentionally overrides `HF_HOME`, `HF_HUB_CACHE`, `HF_XET_CACHE`, and `TRANSFORMERS_CACHE` for the subprocess so model files do not scatter into the default user cache.

## Default Bilingual Layout

The URL workflow defaults to the `news-box` subtitle profile:

- English on top
- Chinese below
- English warm yellow
- Chinese white
- semi-transparent dark subtitle box
- raised position for news/interview videos with lower-third graphics
- first-frame black-screen repair for social platforms that use frame 0 as the thumbnail

Useful overrides:

```bash
./burn_bilingual_link.sh "https://example.com/video-url" --subtitle-profile standard
./burn_bilingual_link.sh "https://example.com/video-url" --subtitle-profile news-safe
./burn_bilingual_link.sh "https://example.com/video-url" --cn-size 50 --en-size 44
./burn_bilingual_link.sh "https://example.com/video-url" --ass-back-color '&H80000000&'
```

## Transcription

The wrapper currently installs runtime dependencies with `uv` and defaults to the high-accuracy local ladder:

```text
Parakeet TDT 0.6B v2 via parakeet-mlx (`mlx-community/parakeet-tdt-0.6b-v2`)
-> MLX Whisper
-> faster-whisper
-> whisper.cpp server fallback
```

You can force a specific engine:

```bash
./burn_bilingual_link.sh "https://example.com/video-url" --transcriber parakeet-v2
./burn_bilingual_link.sh "https://example.com/video-url" --transcriber mlx
./burn_bilingual_link.sh "https://example.com/video-url" --transcriber faster
./burn_bilingual_link.sh "https://example.com/video-url" --transcriber server
```

The default is equivalent to:

```bash
./burn_bilingual_link.sh "https://example.com/video-url" --quality accurate
```

Notes:

- Parakeet v2 is English-focused and fast on Apple Silicon through `parakeet-mlx`.
- The Parakeet v2 model used here is `mlx-community/parakeet-tdt-0.6b-v2`.
- Set `SUBTITLE_MODEL_CACHE_ROOT` if you want Parakeet/Hugging Face model files stored outside the default cache path.
- This workflow intentionally uses Parakeet v2 only. It does not download Parakeet v3.

For a lighter dependency path that skips Parakeet and starts with MLX Whisper:

```bash
./burn_bilingual_link.sh "https://example.com/video-url" --quality fast
```

The default whisper.cpp server URL is:

```text
http://127.0.0.1:8178
```

## Translation

Default translation backend:

```text
google
```

Offline Argos translation is supported if you have the language packages installed:

```bash
uv run --python 3.11 --with argostranslate \
  python subtitle_pipeline_local.py "/absolute/path/to/video.mp4" \
  --source-lang en \
  --target-lang zh-CN \
  --translator-backend argos
```

## Draft Subtitle Pipeline

For file-only draft subtitle generation without burning:

```bash
uv run --python 3.11 --with deep-translator \
  python subtitle_pipeline_local.py "/absolute/path/to/video.mp4" \
  --glossary ./glossary.example.json
```

Generated files are written next to the input media:

- `*.verbose.json`
- `*.en.srt`
- `*.zh.draft.srt`
- `*.zh.srt`
- `*.review.txt`

## Existing Translation Alignment

To align an existing translated text file:

```bash
./bootstrap_subaligner_env.sh

python3 align_existing_translation.py \
  "/absolute/path/to/video.mp4" \
  "/absolute/path/to/translated.txt"
```

The aligner tries Subaligner first and falls back to a local heuristic alignment.

## Agent SOP

See [`docs/agent-sop.md`](docs/agent-sop.md) for a concise handoff prompt/SOP for coding agents.
