# Agent SOP: video URL to bilingual hard-subtitled MP4

When the user provides a bare video URL, produce an MP4 with hard-burned bilingual subtitles.

Default requirements:

- English on top
- Chinese below
- hard subtitles burned into the video
- default profile: `news-box`
- default transcription quality: `accurate`
- output root: `$HOME/Downloads/bilingual-output`, unless `SUBTITLE_OUTPUT_ROOT` is set
- model cache root: `$HOME/Tools/Local-LLM`, unless `SUBTITLE_MODEL_CACHE_ROOT` is set
- the wrapper forces Hugging Face caches under the model cache root for the subprocess
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
