#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from subtitle_pipeline_local import (
    DEFAULT_SERVER_URL,
    DEFAULT_TRANSLATOR_BACKEND,
    DEFAULT_WRAP_WIDTH,
    SubtitleBlock,
    extract_audio,
    group_segments,
    load_glossary,
    normalize_spaces,
    render_plain_srt,
    render_srt,
    seconds_to_srt,
    translate_texts,
    validate_blocks,
    write_atomic,
    write_review_file,
)


DEFAULT_FORMAT = (
    "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
)
FALLBACK_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
DEFAULT_PARAKEET_V2_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"
DEFAULT_MODEL_CACHE_ROOT = Path.home() / "Tools" / "Local-LLM"
SENTENCE_END = ".?!。？！…"
SOFT_BREAK = ",;:，；：、"
ASS_SIZE_TABLE = {360: (22, 13), 720: (22, 13), 1080: (20, 12), 2160: (20, 12)}
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{cn_size},&H00FFFFFF,&H000000FF,&H78000000,{back_color},{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,20,20,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


class WorkflowError(RuntimeError):
    pass


class PseudoWord:
    def __init__(self, start: float, end: float, word: str) -> None:
        self.start = start
        self.end = end
        self.word = word


def run_command(args: Sequence[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        raise WorkflowError(f"command failed: {' '.join(args)}\n{completed.stdout}")
    return completed.stdout


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise WorkflowError(f"missing required command on PATH: {name}")


def executable_path(value: str | None) -> str | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if path.exists() and path.is_file() and path.stat().st_mode & 0o111:
        return str(path)
    resolved = shutil.which(value)
    return resolved


def command_output(args: Sequence[str]) -> tuple[int, str]:
    completed = subprocess.run(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return int(completed.returncode), completed.stdout


def ffmpeg_supports_ass(ffmpeg_bin: str) -> bool:
    code, output = command_output([ffmpeg_bin, "-hide_banner", "-filters"])
    if code != 0:
        return False
    return bool(re.search(r"(^|\n)\s*..?\s+ass\s+", output))


def matching_ffprobe(ffmpeg_bin: str, override: str | None) -> str:
    if override:
        resolved = executable_path(override)
        if resolved is None:
            raise WorkflowError(f"ffprobe override is not executable: {override}")
        return resolved

    sibling = Path(ffmpeg_bin).with_name("ffprobe")
    if sibling.exists() and sibling.is_file() and sibling.stat().st_mode & 0o111:
        return str(sibling)

    resolved = shutil.which("ffprobe")
    if resolved is None:
        raise WorkflowError("missing required command on PATH: ffprobe")
    return resolved


def resolve_media_tools(ffmpeg_override: str | None, ffprobe_override: str | None) -> tuple[str, str, list[str]]:
    candidates: list[str] = []
    for item in [
        ffmpeg_override,
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        shutil.which("ffmpeg"),
    ]:
        resolved = executable_path(item) if item else None
        if resolved and resolved not in candidates:
            candidates.append(resolved)

    failures: list[str] = []
    for candidate in candidates:
        if ffmpeg_supports_ass(candidate):
            return candidate, matching_ffprobe(candidate, ffprobe_override), failures
        failures.append(f"{candidate}: missing libass ass filter")

    raise WorkflowError(
        "no ffmpeg with libass/ass filter found. Install or expose ffmpeg-full first:\n"
        "  brew install libass ffmpeg-full\n"
        "Expected binary: /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg\n"
        + ("\nChecked:\n" + "\n".join(failures) if failures else "")
    )


def ytdlp_command() -> list[str]:
    binary = shutil.which("yt-dlp")
    if binary is not None:
        return [binary]
    return [sys.executable, "-m", "yt_dlp"]


def validate_url(url: str) -> str:
    cleaned = url.strip()
    if not re.match(r"^https?://", cleaned, flags=re.IGNORECASE):
        raise ValueError("url must start with http:// or https://")
    return cleaned


def safe_filename(value: str, fallback: str = "video") -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return safe[:120] or fallback


def escape_filter_path(path: Path) -> str:
    value = str(path)
    replacements = {
        "\\": "\\\\",
        ":": "\\:",
        "'": "\\'",
        ",": "\\,",
        " ": "\\ ",
        "[": "\\[",
        "]": "\\]",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


def newest_media_file(root: Path) -> Path:
    candidates = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
    ]
    if not candidates:
        raise WorkflowError(f"download finished but no media file found in {root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def extract_printed_filepath(output: str) -> Path | None:
    for line in reversed(output.splitlines()):
        candidate = Path(line.strip())
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def run_ytdlp_attempt(
    url: str,
    download_dir: Path,
    browser: str,
    proxy: str,
    fmt: str,
    use_cookies: bool,
) -> Path:
    outtmpl = str(download_dir / "%(title).80s-%(id)s.%(ext)s")
    command = [
        *ytdlp_command(),
        "--no-playlist",
        "--restrict-filenames",
        "--merge-output-format",
        "mp4",
        "--retries",
        "10",
        "--fragment-retries",
        "10",
        "--concurrent-fragments",
        "1",
        "-f",
        fmt,
        "-o",
        outtmpl,
        "--print",
        "after_move:filepath",
    ]
    if proxy:
        command.extend(["--proxy", proxy])
    if use_cookies:
        command.extend(["--cookies-from-browser", browser])
    command.append(url)

    output = run_command(command)
    return extract_printed_filepath(output) or newest_media_file(download_dir)


def download_video(url: str, download_dir: Path, browser: str, proxy: str) -> tuple[Path, list[str]]:
    download_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    attempts = [
        (DEFAULT_FORMAT, False),
        (DEFAULT_FORMAT, True),
        (FALLBACK_FORMAT, False),
        (FALLBACK_FORMAT, True),
    ]

    for fmt, use_cookies in attempts:
        try:
            return run_ytdlp_attempt(url, download_dir, browser, proxy, fmt, use_cookies), errors
        except WorkflowError as exc:
            errors.append(str(exc))

    raise WorkflowError("all yt-dlp download attempts failed:\n" + "\n\n".join(errors))


def apply_subtitle_profile_defaults(args: argparse.Namespace) -> None:
    if args.subtitle_profile == "news-box":
        defaults = {
            "cn_size": 50,
            "en_size": 44,
            "marginv": 240,
            "ass_en_wrap": 96,
            "ass_zh_wrap": 34,
            "ass_outline": 2.4,
            "ass_shadow": 0.0,
            "ass_bold": 1,
            "ass_border_style": 3,
            "ass_back_color": "&H98000000&",
            "en_color": "warm-yellow",
            "zh_color": "white",
        }
    elif args.subtitle_profile == "news-safe":
        defaults = {
            "cn_size": 40,
            "en_size": 34,
            "marginv": 320,
            "ass_en_wrap": 96,
            "ass_zh_wrap": 34,
            "ass_outline": 2.6,
            "ass_shadow": 0.0,
            "ass_bold": 1,
            "ass_border_style": 1,
            "ass_back_color": "&H00000000&",
            "en_color": "warm-yellow",
            "zh_color": "white",
        }
    else:
        defaults = {
            "cn_size": 54,
            "en_size": 36,
            "marginv": 72,
            "ass_en_wrap": 48,
            "ass_zh_wrap": 18,
            "ass_outline": 3.0,
            "ass_shadow": 0.0,
            "ass_bold": 1,
            "ass_border_style": 1,
            "ass_back_color": "&H00000000&",
            "en_color": "warm-yellow",
            "zh_color": "white",
        }

    for name, value in defaults.items():
        if getattr(args, name) is None:
            setattr(args, name, value)


def normalize_transcribe_language(language: str) -> str | None:
    cleaned = language.strip()
    if not cleaned or cleaned.lower() == "auto":
        return None
    return re.split(r"[-_]", cleaned, maxsplit=1)[0].lower()


def transcribe_with_server(audio_path: Path, server_url: str, timeout_seconds: int) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(prefix="subtitle-verbose-", suffix=".json", delete=False) as handle:
        tmp_json_path = Path(handle.name)
    try:
        run_command(
            [
                "curl",
                "-sS",
                "-f",
                "--max-time",
                str(timeout_seconds),
                f"{server_url.rstrip('/')}/inference",
                "-H",
                "Content-Type: multipart/form-data",
                "-F",
                f"file=@{audio_path}",
                "-F",
                "temperature=0.0",
                "-F",
                "response_format=verbose_json",
                "-o",
                str(tmp_json_path),
            ]
        )
        payload = json.loads(tmp_json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("whisper server returned a non-object JSON payload")
        return payload
    finally:
        tmp_json_path.unlink(missing_ok=True)


def transcribe_with_mlx(audio_path: Path, language: str | None) -> tuple[list[Any], dict[str, Any]]:
    try:
        import mlx_whisper
    except ModuleNotFoundError as exc:
        raise WorkflowError(
            "mlx-whisper is not installed; run with `uv --with mlx-whisper` or use faster-whisper"
        ) from exc

    kwargs: dict[str, Any] = {
        "path_or_hf_repo": "mlx-community/whisper-large-v3-turbo",
        "word_timestamps": True,
    }
    if language is not None:
        kwargs["language"] = language
    result = mlx_whisper.transcribe(str(audio_path), **kwargs)

    class Word:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.start = float(payload["start"])
            self.end = float(payload["end"])
            self.word = str(payload.get("word", ""))

    class Segment:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.start = float(payload["start"])
            self.end = float(payload["end"])
            self.text = str(payload.get("text", ""))
            self.words = [Word(word) for word in payload.get("words", [])]

    return [Segment(segment) for segment in result.get("segments", [])], result


def transcribe_with_faster(audio_path: Path, language: str | None, model_name: str) -> tuple[list[Any], dict[str, Any]]:
    try:
        from faster_whisper import WhisperModel
    except ModuleNotFoundError as exc:
        raise WorkflowError(
            "faster-whisper is not installed; run with `uv --with faster-whisper`"
        ) from exc

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    transcribe_kwargs: dict[str, Any] = {"word_timestamps": True}
    if language is not None:
        transcribe_kwargs["language"] = language
    segments_iter, info = model.transcribe(str(audio_path), **transcribe_kwargs)
    return list(segments_iter), {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
    }


def transcribe_with_parakeet_v2(
    audio_path: Path,
    language: str | None,
    model_name: str,
    max_line_ms: int,
    pause_ms: int,
    max_block_chars: int,
    chunk_duration: float,
    overlap_duration: float,
    cache_dir: Path,
) -> tuple[list[SubtitleBlock], dict[str, Any]]:
    if language is not None and not language.lower().startswith("en"):
        raise WorkflowError(
            f"Parakeet v2 is English-only; requested transcribe language is {language!r}"
        )

    try:
        from parakeet_mlx import DecodingConfig, SentenceConfig, from_pretrained
    except ModuleNotFoundError as exc:
        raise WorkflowError(
            "parakeet-mlx is not installed; run the wrapper with `--quality accurate` "
            "or install `parakeet-mlx`"
        ) from exc

    max_words = max(8, min(28, max_block_chars // 5))
    sentence_config = SentenceConfig(
        max_words=max_words,
        silence_gap=max(0.2, pause_ms / 1000),
        max_duration=max(1.0, max_line_ms / 1000),
    )
    decoding_config = DecodingConfig(sentence=sentence_config)
    cache_dir.mkdir(parents=True, exist_ok=True)
    model = from_pretrained(model_name, cache_dir=str(cache_dir))
    result = model.transcribe(
        str(audio_path),
        decoding_config=decoding_config,
        chunk_duration=chunk_duration,
        overlap_duration=overlap_duration,
    )

    blocks: list[SubtitleBlock] = []
    for sentence in getattr(result, "sentences", []) or []:
        text = normalize_spaces(str(getattr(sentence, "text", "")))
        if not text:
            continue
        start = float(getattr(sentence, "start"))
        end = float(getattr(sentence, "end"))
        if end <= start:
            end = start + 0.3
        if blocks and start < blocks[-1].end:
            start = blocks[-1].end
        if end <= start:
            end = start + 0.3
        blocks.append(SubtitleBlock(index=len(blocks) + 1, start=start, end=end, text=text))

    return blocks, {
        "model": model_name,
        "cache_dir": str(cache_dir),
        "result_text": normalize_spaces(str(getattr(result, "text", ""))),
        "sentence_count": len(blocks),
        "sentence_config": {
            "max_words": max_words,
            "silence_gap": sentence_config.silence_gap,
            "max_duration": sentence_config.max_duration,
        },
        "chunk_duration": chunk_duration,
        "overlap_duration": overlap_duration,
    }


def flush_words(words: list[Any]) -> dict[str, Any] | None:
    text = "".join(str(word.word) for word in words).strip()
    if not text:
        return None
    return {"start": float(words[0].start), "end": float(words[-1].end), "text": text}


def find_soft_cut(words: list[Any]) -> int | None:
    for index in range(len(words) - 1, -1, -1):
        text = str(words[index].word).strip()
        if text and text[-1] in SOFT_BREAK:
            return index
    return None


def find_pause_cut(words: list[Any], min_gap: float = 0.2) -> int | None:
    best_gap = min_gap
    best_index: int | None = None
    start_index = max(1, len(words) // 3)
    for index in range(start_index, len(words) - 1):
        gap = float(words[index + 1].start) - float(words[index].end)
        if gap > best_gap:
            best_gap = gap
            best_index = index
    return best_index


def postprocess_word_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for segment in segments:
        if float(segment["end"]) <= float(segment["start"]):
            continue
        if cleaned and segment["text"] == cleaned[-1]["text"]:
            cleaned[-1]["end"] = max(float(cleaned[-1]["end"]), float(segment["end"]))
            continue
        cleaned.append(segment)

    merged: list[dict[str, Any]] = []
    for segment in cleaned:
        duration = float(segment["end"]) - float(segment["start"])
        word_count = len(str(segment["text"]).split())
        if merged and duration < 0.4 and word_count < 3:
            merged[-1]["end"] = segment["end"]
            merged[-1]["text"] = f'{merged[-1]["text"]} {segment["text"]}'
        else:
            merged.append(segment)

    for index in range(1, len(merged)):
        if float(merged[index]["start"]) < float(merged[index - 1]["end"]):
            merged[index]["start"] = merged[index - 1]["end"]
        if float(merged[index]["end"]) <= float(merged[index]["start"]):
            merged[index]["end"] = float(merged[index]["start"]) + 0.3
    return merged


def merge_words_to_blocks(
    segments: Sequence[Any],
    max_line_ms: int,
    pause_ms: int,
    max_chars: int,
) -> list[SubtitleBlock]:
    flat_words: list[Any] = []
    for segment in segments:
        words = list(getattr(segment, "words", []) or [])
        if words:
            flat_words.extend(words)
        else:
            flat_words.append(
                PseudoWord(
                    start=float(getattr(segment, "start")),
                    end=float(getattr(segment, "end")),
                    word=str(getattr(segment, "text", "")),
                )
            )

    if not flat_words:
        return []

    result: list[dict[str, Any]] = []
    current: list[Any] = []
    for index, word in enumerate(flat_words):
        current.append(word)
        word_text = str(word.word).strip()
        current_text = "".join(str(item.word) for item in current).strip()
        current_duration_ms = (float(word.end) - float(current[0].start)) * 1000
        gap_ms = (
            (float(flat_words[index + 1].start) - float(word.end)) * 1000
            if index + 1 < len(flat_words)
            else 0
        )

        end_sentence = bool(word_text) and word_text[-1] in SENTENCE_END
        big_pause = gap_ms >= pause_ms
        too_long = current_duration_ms >= max_line_ms or len(current_text) >= max_chars

        if end_sentence or big_pause:
            segment = flush_words(current)
            if segment is not None:
                result.append(segment)
            current = []
            continue

        if too_long:
            cut = find_soft_cut(current)
            if cut is None or cut >= len(current) - 1:
                cut = find_pause_cut(current)
            if cut is not None and cut < len(current) - 1:
                head, current = current[: cut + 1], current[cut + 1 :]
                segment = flush_words(head)
                if segment is not None:
                    result.append(segment)
            else:
                segment = flush_words(current)
                if segment is not None:
                    result.append(segment)
                current = []

    if current:
        segment = flush_words(current)
        if segment is not None:
            result.append(segment)

    return [
        SubtitleBlock(index=index, start=float(segment["start"]), end=float(segment["end"]), text=str(segment["text"]))
        for index, segment in enumerate(postprocess_word_segments(result), start=1)
    ]


def blocks_to_payload(blocks: list[SubtitleBlock], metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        **metadata,
        "segments": [
            {"id": block.index - 1, "start": block.start, "end": block.end, "text": block.text}
            for block in blocks
        ],
    }


def transcribe_audio(audio_path: Path, args: argparse.Namespace) -> tuple[dict[str, Any], list[SubtitleBlock], str, list[str]]:
    errors: list[str] = []
    language = normalize_transcribe_language(args.transcribe_language)

    if args.transcriber == "server":
        try:
            payload = transcribe_with_server(audio_path, args.server_url, args.server_timeout)
            raw_segments = payload.get("segments")
            if not isinstance(raw_segments, list):
                raise ValueError("whisper server response does not contain a segment list")
            blocks = group_segments(
                segments=raw_segments,
                max_block_seconds=args.max_block_seconds,
                max_block_chars=args.max_block_chars,
                max_gap_seconds=args.max_gap_seconds,
            )
            validate_blocks(blocks)
            payload["transcriber"] = "whisper.cpp-server"
            return payload, blocks, "whisper.cpp-server", errors
        except Exception as exc:
            raise

    if args.transcriber == "auto":
        local_attempts = ["parakeet-v2", "mlx", "faster"] if args.quality == "accurate" else ["mlx", "faster"]
    else:
        local_attempts = [args.transcriber]

    for engine in local_attempts:
        try:
            started = time.time()
            if engine == "parakeet-v2":
                blocks, metadata = transcribe_with_parakeet_v2(
                    audio_path=audio_path,
                    language=language,
                    model_name=args.parakeet_model,
                    max_line_ms=args.max_line_ms,
                    pause_ms=args.pause_ms,
                    max_block_chars=args.max_block_chars,
                    chunk_duration=args.parakeet_chunk_duration,
                    overlap_duration=args.parakeet_overlap_duration,
                    cache_dir=args.model_cache_root / "parakeet-models" / "huggingface",
                )
                validate_blocks(blocks)
                payload = blocks_to_payload(
                    blocks,
                    {
                        "transcriber": engine,
                        "metadata": metadata,
                        "elapsed_seconds": round(time.time() - started, 3),
                    },
                )
                return payload, blocks, engine, errors
            if engine == "mlx":
                local_segments, metadata = transcribe_with_mlx(audio_path, language)
            elif engine == "faster":
                local_segments, metadata = transcribe_with_faster(audio_path, language, args.faster_model)
            else:
                raise ValueError(f"unsupported transcriber: {engine}")
            blocks = merge_words_to_blocks(
                local_segments,
                max_line_ms=args.max_line_ms,
                pause_ms=args.pause_ms,
                max_chars=args.max_block_chars,
            )
            validate_blocks(blocks)
            payload = blocks_to_payload(
                blocks,
                {
                    "transcriber": engine,
                    "metadata": metadata,
                    "elapsed_seconds": round(time.time() - started, 3),
                },
            )
            return payload, blocks, engine, errors
        except Exception as exc:
            if args.transcriber != "auto":
                raise
            errors.append(f"{engine} failed: {exc}")

    if args.transcriber == "auto":
        try:
            payload = transcribe_with_server(audio_path, args.server_url, args.server_timeout)
            raw_segments = payload.get("segments")
            if not isinstance(raw_segments, list):
                raise ValueError("whisper server response does not contain a segment list")
            blocks = group_segments(
                segments=raw_segments,
                max_block_seconds=args.max_block_seconds,
                max_block_chars=args.max_block_chars,
                max_gap_seconds=args.max_gap_seconds,
            )
            validate_blocks(blocks)
            payload["transcriber"] = "whisper.cpp-server"
            return payload, blocks, "whisper.cpp-server", errors
        except Exception as exc:
            errors.append(f"server failed: {exc}")

    raise WorkflowError("all transcription attempts failed:\n" + "\n\n".join(errors))


def pick_ass_sizes(height: int | None, cn_size: int | None, en_size: int | None) -> tuple[int, int]:
    if cn_size is not None:
        picked_cn = cn_size
        picked_en = max(8, round(cn_size / 1.7))
    else:
        key = min(ASS_SIZE_TABLE, key=lambda item: abs(item - (height or 720)))
        picked_cn, picked_en = ASS_SIZE_TABLE[key]
    if en_size is not None:
        picked_en = en_size
    return picked_cn, picked_en


def srt_time_to_ass(value: str) -> str:
    normalized = value.replace(".", ",")
    hms, milliseconds = normalized.split(",")
    hours, minutes, seconds = hms.split(":")
    centiseconds = int(round(int(milliseconds) / 10.0))
    if centiseconds >= 100:
        centiseconds = 99
    return f"{int(hours)}:{minutes}:{seconds}.{centiseconds:02d}"


ASS_COLOR_NAMES = {
    "white": "&HFFFFFF&",
    "warm-white": "&HF4F7FF&",
    "soft-yellow": "&HA8F2FF&",
    "warm-yellow": "&H4DD3FF&",
    "yellow": "&H00FFFF&",
}


def ass_color(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned in ASS_COLOR_NAMES:
        return ASS_COLOR_NAMES[cleaned]
    if re.fullmatch(r"&h(?:[0-9a-f]{6}|[0-9a-f]{8})&", cleaned, flags=re.IGNORECASE):
        return cleaned.upper()
    if re.fullmatch(r"#[0-9a-f]{6}", cleaned, flags=re.IGNORECASE):
        red = cleaned[1:3]
        green = cleaned[3:5]
        blue = cleaned[5:7]
        return f"&H{blue}{green}{red}&".upper()
    raise ValueError(
        "unsupported ASS color: "
        f"{value}; use white, warm-white, soft-yellow, warm-yellow, yellow, #RRGGBB, or &H[AA]BBGGRR&"
    )


def clean_ass_text(text: str) -> str:
    cleaned = normalize_spaces(text)
    return cleaned.replace("\\", "/").replace("{", "(").replace("}", ")")


def wrap_latin_ass_text(text: str, width: int) -> list[str]:
    cleaned = clean_ass_text(text)
    if not cleaned:
        return [""]
    words = cleaned.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if not word:
            continue
        if len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.extend(word[index : index + width] for index in range(0, len(word), width))
            continue
        candidate = word if not current else f"{current} {word}"
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [cleaned]


def wrap_cjk_ass_text(text: str, width: int) -> list[str]:
    cleaned = clean_ass_text(text)
    if not cleaned:
        return [""]
    chunks = re.split(r"([，。！？；：、“”‘’,.!?;:])", cleaned)
    segments: list[str] = []
    for index in range(0, len(chunks), 2):
        piece = chunks[index]
        punctuation = chunks[index + 1] if index + 1 < len(chunks) else ""
        token = f"{piece}{punctuation}".strip()
        if token:
            segments.append(token)

    lines: list[str] = []
    current = ""
    for segment in segments or [cleaned]:
        candidate = f"{current}{segment}"
        if current and len(candidate) > width:
            lines.append(current)
            current = segment
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [cleaned]


def cjk_block_ratio(texts: Sequence[str]) -> float:
    if not texts:
        return 0.0
    cjk_blocks = sum(1 for text in texts if re.search(r"[\u3400-\u9fff]", text))
    return cjk_blocks / len(texts)


def validate_translation_quality(texts: Sequence[str], target_lang: str, min_cjk_block_ratio: float) -> None:
    if not target_lang.lower().startswith("zh") or min_cjk_block_ratio <= 0:
        return
    ratio = cjk_block_ratio(texts)
    if ratio < min_cjk_block_ratio:
        raise WorkflowError(
            f"translation quality check failed: CJK block ratio {ratio:.2f} < {min_cjk_block_ratio:.2f}; "
            "not burning likely-untranslated subtitles"
        )


def render_bilingual_srt(
    english_blocks: list[SubtitleBlock],
    chinese_blocks: list[SubtitleBlock],
) -> str:
    if len(english_blocks) != len(chinese_blocks):
        raise ValueError(
            f"bilingual render requires equal block counts: en={len(english_blocks)}, zh={len(chinese_blocks)}"
        )

    rendered: list[str] = []
    for english_block, chinese_block in zip(english_blocks, chinese_blocks):
        if english_block.index != chinese_block.index:
            raise ValueError(f"block index mismatch near {english_block.index}")
        rendered.append(
            "\n".join(
                [
                    str(english_block.index),
                    f"{seconds_to_srt(english_block.start)} --> {seconds_to_srt(english_block.end)}",
                    normalize_spaces(english_block.text),
                    normalize_spaces(chinese_block.text),
                    "",
                ]
            )
        )
    return "\n".join(rendered).strip() + "\n"


def render_english_top_ass(
    english_blocks: list[SubtitleBlock],
    chinese_blocks: list[SubtitleBlock],
    cn_size: int,
    en_size: int,
    font: str,
    marginv: int,
    en_wrap: int,
    zh_wrap: int,
    outline: float,
    shadow: float,
    bold: int,
    border_style: int,
    back_color: str,
    en_color: str,
    zh_color: str,
) -> str:
    if len(english_blocks) != len(chinese_blocks):
        raise ValueError("ASS render requires equal block counts")

    lines = [
        ASS_HEADER.format(
            font=font,
            cn_size=cn_size,
            marginv=marginv,
            outline=outline,
            shadow=shadow,
            bold=bold,
            border_style=border_style,
            back_color=ass_color(back_color),
        )
    ]
    english_color = ass_color(en_color)
    chinese_color = ass_color(zh_color)
    for english_block, chinese_block in zip(english_blocks, chinese_blocks):
        start = srt_time_to_ass(seconds_to_srt(english_block.start))
        end = srt_time_to_ass(seconds_to_srt(english_block.end))
        english = "\\N".join(wrap_latin_ass_text(english_block.text, en_wrap))
        chinese = "\\N".join(wrap_cjk_ass_text(chinese_block.text, zh_wrap))
        text = f"{{\\fs{en_size}\\c{english_color}}}{english}\\N{{\\fs{cn_size}\\c{chinese_color}}}{chinese}"
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def probe_video_height(video_path: Path, ffprobe_bin: str) -> int | None:
    try:
        output = run_command(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
        )
    except WorkflowError:
        return None
    try:
        return int(output.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None


def detect_leading_black_end(video_path: Path, ffmpeg_bin: str) -> float | None:
    try:
        output = run_command(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-nostats",
                "-t",
                "3",
                "-i",
                str(video_path),
                "-vf",
                "blackdetect=d=0.05:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "-",
            ]
        )
    except WorkflowError:
        return None

    pattern = re.compile(
        r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<duration>[0-9.]+)"
    )
    for match in pattern.finditer(output):
        start = float(match.group("start"))
        end = float(match.group("end"))
        duration = float(match.group("duration"))
        if start <= 0.05 and duration >= 0.05 and end > 0:
            return min(end, 3.0)
    return None


def first_frame_cover_filter(ass_path: Path, leading_black_end: float) -> str:
    cover_duration = max(0.05, min(leading_black_end, 3.0))
    cover_frame_time = min(max(cover_duration + 0.12, 0.5), 3.0)
    cover_frame_end = cover_frame_time + 0.05
    ass_filter = f"ass={escape_filter_path(ass_path)}"
    return (
        f"[0:v]trim=start={cover_frame_time:.3f}:end={cover_frame_end:.3f},"
        f"setpts=PTS-STARTPTS,loop=loop=180:size=1:start=0,"
        f"trim=duration={cover_duration:.3f}[cover];"
        f"[0:v]trim=start={cover_duration:.3f},setpts=PTS-STARTPTS[main];"
        f"[cover][main]concat=n=2:v=1:a=0,{ass_filter}[v]"
    )


def burn_subtitles(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    ffmpeg_bin: str,
    *,
    fix_black_first_frame: bool,
) -> tuple[list[str], float | None]:
    leading_black_end = detect_leading_black_end(video_path, ffmpeg_bin) if fix_black_first_frame else None
    if leading_black_end is not None:
        command = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-filter_complex",
            first_frame_cover_filter(ass_path, leading_black_end),
            "-map",
            "[v]",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        run_command(command)
        return command, leading_black_end

    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"ass={escape_filter_path(ass_path)}",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    run_command(command)
    return command, leading_black_end


def representative_frame_times(blocks: list[SubtitleBlock]) -> list[float]:
    if not blocks:
        return [0.0, 1.0]
    indexes = sorted({0, min(len(blocks) - 1, max(0, len(blocks) // 2))})
    return [0.0] + [max(0.0, (blocks[index].start + blocks[index].end) / 2) for index in indexes]


def extract_verification_frames(video_path: Path, frame_dir: Path, times: list[float], ffmpeg_bin: str) -> list[Path]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for index, timestamp in enumerate(times, start=1):
        frame_path = frame_dir / f"verify-{index:02d}.jpg"
        run_command(
            [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-vframes",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ]
        )
        frames.append(frame_path)
    return frames


def process_url(args: argparse.Namespace) -> dict[str, object]:
    local_path = Path(args.local).expanduser().resolve() if args.local else None
    if local_path:
        if not local_path.is_file():
            raise WorkflowError(f"local file not found: {local_path}")
        url = str(local_path)
    else:
        url = validate_url(args.url)
    ffmpeg_bin, ffprobe_bin, ffmpeg_detection_notes = resolve_media_tools(args.ffmpeg_bin, args.ffprobe_bin)
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("bilingual-%Y%m%d-%H%M%S")
    run_dir = output_root / run_id
    download_dir = run_dir / "download"
    frame_dir = run_dir / "frames"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest: dict[str, object] = {
        "url": url,
        "run_dir": str(run_dir),
        "status": "started",
        "subtitle_profile": args.subtitle_profile,
        "ffmpeg_bin": ffmpeg_bin,
        "ffprobe_bin": ffprobe_bin,
        "ffmpeg_detection_notes": ffmpeg_detection_notes,
    }

    def save_manifest(status: str, **updates: object) -> None:
        manifest.update(updates)
        manifest["status"] = status
        manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    save_manifest("started")

    if local_path:
        video_path = local_path
        download_errors: list[str] = []
        save_manifest("downloaded", downloaded_video=str(video_path), local_file=True)
    else:
        video_path, download_errors = download_video(
            url=url,
            download_dir=download_dir,
            browser=args.browser,
            proxy=args.proxy,
        )
        save_manifest("downloaded", downloaded_video=str(video_path), download_retry_errors=download_errors)
    stem = safe_filename(video_path.stem)
    verbose_json_path = run_dir / f"{stem}.verbose.json"
    english_srt_path = run_dir / f"{stem}.en.srt"
    chinese_srt_path = run_dir / f"{stem}.zh.draft.srt"
    bilingual_srt_path = run_dir / f"{stem}.en-top.zh-bottom.srt"
    ass_path = run_dir / f"{stem}.en-top.zh-bottom.ass"
    review_path = run_dir / f"{stem}.review.txt"
    burned_path = run_dir / f"{stem}.en-top.zh-bottom.burned.mp4"

    glossary = load_glossary(args.glossary)
    with tempfile.TemporaryDirectory(prefix="video-link-bilingual-") as temp_root:
        audio_path = Path(temp_root) / "input.wav"
        extract_audio(video_path, audio_path)
        payload, english_blocks, transcriber, transcribe_errors = transcribe_audio(audio_path, args)

    write_atomic(verbose_json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    write_atomic(english_srt_path, render_plain_srt(english_blocks))
    save_manifest(
        "transcribed",
        verbose_json=str(verbose_json_path),
        english_srt=str(english_srt_path),
        transcriber=transcriber,
        transcribe_retry_errors=transcribe_errors,
        subtitle_block_count=len(english_blocks),
    )

    translated_texts = translate_texts(
        texts=[block.text for block in english_blocks],
        glossary=glossary,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        translator_backend=args.translator_backend,
    )
    validate_translation_quality(translated_texts, args.target_lang, args.min_cjk_block_ratio)
    chinese_blocks = [
        SubtitleBlock(index=block.index, start=block.start, end=block.end, text=translated_texts[index])
        for index, block in enumerate(english_blocks)
    ]
    validate_blocks(chinese_blocks)
    write_atomic(chinese_srt_path, render_srt(chinese_blocks, args.wrap_width))
    write_review_file(review_path, english_blocks, chinese_blocks)
    write_atomic(bilingual_srt_path, render_bilingual_srt(english_blocks, chinese_blocks))
    save_manifest(
        "translated",
        chinese_draft_srt=str(chinese_srt_path),
        bilingual_srt=str(bilingual_srt_path),
        review=str(review_path),
        translator_backend=args.translator_backend,
    )

    height = probe_video_height(video_path, ffprobe_bin)
    cn_size, en_size = pick_ass_sizes(height, args.cn_size, args.en_size)
    write_atomic(
        ass_path,
        render_english_top_ass(
            english_blocks=english_blocks,
            chinese_blocks=chinese_blocks,
            cn_size=cn_size,
            en_size=en_size,
            font=args.font,
            marginv=args.marginv,
            en_wrap=args.ass_en_wrap,
            zh_wrap=args.ass_zh_wrap,
            outline=args.ass_outline,
            shadow=args.ass_shadow,
            bold=args.ass_bold,
            border_style=args.ass_border_style,
            back_color=args.ass_back_color,
            en_color=args.en_color,
            zh_color=args.zh_color,
        ),
    )
    save_manifest(
        "ass_ready",
        bilingual_ass=str(ass_path),
        video_height=height,
        ass_style={
            "font": args.font,
            "cn_size": cn_size,
            "en_size": en_size,
            "marginv": args.marginv,
            "line_order": "english_top_chinese_bottom",
            "en_wrap": args.ass_en_wrap,
            "zh_wrap": args.ass_zh_wrap,
            "outline": args.ass_outline,
            "shadow": args.ass_shadow,
            "bold": args.ass_bold,
            "border_style": args.ass_border_style,
            "back_color": args.ass_back_color,
            "en_color": args.en_color,
            "zh_color": args.zh_color,
        },
    )

    burn_command, leading_black_end = burn_subtitles(
        video_path,
        ass_path,
        burned_path,
        ffmpeg_bin,
        fix_black_first_frame=args.fix_black_first_frame,
    )
    frames = extract_verification_frames(
        video_path=burned_path,
        frame_dir=frame_dir,
        times=representative_frame_times(english_blocks),
        ffmpeg_bin=ffmpeg_bin,
    )

    final_manifest: dict[str, object] = {
        "url": url,
        "run_dir": str(run_dir),
        "downloaded_video": str(video_path),
        "verbose_json": str(verbose_json_path),
        "english_srt": str(english_srt_path),
        "chinese_draft_srt": str(chinese_srt_path),
        "bilingual_srt": str(bilingual_srt_path),
        "bilingual_ass": str(ass_path),
        "review": str(review_path),
        "burned_video": str(burned_path),
        "verification_frames": [str(path) for path in frames],
        "video_height": height,
        "subtitle_profile": args.subtitle_profile,
        "ffmpeg_bin": ffmpeg_bin,
        "ffprobe_bin": ffprobe_bin,
        "ffmpeg_detection_notes": ffmpeg_detection_notes,
        "fix_black_first_frame": args.fix_black_first_frame,
        "leading_black_end": leading_black_end,
        "model_cache_root": str(args.model_cache_root),
        "ass_style": {
            "font": args.font,
            "cn_size": cn_size,
            "en_size": en_size,
            "marginv": args.marginv,
            "line_order": "english_top_chinese_bottom",
            "en_wrap": args.ass_en_wrap,
            "zh_wrap": args.ass_zh_wrap,
            "outline": args.ass_outline,
            "shadow": args.ass_shadow,
            "bold": args.ass_bold,
            "border_style": args.ass_border_style,
            "back_color": args.ass_back_color,
            "en_color": args.en_color,
            "zh_color": args.zh_color,
        },
        "translator_backend": args.translator_backend,
        "transcriber": transcriber,
        "transcribe_retry_errors": transcribe_errors,
        "download_retry_errors": download_errors,
        "burn_command": burn_command,
        "subtitle_blocks": [asdict(block) for block in english_blocks],
    }
    save_manifest("complete", **final_manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a video URL, create English-top Chinese-bottom bilingual subtitles, and burn them into an MP4."
    )
    parser.add_argument("url", help="Video URL supported by yt-dlp")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path.cwd() / "outputs",
        help="Root directory for all run artifacts. Default: ./outputs",
    )
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="whisper.cpp server URL")
    parser.add_argument("--ffmpeg-bin", default=None, help="Optional ffmpeg binary override. Must support libass ass filter.")
    parser.add_argument("--ffprobe-bin", default=None, help="Optional ffprobe binary override")
    parser.add_argument(
        "--transcriber",
        choices=["auto", "server", "mlx", "faster", "parakeet-v2"],
        default="auto",
        help="Transcription engine. auto uses the selected quality ladder.",
    )
    parser.add_argument(
        "--quality",
        choices=["fast", "accurate"],
        default="accurate",
        help="Transcription quality ladder. accurate tries Parakeet v2 first; fast tries MLX first.",
    )
    parser.add_argument("--server-timeout", type=int, default=15, help="whisper.cpp server request timeout seconds")
    parser.add_argument("--transcribe-language", default="auto", help="Whisper language code, or auto")
    parser.add_argument("--faster-model", default="large-v3-turbo", help="faster-whisper model name")
    parser.add_argument("--parakeet-model", default=DEFAULT_PARAKEET_V2_MODEL, help="Parakeet v2 MLX model name")
    parser.add_argument("--parakeet-chunk-duration", type=float, default=120.0, help="Parakeet chunk duration seconds")
    parser.add_argument("--parakeet-overlap-duration", type=float, default=15.0, help="Parakeet chunk overlap seconds")
    parser.add_argument(
        "--model-cache-root",
        type=Path,
        default=Path(os.environ.get("SUBTITLE_MODEL_CACHE_ROOT", DEFAULT_MODEL_CACHE_ROOT)),
        help="Root directory for downloaded local ASR models.",
    )
    parser.add_argument("--local", type=Path, default=None, help="Use a local video file instead of downloading from URL")
    parser.add_argument("--browser", default="chrome", help="Browser used for yt-dlp cookie fallback")
    parser.add_argument("--proxy", default="", help="Optional proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--glossary", type=Path, default=None, help="Optional JSON glossary file")
    parser.add_argument("--source-lang", default="auto", help="Translation source language code")
    parser.add_argument("--target-lang", default="zh-CN", help="Translation target language code")
    parser.add_argument(
        "--translator-backend",
        choices=["google", "argos"],
        default=DEFAULT_TRANSLATOR_BACKEND,
        help="Translation backend. Google needs deep-translator; Argos needs installed Argos packages.",
    )
    parser.add_argument("--wrap-width", type=int, default=DEFAULT_WRAP_WIDTH, help="CJK wrap width for zh draft SRT")
    parser.add_argument("--max-block-seconds", type=float, default=7.0, help="Maximum source subtitle block duration")
    parser.add_argument("--max-block-chars", type=int, default=84, help="Maximum source subtitle block characters")
    parser.add_argument("--max-gap-seconds", type=float, default=0.75, help="Gap that forces a new subtitle block")
    parser.add_argument("--max-line-ms", type=int, default=6000, help="Local word-level subtitle max duration")
    parser.add_argument("--pause-ms", type=int, default=500, help="Local word-level pause split threshold")
    parser.add_argument(
        "--no-fix-black-first-frame",
        dest="fix_black_first_frame",
        action="store_false",
        help="Disable replacing a leading black video frame with the first visible frame for social thumbnails.",
    )
    parser.set_defaults(fix_black_first_frame=True)
    parser.add_argument(
        "--subtitle-profile",
        choices=["news-box", "news-safe", "standard"],
        default="news-box",
        help="Subtitle layout profile. news-box is the default high-contrast lower-third-safe style.",
    )
    parser.add_argument("--font", default="PingFang SC", help="ASS font name")
    parser.add_argument("--cn-size", type=int, default=None, help="Chinese ASS font size override")
    parser.add_argument("--en-size", type=int, default=None, help="English ASS font size override")
    parser.add_argument("--marginv", type=int, default=None, help="ASS bottom margin")
    parser.add_argument("--ass-outline", type=float, default=None, help="ASS outline thickness")
    parser.add_argument("--ass-shadow", type=float, default=None, help="ASS shadow thickness")
    parser.add_argument("--ass-bold", type=int, choices=[0, 1], default=None, help="ASS bold flag")
    parser.add_argument("--ass-border-style", type=int, choices=[1, 3], default=None, help="ASS border style: 1 outline, 3 boxed background")
    parser.add_argument(
        "--ass-back-color",
        default=None,
        help="ASS background color for boxed subtitles: white, warm-white, soft-yellow, warm-yellow, yellow, #RRGGBB, or &H[AA]BBGGRR&",
    )
    parser.add_argument(
        "--en-color",
        default=None,
        help="English subtitle color: white, warm-white, soft-yellow, warm-yellow, yellow, #RRGGBB, or &H[AA]BBGGRR&",
    )
    parser.add_argument(
        "--zh-color",
        default=None,
        help="Chinese subtitle color: white, warm-white, soft-yellow, warm-yellow, yellow, #RRGGBB, or &H[AA]BBGGRR&",
    )
    parser.add_argument("--ass-en-wrap", type=int, default=None, help="Maximum English characters per ASS line")
    parser.add_argument("--ass-zh-wrap", type=int, default=None, help="Maximum Chinese characters per ASS line")
    parser.add_argument(
        "--min-cjk-block-ratio",
        type=float,
        default=0.35,
        help="For zh targets, stop before burn if fewer translated blocks contain CJK text. Use 0 to disable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    apply_subtitle_profile_defaults(args)
    manifest = process_url(args)
    print("[done] burned_video=" + str(manifest["burned_video"]))
    print("[done] bilingual_srt=" + str(manifest["bilingual_srt"]))
    print("[done] review=" + str(manifest["review"]))
    print("[done] manifest=" + str(Path(str(manifest["run_dir"])) / "manifest.json"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise
