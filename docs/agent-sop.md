# Agent SOP: video URL to bilingual hard-subtitled MP4

When the user provides a bare video URL, produce an MP4 with hard-burned bilingual subtitles.

Default requirements:

- English on top
- Chinese below
- hard subtitles burned into the video
- default profile: `news-box`
- default transcription quality: `accurate`
- output root: `$HOME/Downloads/视频字幕输出`, unless `SUBTITLE_OUTPUT_ROOT` is set
- model cache root: `$HOME/Tools/Local-LLM`, unless `SUBTITLE_MODEL_CACHE_ROOT` is set
- the wrapper forces Hugging Face caches under the model cache root for the subprocess
- translation path: Google Translate draft first; then either script-internal OpenAI-compatible refinement, or agent-side proofreading with the current LLM
- do not ask about subtitle order; English-on-top and Chinese-on-bottom is the default

Run from the repository root:

```bash
./burn_bilingual_link.sh "<video-url>"
```

The default transcription ladder is `--quality accurate`, which means:

```text
Parakeet TDT 0.6B v2 via parakeet-mlx (`mlx-community/parakeet-tdt-0.6b-v2`)
-> MLX Whisper
-> faster-whisper
-> whisper.cpp server
```

Use Parakeet v2 only. Do not add Parakeet v3 as fallback and do not download v3.

The open-source default keeps this fallback ladder so the project works on more machines. If a workflow must notice Parakeet failures instead of silently using another ASR engine, force `--transcriber parakeet-v2`.

If Parakeet v2 fails in default auto mode, the run should print `[warn]` fallback lines and continue with the next engine. Check `manifest.json`: `transcriber` is the engine actually used, and `transcribe_retry_errors` records failed attempts.

If `--transcriber parakeet-v2` is forced, Parakeet errors should stop the run instead of falling back.

Default translation/refinement:

```text
Google Translate via deep-translator
-> LLM proofreading/refinement
```

There are two valid LLM refinement paths:

1. Script-internal refinement: configure `SUBTITLE_LLM_BASE_URL`, `SUBTITLE_LLM_API_KEY`, and `SUBTITLE_LLM_MODEL` for any OpenAI-compatible provider. In this mode the script refines the Google draft automatically. Use `--translation-refine require` when the caller wants the run to stop unless script-internal LLM refinement succeeds. Check `manifest.json`: `translator_backend`, `translation_refine`, `llm_refine_used`, and `google_chinese_draft_srt`.

2. Agent-side refinement: if the coding agent already runs on Claude, Gemini, MiMo, Codex, or another capable LLM, the agent may proofread the Google draft against the English SRT itself. This does not require an OpenAI-compatible API endpoint, but it does require the agent to explicitly orchestrate that proofreading step and reburn the subtitles from the refined Chinese SRT. The one-command script cannot automatically access the agent's chat model unless that model is exposed through a configured API endpoint. The final response should explicitly state that the Google draft was proofread against the English original.

In short: OpenAI-compatible is only the protocol currently supported by the script's built-in API caller. It is not a limit on which LLM an agent may use for subtitle proofreading.

For agent-side refinement without a configured LLM API endpoint, use a staged run:

```bash
./burn_bilingual_link.sh "<video-url>" --translation-refine off --stop-after translated
```

Then proofread the generated `chinese_draft_srt` against `english_srt`, keep the same SRT block count/order/timestamps, and reburn:

```bash
python3 reburn_bilingual_run.py "<run-dir>"
```

If the user provides a local file:

```bash
./burn_bilingual_link.sh "/absolute/path/to/video.mp4"
```

Expected artifacts:

```text
burned_video: <final mp4 path>
manifest: <manifest.json path>
review: <review.txt path>
verification_frames:
- <frames/verify-01.jpg>  # frame 0 thumbnail check; must not be black
- <frames/verify-02.jpg>
- <frames/verify-03.jpg>
```

Operational notes:

- Use a long-running terminal/session for videos over 10 minutes.
- If a run is interrupted, inspect the newest run directory and `manifest.json` before starting over.
- The wrapper checks for ffmpeg/libass before expensive transcription work.
- The default layout uses warm-yellow English, white Chinese, and a semi-transparent dark subtitle box.
- If subtitles are hard to read, first darken the subtitle box instead of making both lines pure yellow.
- If frame 0 is black, the workflow should repair it so social platforms do not use a black thumbnail.
