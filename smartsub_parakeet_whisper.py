#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from subtitle_pipeline_local import render_plain_srt, validate_blocks, write_atomic
from video_link_bilingual_burn import (
    DEFAULT_MODEL_CACHE_ROOT,
    DEFAULT_PARAKEET_V2_MODEL,
    normalize_transcribe_language,
    transcribe_with_parakeet_v2,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Whisper-compatible SmartSub bridge backed by Parakeet TDT 0.6B v2."
    )
    parser.add_argument("audio_file", type=Path, help="Input audio file from SmartSub.")
    parser.add_argument("--model", default="", help="Ignored Whisper model name kept for CLI compatibility.")
    parser.add_argument("--parakeet-model", default=DEFAULT_PARAKEET_V2_MODEL, help="Parakeet MLX model name.")
    parser.add_argument("--output_format", default="srt", help="SmartSub passes srt; only srt is supported.")
    parser.add_argument("--output_dir", type=Path, default=Path.cwd(), help="Directory where SmartSub expects the SRT.")
    parser.add_argument("--srtFile", type=Path, default=None, help="Optional exact SRT output path.")
    parser.add_argument("--srt-file", dest="srtFile", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--language", default="auto", help="Source language. Parakeet v2 is English-only.")
    parser.add_argument("--max-line-ms", type=int, default=6000)
    parser.add_argument("--pause-ms", type=int, default=500)
    parser.add_argument("--max-block-chars", type=int, default=84)
    parser.add_argument("--parakeet-chunk-duration", type=float, default=120.0)
    parser.add_argument("--parakeet-overlap-duration", type=float, default=15.0)
    parser.add_argument(
        "--model-cache-root",
        type=Path,
        default=Path(DEFAULT_MODEL_CACHE_ROOT),
        help="Root directory for local ASR model cache.",
    )
    args, unknown = parser.parse_known_args()
    if unknown:
        print(f"[warn] ignored unsupported whisper-compatible args: {' '.join(unknown)}", file=sys.stderr)
    return args


def output_path_for(args: argparse.Namespace) -> Path:
    if args.srtFile is not None:
        return Path(args.srtFile).expanduser()
    audio_file = Path(args.audio_file).expanduser()
    return Path(args.output_dir).expanduser() / f"{audio_file.stem}.srt"


def main() -> int:
    args = parse_args()
    audio_file = Path(args.audio_file).expanduser().resolve()
    if not audio_file.is_file():
        raise FileNotFoundError(f"audio file not found: {audio_file}")

    output_format = str(args.output_format).strip().lower()
    if output_format != "srt":
        raise ValueError(f"unsupported output_format for SmartSub bridge: {args.output_format!r}; use srt")

    language = normalize_transcribe_language(str(args.language))
    started = time.time()
    blocks, metadata = transcribe_with_parakeet_v2(
        audio_path=audio_file,
        language=language,
        model_name=str(args.parakeet_model),
        max_line_ms=int(args.max_line_ms),
        pause_ms=int(args.pause_ms),
        max_block_chars=int(args.max_block_chars),
        chunk_duration=float(args.parakeet_chunk_duration),
        overlap_duration=float(args.parakeet_overlap_duration),
        cache_dir=Path(args.model_cache_root).expanduser() / "parakeet-models" / "huggingface",
    )
    validate_blocks(blocks)

    srt_path = output_path_for(args)
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(srt_path, render_plain_srt(blocks))
    elapsed = round(time.time() - started, 3)
    print(f"[done] smartsub_parakeet_srt={srt_path}")
    print(f"[done] blocks={len(blocks)} elapsed_seconds={elapsed} model={metadata.get('model')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise
