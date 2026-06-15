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
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_SERVER_URL = "http://127.0.0.1:8178"
DEFAULT_MAX_BLOCK_SECONDS = 7.0
DEFAULT_MAX_BLOCK_CHARS = 84
DEFAULT_MAX_GAP_SECONDS = 0.75
DEFAULT_WRAP_WIDTH = 22
DEFAULT_TRANSLATOR_BACKEND = "google"


def env_int(name: str, default: int, minimum: int = 1) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value >= minimum else default


def env_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.environ.get(name, default)
    return value if value in choices else default


DEFAULT_TRANSLATION_REFINE = env_choice("SUBTITLE_TRANSLATION_REFINE", "auto", {"off", "auto", "require"})
DEFAULT_LLM_REFINE_BASE_URL = os.environ.get("SUBTITLE_LLM_BASE_URL", "")
DEFAULT_LLM_REFINE_API_KEY = os.environ.get("SUBTITLE_LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
DEFAULT_LLM_REFINE_MODEL = os.environ.get("SUBTITLE_LLM_MODEL", "gpt-5.4")
DEFAULT_LLM_REFINE_TIMEOUT = env_int("SUBTITLE_LLM_TIMEOUT", 120)
DEFAULT_LLM_REFINE_BATCH_SIZE = env_int("SUBTITLE_LLM_BATCH_SIZE", 24)


@dataclass
class SubtitleBlock:
    index: int
    start: float
    end: float
    text: str


def run_command(args: list[str]) -> str:
    completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{completed.stdout}")
    return completed.stdout


def seconds_to_srt(seconds: float) -> str:
    total_milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_audio(video_path: Path, wav_path: Path) -> None:
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ]
    )


def transcribe_with_whisper_server(audio_path: Path, server_url: str) -> dict:
    with tempfile.NamedTemporaryFile(prefix="subtitle-verbose-", suffix=".json", delete=False) as handle:
        tmp_json_path = Path(handle.name)
    try:
        run_command(
            [
                "curl",
                "-sS",
                "-f",
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
        return json.loads(tmp_json_path.read_text(encoding="utf-8"))
    finally:
        tmp_json_path.unlink(missing_ok=True)


def group_segments(
    segments: list[dict],
    max_block_seconds: float,
    max_block_chars: int,
    max_gap_seconds: float,
) -> list[SubtitleBlock]:
    grouped: list[SubtitleBlock] = []
    current_texts: list[str] = []
    current_start: float | None = None
    current_end: float | None = None

    def flush() -> None:
        nonlocal current_texts, current_start, current_end
        if not current_texts or current_start is None or current_end is None:
            current_texts = []
            current_start = None
            current_end = None
            return
        grouped.append(
            SubtitleBlock(
                index=len(grouped) + 1,
                start=current_start,
                end=current_end,
                text=normalize_spaces(" ".join(current_texts)),
            )
        )
        current_texts = []
        current_start = None
        current_end = None

    for segment in segments:
        text = normalize_spaces(str(segment.get("text", "")))
        if not text:
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        if end <= start + 0.05:
            continue
        if current_start is None:
            current_start = start
            current_end = end
            current_texts = [text]
            continue

        gap = start - current_end
        candidate_text = normalize_spaces(" ".join(current_texts + [text]))
        candidate_duration = end - current_start

        if gap > max_gap_seconds or candidate_duration > max_block_seconds or len(candidate_text) > max_block_chars:
            flush()
            current_start = start
            current_end = end
            current_texts = [text]
            continue

        current_texts.append(text)
        current_end = end

    flush()
    return grouped


def load_glossary(glossary_path: Path | None) -> dict[str, str]:
    glossary: dict[str, str] = {}
    if glossary_path is None:
        return glossary
    payload = json.loads(glossary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("glossary JSON must be an object map of source term to target term")
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("glossary terms must be string to string mappings")
        glossary[key] = value
    return glossary


def protect_glossary_terms(text: str, glossary: dict[str, str]) -> tuple[str, dict[str, str]]:
    protected_text = text
    replacements: dict[str, str] = {}
    for idx, source_term in enumerate(sorted(glossary, key=len, reverse=True)):
        token = f"[[991{idx:03d}991]]"
        pattern = re.compile(re.escape(source_term), re.IGNORECASE)
        if pattern.search(protected_text):
            protected_text = pattern.sub(token, protected_text)
            replacements[token] = glossary[source_term]
    return protected_text, replacements


def restore_glossary_terms(text: str, replacements: dict[str, str]) -> str:
    restored = text
    for token, target_term in replacements.items():
        token_pattern = re.escape(token).replace(r"\[", r"\[\s*").replace(r"\]", r"\s*\]")
        restored = re.sub(token_pattern, target_term, restored)
    return restored


def normalize_lang_code(lang_code: str) -> str:
    normalized = re.split(r"[-_]", lang_code.strip(), maxsplit=1)[0].lower()
    if not normalized:
        raise ValueError("language code must not be empty")
    return normalized


def translate_texts_google(texts: list[str], glossary: dict[str, str], target_lang: str) -> list[str]:
    try:
        from deep_translator import GoogleTranslator
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "missing dependency: deep_translator; install it before running the translation draft workflow"
        ) from exc

    translator = GoogleTranslator(source="auto", target=target_lang)
    translated: list[str] = []

    for source_text in texts:
        if not source_text:
            translated.append("")
            continue

        protected_text, replacements = protect_glossary_terms(source_text, glossary)
        try:
            target_text = translator.translate(protected_text)
        except Exception:
            target_text = protected_text
        restored_text = restore_glossary_terms(target_text, replacements)
        translated.append(normalize_spaces(restored_text))
        time.sleep(0.12)

    return translated


def translate_texts_argos(
    texts: list[str],
    glossary: dict[str, str],
    source_lang: str,
    target_lang: str,
) -> list[str]:
    if source_lang == "auto":
        raise ValueError("argos backend requires an explicit source language, not 'auto'")

    try:
        import argostranslate.translate
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "missing dependency: argostranslate; install it before using the offline argos backend"
        ) from exc

    source_code = normalize_lang_code(source_lang)
    target_code = normalize_lang_code(target_lang)
    installed_languages = argostranslate.translate.get_installed_languages()
    from_lang = next((lang for lang in installed_languages if lang.code == source_code), None)
    to_lang = next((lang for lang in installed_languages if lang.code == target_code), None)

    if from_lang is None or to_lang is None:
        raise ValueError(
            f"argos language package missing: source={source_code if from_lang is None else 'ok'}, "
            f"target={target_code if to_lang is None else 'ok'}"
        )

    translation = from_lang.get_translation(to_lang)
    if translation is None:
        raise ValueError(f"argos translation model not installed for {source_code} -> {target_code}")

    translated: list[str] = []
    for source_text in texts:
        if not source_text:
            translated.append("")
            continue

        protected_text, replacements = protect_glossary_terms(source_text, glossary)
        restored_text = restore_glossary_terms(translation.translate(protected_text), replacements)
        translated.append(normalize_spaces(restored_text))

    return translated


def translate_texts(
    texts: list[str],
    glossary: dict[str, str],
    source_lang: str,
    target_lang: str,
    translator_backend: str,
) -> list[str]:
    if translator_backend == "google":
        return translate_texts_google(texts=texts, glossary=glossary, target_lang=target_lang)
    if translator_backend == "argos":
        return translate_texts_argos(
            texts=texts,
            glossary=glossary,
            source_lang=source_lang,
            target_lang=target_lang,
        )
    raise ValueError(f"unsupported translator backend: {translator_backend}")


def llm_chat_completions_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if not cleaned:
        raise ValueError("LLM refine base URL must not be empty")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def extract_json_array(content: str) -> list[str]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if not cleaned.startswith("["):
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    payload = json.loads(cleaned)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("LLM refine response must be a JSON array of strings")
    return [normalize_spaces(item) for item in payload]


def refine_translation_batch_with_llm(
    source_texts: list[str],
    draft_texts: list[str],
    target_lang: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int,
) -> list[str]:
    if len(source_texts) != len(draft_texts):
        raise ValueError("source and draft translation counts differ")
    if not api_key:
        raise ValueError("LLM refine API key is missing")
    if not model:
        raise ValueError("LLM refine model is missing")

    items = [
        {"index": index, "english": source, "google_draft": draft}
        for index, (source, draft) in enumerate(zip(source_texts, draft_texts), start=1)
    ]
    system_prompt = (
        "You are a professional English-to-Simplified-Chinese subtitle proofreader for "
        "finance, markets, technology, and news interviews. Refine Google Translate drafts "
        "against the English source. Preserve meaning, names, numbers, tickers, company names, "
        "and finance terminology. Keep each subtitle concise and natural for on-screen reading. "
        "Do not add explanations. Return only a JSON array of strings with exactly the same "
        "order and count as the input."
    )
    user_prompt = (
        "Refine these subtitle translations into natural Simplified Chinese. "
        f"Target language: {target_lang}. Input JSON:\n"
        + json.dumps(items, ensure_ascii=False)
    )
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }
    request = urllib.request.Request(
        llm_chat_completions_url(base_url),
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM refine HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM refine request failed: {exc}") from exc

    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM refine response missing choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("LLM refine response missing message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM refine response content is empty")
    refined = extract_json_array(content)
    if len(refined) != len(draft_texts):
        raise ValueError(f"LLM refine returned {len(refined)} items for {len(draft_texts)} inputs")
    return refined


def refine_translations_with_llm(
    source_texts: list[str],
    draft_texts: list[str],
    target_lang: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int,
    batch_size: int,
) -> list[str]:
    if batch_size <= 0:
        raise ValueError("LLM refine batch size must be positive")
    refined: list[str] = []
    for offset in range(0, len(draft_texts), batch_size):
        source_batch = source_texts[offset : offset + batch_size]
        draft_batch = draft_texts[offset : offset + batch_size]
        refined.extend(
            refine_translation_batch_with_llm(
                source_texts=source_batch,
                draft_texts=draft_batch,
                target_lang=target_lang,
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout=timeout,
            )
        )
        time.sleep(0.2)
    return refined


def wrap_chinese_text(text: str, width: int) -> str:
    cleaned = normalize_spaces(text)
    if not cleaned:
        return ""

    chunks = re.split(r"([，。！？；：、“”‘’,.!?;:])", cleaned)
    segments: list[str] = []
    for i in range(0, len(chunks), 2):
        piece = chunks[i]
        punctuation = chunks[i + 1] if i + 1 < len(chunks) else ""
        token = f"{piece}{punctuation}".strip()
        if token:
            segments.append(token)

    lines: list[str] = []
    current = ""
    for segment in segments:
        candidate = f"{current}{segment}"
        if current and len(candidate) > width:
            lines.append(current.strip())
            current = segment
        else:
            current = candidate

    if current.strip():
        lines.append(current.strip())

    if not lines:
        return cleaned
    return "\n".join(lines)


def validate_blocks(blocks: list[SubtitleBlock]) -> None:
    previous_end = -1.0
    for expected_index, block in enumerate(blocks, start=1):
        if block.index != expected_index:
            raise ValueError(f"block index mismatch: expected {expected_index}, got {block.index}")
        if block.start >= block.end:
            raise ValueError(f"invalid timing in block {block.index}")
        if block.start < previous_end - 0.05:
            raise ValueError(f"timing overlap near block {block.index}")
        if not block.text.strip():
            raise ValueError(f"empty text in block {block.index}")
        previous_end = block.end


def render_srt(blocks: Iterable[SubtitleBlock], wrap_width: int) -> str:
    rendered_blocks: list[str] = []
    for block in blocks:
        rendered_blocks.append(
            "\n".join(
                [
                    str(block.index),
                    f"{seconds_to_srt(block.start)} --> {seconds_to_srt(block.end)}",
                    wrap_chinese_text(block.text, wrap_width),
                    "",
                ]
            )
        )
    return "\n".join(rendered_blocks).strip() + "\n"


def render_plain_srt(blocks: Iterable[SubtitleBlock]) -> str:
    rendered_blocks: list[str] = []
    for block in blocks:
        rendered_blocks.append(
            "\n".join(
                [
                    str(block.index),
                    f"{seconds_to_srt(block.start)} --> {seconds_to_srt(block.end)}",
                    normalize_spaces(block.text),
                    "",
                ]
            )
        )
    return "\n".join(rendered_blocks).strip() + "\n"


def language_output_tag(target_lang: str) -> str:
    normalized = re.split(r"[-_]", target_lang.strip(), maxsplit=1)[0].lower()
    if not normalized:
        raise ValueError("target language code must not be empty")
    return normalized


def write_temp_file(content: str, preferred_dir: Path) -> Path:
    temp_dirs = [preferred_dir]
    fallback_dir = Path(tempfile.gettempdir())
    if fallback_dir not in temp_dirs:
        temp_dirs.append(fallback_dir)

    last_error: Exception | None = None
    for temp_dir in temp_dirs:
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=temp_dir) as handle:
                handle.write(content)
                return Path(handle.name)
        except PermissionError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"unable to create temp file for {preferred_dir}")


def write_atomic(target_path: Path, content: str) -> None:
    tmp_path = write_temp_file(content, target_path.parent)
    try:
        try:
            os.replace(tmp_path, target_path)
        except OSError:
            shutil.move(str(tmp_path), str(target_path))
    finally:
        tmp_path.unlink(missing_ok=True)


def write_review_file(review_path: Path, english_blocks: list[SubtitleBlock], chinese_blocks: list[SubtitleBlock]) -> None:
    lines: list[str] = []
    for english_block, chinese_block in zip(english_blocks, chinese_blocks):
        lines.extend(
            [
                f"--- block {english_block.index} {seconds_to_srt(english_block.start)} --> {seconds_to_srt(english_block.end)} ---",
                f"EN: {english_block.text}",
                f"ZH: {chinese_block.text}",
                "",
            ]
        )
    write_atomic(review_path, "\n".join(lines).rstrip() + "\n")


def process_video(
    video_path: Path,
    out_dir: Path,
    server_url: str,
    glossary: dict[str, str],
    source_lang: str,
    target_lang: str,
    translator_backend: str,
    translation_refine: str,
    llm_refine_base_url: str,
    llm_refine_api_key: str,
    llm_refine_model: str,
    llm_refine_timeout: int,
    llm_refine_batch_size: int,
    wrap_width: int,
    verbose_json_override: Path | None,
) -> None:
    stem = video_path.stem
    target_lang_tag = language_output_tag(target_lang)
    out_dir.mkdir(parents=True, exist_ok=True)
    verbose_json_path = out_dir / f"{stem}.verbose.json"
    english_srt_path = out_dir / f"{stem}.en.srt"
    google_chinese_draft_path = out_dir / f"{stem}.zh.google-draft.srt"
    chinese_draft_path = out_dir / f"{stem}.zh.draft.srt"
    chinese_srt_path = video_path.with_name(f"{stem}.{target_lang_tag}.srt")
    review_path = out_dir / f"{stem}.review.txt"

    with tempfile.TemporaryDirectory(prefix="subtitle-local-workflow-") as temp_root:
        temp_root_path = Path(temp_root)
        audio_path = temp_root_path / "input.wav"

        if verbose_json_override is not None:
            payload = json.loads(verbose_json_override.read_text(encoding="utf-8"))
        else:
            extract_audio(video_path, audio_path)
            payload = transcribe_with_whisper_server(audio_path, server_url)

        write_atomic(verbose_json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

        segments = payload.get("segments")
        if not isinstance(segments, list):
            raise ValueError("verbose JSON does not contain a segment list")

        english_blocks = group_segments(
            segments=segments,
            max_block_seconds=DEFAULT_MAX_BLOCK_SECONDS,
            max_block_chars=DEFAULT_MAX_BLOCK_CHARS,
            max_gap_seconds=DEFAULT_MAX_GAP_SECONDS,
        )
        validate_blocks(english_blocks)
        write_atomic(english_srt_path, render_plain_srt(english_blocks))

        source_texts = [block.text for block in english_blocks]
        translated_texts = translate_texts(
            texts=source_texts,
            glossary=glossary,
            source_lang=source_lang,
            target_lang=target_lang,
            translator_backend=translator_backend,
        )
        google_chinese_blocks = [
            SubtitleBlock(index=block.index, start=block.start, end=block.end, text=translated_texts[idx - 1])
            for idx, block in enumerate(english_blocks, start=1)
        ]
        validate_blocks(google_chinese_blocks)
        write_atomic(google_chinese_draft_path, render_srt(google_chinese_blocks, wrap_width))

        if translation_refine != "off":
            missing = []
            if not llm_refine_base_url:
                missing.append("--llm-refine-base-url or SUBTITLE_LLM_BASE_URL")
            if not llm_refine_api_key:
                missing.append("--llm-refine-api-key or SUBTITLE_LLM_API_KEY")
            if not llm_refine_model:
                missing.append("--llm-refine-model or SUBTITLE_LLM_MODEL")
            if missing:
                message = "LLM translation refinement is not configured: missing " + ", ".join(missing)
                if translation_refine == "require":
                    raise RuntimeError(message)
                print(f"[warn] {message}; using raw machine translation", file=sys.stderr)
            else:
                try:
                    translated_texts = refine_translations_with_llm(
                        source_texts=source_texts,
                        draft_texts=translated_texts,
                        target_lang=target_lang,
                        base_url=llm_refine_base_url,
                        api_key=llm_refine_api_key,
                        model=llm_refine_model,
                        timeout=llm_refine_timeout,
                        batch_size=llm_refine_batch_size,
                    )
                    print(f"[info] LLM translation refinement used: {llm_refine_model}")
                except Exception as exc:
                    if translation_refine == "require":
                        raise RuntimeError(f"LLM translation refinement failed: {exc}") from exc
                    print(f"[warn] LLM translation refinement failed: {exc}; using raw machine translation", file=sys.stderr)

        chinese_blocks = [
            SubtitleBlock(index=block.index, start=block.start, end=block.end, text=translated_texts[idx - 1])
            for idx, block in enumerate(english_blocks, start=1)
        ]
        validate_blocks(chinese_blocks)
        rendered_chinese_srt = render_srt(chinese_blocks, wrap_width)
        write_atomic(chinese_draft_path, rendered_chinese_srt)
        write_atomic(chinese_srt_path, rendered_chinese_srt)
        write_review_file(review_path, english_blocks, chinese_blocks)

        print(f"[done] input={video_path}")
        print(f"       verbose_json={verbose_json_path}")
        print(f"       english_srt={english_srt_path}")
        print(f"       google_chinese_draft_srt={google_chinese_draft_path}")
        print(f"       chinese_draft_srt={chinese_draft_path}")
        print(f"       chinese_srt={chinese_srt_path}")
        print(f"       review={review_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local subtitle pipeline: whisper.cpp transcription -> translated draft SRT")
    parser.add_argument("inputs", nargs="+", type=Path, help="Video or audio files to process")
    parser.add_argument("--out-dir", type=Path, default=None, help="Directory to store generated files")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="whisper.cpp server URL")
    parser.add_argument("--glossary", type=Path, default=None, help="Optional JSON glossary file")
    parser.add_argument("--source-lang", default="auto", help="Translation source language code")
    parser.add_argument("--target-lang", default="zh-CN", help="Translation target language code")
    parser.add_argument(
        "--translator-backend",
        choices=["google", "argos"],
        default=DEFAULT_TRANSLATOR_BACKEND,
        help="Translation backend. Default keeps the higher-quality Google route; Argos is offline but lower quality.",
    )
    parser.add_argument(
        "--translation-refine",
        choices=["off", "auto", "require"],
        default=DEFAULT_TRANSLATION_REFINE,
        help="Optional LLM proofread pass after machine translation. require stops if refinement fails.",
    )
    parser.add_argument(
        "--llm-refine-base-url",
        default=DEFAULT_LLM_REFINE_BASE_URL,
        help="OpenAI-compatible base URL for LLM translation refinement.",
    )
    parser.add_argument(
        "--llm-refine-api-key",
        default=DEFAULT_LLM_REFINE_API_KEY,
        help="API key for LLM translation refinement. Prefer SUBTITLE_LLM_API_KEY.",
    )
    parser.add_argument("--llm-refine-model", default=DEFAULT_LLM_REFINE_MODEL, help="LLM model for translation refinement")
    parser.add_argument("--llm-refine-timeout", type=int, default=DEFAULT_LLM_REFINE_TIMEOUT, help="LLM refine request timeout seconds")
    parser.add_argument("--llm-refine-batch-size", type=int, default=DEFAULT_LLM_REFINE_BATCH_SIZE, help="Subtitle blocks per LLM refine request")
    parser.add_argument("--wrap-width", type=int, default=DEFAULT_WRAP_WIDTH, help="Approximate CJK line wrap width")
    parser.add_argument(
        "--verbose-json",
        type=Path,
        default=None,
        help="Optional existing whisper verbose JSON file. Only valid for a single input.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.verbose_json is not None and len(args.inputs) != 1:
        raise ValueError("--verbose-json can only be used with exactly one input")

    glossary = load_glossary(args.glossary)
    for idx, input_path in enumerate(args.inputs):
        if not input_path.exists():
            raise FileNotFoundError(f"missing input: {input_path}")
        output_dir = args.out_dir if args.out_dir is not None else input_path.parent
        verbose_override = args.verbose_json if idx == 0 else None
        process_video(
            video_path=input_path,
            out_dir=output_dir,
            server_url=args.server_url,
            glossary=glossary,
            source_lang=args.source_lang,
            target_lang=args.target_lang,
            translator_backend=args.translator_backend,
            translation_refine=args.translation_refine,
            llm_refine_base_url=args.llm_refine_base_url,
            llm_refine_api_key=args.llm_refine_api_key,
            llm_refine_model=args.llm_refine_model,
            llm_refine_timeout=args.llm_refine_timeout,
            llm_refine_batch_size=args.llm_refine_batch_size,
            wrap_width=args.wrap_width,
            verbose_json_override=verbose_override,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise
