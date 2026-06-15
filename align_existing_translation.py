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
from pathlib import Path

from subtitle_pipeline_local import (
    DEFAULT_SERVER_URL,
    DEFAULT_WRAP_WIDTH,
    SubtitleBlock,
    extract_audio,
    group_segments,
    render_srt,
    transcribe_with_whisper_server,
    validate_blocks,
    write_atomic,
)


DEFAULT_ALIGNER = "auto"
LOCAL_SUBALIGNER_VENV = Path(__file__).resolve().parent / ".venv-subaligner" / "bin" / "python"
LOCAL_SUBALIGNER_READY = Path(__file__).resolve().parent / ".subaligner-ready"


def normalize_translation_text(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    collapsed = re.sub(r"\s*([，。！？；：、,.!?;:])\s*", r"\1", collapsed)
    collapsed = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", collapsed)
    collapsed = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z0-9])", "", collapsed)
    collapsed = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[\u4e00-\u9fff])", "", collapsed)
    return collapsed.strip()


def split_translation_units(text: str) -> list[str]:
    normalized = normalize_translation_text(text)
    if not normalized:
        return []

    raw_units = re.findall(r"[^，。！？；：、,.!?;:]+[，。！？；：、,.!?;:”’」』）)]*", normalized)
    units: list[str] = []

    for raw_unit in raw_units:
        unit = raw_unit.strip()
        if not unit:
            continue
        if units and len(re.sub(r"[，。！？；：、,.!?;:]", "", unit)) <= 4:
            units[-1] = f"{units[-1]}{unit}"
            continue
        units.append(unit)

    if not units:
        return [normalized]
    return units


def render_script_units(units: list[str]) -> str:
    cleaned = [unit.strip() for unit in units if unit.strip()]
    if not cleaned:
        return ""
    return "\n\n".join(cleaned) + "\n"


def weight_for_text(text: str) -> int:
    stripped = re.sub(r"\s+", "", text)
    alnum_weight = len(re.findall(r"[A-Za-z0-9]", stripped))
    cjk_weight = len(re.findall(r"[\u3400-\u9fff]", stripped))
    punctuation_weight = len(re.findall(r"[，。！？；：、,.!?;:]", stripped))
    return max(1, cjk_weight * 2 + alnum_weight + punctuation_weight)


def run_command(args: list[str], env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{completed.stdout}")
    return completed.stdout


def resolve_subaligner_command() -> list[str]:
    env_python = os.environ.get("SUBALIGNER_PYTHON_BIN")
    if env_python:
        env_python_path = Path(env_python).expanduser()
        if env_python_path.exists():
            return [str(env_python_path), "-m", "subaligner"]

    if LOCAL_SUBALIGNER_VENV.exists() and LOCAL_SUBALIGNER_READY.exists():
        return [str(LOCAL_SUBALIGNER_VENV), "-m", "subaligner"]

    subaligner_cli = shutil.which("subaligner")
    if subaligner_cli is not None:
        return [subaligner_cli]

    raise RuntimeError(
        "subaligner is not provisioned locally. Run bootstrap_subaligner_env.sh first, "
        "or set SUBALIGNER_PYTHON_BIN to a Python with subaligner installed."
    )


def align_with_subaligner(
    video_path: Path,
    units: list[str],
    output_path: Path,
) -> str:
    script_text = render_script_units(units)
    if not script_text:
        raise ValueError("translated text is empty after segmentation")

    with tempfile.TemporaryDirectory(prefix="subaligner-script-") as temp_root:
        temp_root_path = Path(temp_root)
        script_path = temp_root_path / f"{video_path.stem}.script.txt"
        script_path.write_text(script_text, encoding="utf-8")

        env = os.environ.copy()
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is not None:
            env.setdefault("FFMPEG_PATH", ffmpeg_path)

        command = [
            *resolve_subaligner_command(),
            "-m",
            "script",
            "-v",
            str(video_path),
            "-s",
            str(script_path),
            "-o",
            str(output_path),
        ]
        return run_command(command, env=env)


def choose_split_index(
    unit_weights: list[int],
    current_index: int,
    current_weight: int,
    target_weight: float,
    remaining_blocks: int,
) -> tuple[int, int]:
    min_end = current_index + 1
    max_end = len(unit_weights) - remaining_blocks
    end = min_end
    cumulative = current_weight + unit_weights[current_index]

    while end < max_end and cumulative < target_weight:
        cumulative += unit_weights[end]
        end += 1

    if end > min_end:
        with_last = abs(cumulative - target_weight)
        without_last = abs((cumulative - unit_weights[end - 1]) - target_weight)
        if without_last <= with_last:
            cumulative -= unit_weights[end - 1]
            end -= 1

    return end, cumulative


def align_units_to_blocks(blocks: list[SubtitleBlock], units: list[str]) -> list[SubtitleBlock]:
    if not blocks:
        raise ValueError("no subtitle timing blocks available")
    if not units:
        raise ValueError("translated text is empty after normalization")

    block_weights = [weight_for_text(block.text) for block in blocks]
    unit_weights = [weight_for_text(unit) for unit in units]
    total_block_weight = sum(block_weights)
    total_unit_weight = sum(unit_weights)

    if len(units) < len(blocks):
        raise ValueError(
            f"translated units too few for timing blocks: units={len(units)}, blocks={len(blocks)}; "
            "please provide a more granular translation or adjust the source segmentation"
        )

    aligned_blocks: list[SubtitleBlock] = []
    current_index = 0
    current_weight = 0
    cumulative_block_weight = 0

    for block_index, block in enumerate(blocks):
        cumulative_block_weight += block_weights[block_index]

        if block_index == len(blocks) - 1:
            end = len(units)
            current_weight = total_unit_weight
        else:
            remaining_blocks = len(blocks) - block_index - 1
            target_weight = total_unit_weight * (cumulative_block_weight / total_block_weight)
            end, current_weight = choose_split_index(
                unit_weights=unit_weights,
                current_index=current_index,
                current_weight=current_weight,
                target_weight=target_weight,
                remaining_blocks=remaining_blocks,
            )

        translated_text = "".join(units[current_index:end]).strip()
        if not translated_text:
            raise ValueError(f"empty aligned text near block {block.index}")

        aligned_blocks.append(
            SubtitleBlock(index=block.index, start=block.start, end=block.end, text=translated_text)
        )
        current_index = end

    if current_index != len(units):
        raise ValueError("not all translated units were assigned to subtitle blocks")
    return aligned_blocks


def generate_timing_blocks(video_path: Path, server_url: str, verbose_json_path: Path | None) -> tuple[dict, list[SubtitleBlock]]:
    if verbose_json_path is not None:
        payload = json.loads(verbose_json_path.read_text(encoding="utf-8"))
    else:
        with tempfile.TemporaryDirectory(prefix="align-existing-translation-") as temp_root:
            audio_path = Path(temp_root) / f"{video_path.stem}.wav"
            extract_audio(video_path, audio_path)
            payload = transcribe_with_whisper_server(audio_path, server_url)

    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise ValueError("verbose JSON does not contain a segment list")

    english_blocks = group_segments(
        segments=segments,
        max_block_seconds=7.0,
        max_block_chars=84,
        max_gap_seconds=0.75,
    )
    validate_blocks(english_blocks)
    return payload, english_blocks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align an existing translated TXT file onto subtitle timings, preferring Subaligner and falling back to the local heuristic workflow."
    )
    parser.add_argument("video", type=Path, help="Video or audio file to align against")
    parser.add_argument("translation_txt", type=Path, help="Existing translated text file")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output SRT path. Defaults to <video>.zh.aligned.srt",
    )
    parser.add_argument(
        "--verbose-json",
        type=Path,
        default=None,
        help="Optional existing whisper verbose JSON file to reuse instead of transcribing again",
    )
    parser.add_argument(
        "--save-verbose-json",
        type=Path,
        default=None,
        help="Optional path to write the generated whisper verbose JSON",
    )
    parser.add_argument(
        "--aligner",
        choices=["auto", "subaligner", "heuristic"],
        default=DEFAULT_ALIGNER,
        help="Alignment engine. 'auto' tries Subaligner first and falls back to the local heuristic aligner.",
    )
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="whisper.cpp server URL")
    parser.add_argument("--wrap-width", type=int, default=DEFAULT_WRAP_WIDTH, help="Approximate CJK line wrap width")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.video.exists():
        raise FileNotFoundError(f"missing video: {args.video}")
    if not args.translation_txt.exists():
        raise FileNotFoundError(f"missing translation text: {args.translation_txt}")
    if args.verbose_json is not None and not args.verbose_json.exists():
        raise FileNotFoundError(f"missing verbose JSON: {args.verbose_json}")

    output_path = args.output or args.video.with_name(f"{args.video.stem}.zh.aligned.srt")
    translation_text = args.translation_txt.read_text(encoding="utf-8")
    translation_units = split_translation_units(translation_text)

    if args.aligner in ("auto", "subaligner"):
        try:
            subaligner_output = align_with_subaligner(video_path=args.video, units=translation_units, output_path=output_path)
            print(f"[done] output={output_path}")
            print("       engine=subaligner")
            print(f"       translation_units={len(translation_units)}")
            if subaligner_output.strip():
                print("       subaligner_log=available")
            if args.save_verbose_json is not None:
                print("       note=save_verbose_json skipped because subaligner path does not use whisper verbose JSON")
            return 0
        except Exception as exc:
            if args.aligner == "subaligner":
                raise
            print(f"[warn] subaligner failed, falling back to heuristic alignment: {exc}", file=sys.stderr)

    payload, english_blocks = generate_timing_blocks(
        video_path=args.video,
        server_url=args.server_url,
        verbose_json_path=args.verbose_json,
    )

    if args.save_verbose_json is not None:
        write_atomic(args.save_verbose_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    aligned_blocks = align_units_to_blocks(english_blocks, translation_units)
    validate_blocks(aligned_blocks)

    rendered = render_srt(aligned_blocks, args.wrap_width)
    write_atomic(output_path, rendered)

    print(f"[done] output={output_path}")
    print("       engine=heuristic")
    print(f"       timing_blocks={len(english_blocks)}")
    print(f"       translation_units={len(translation_units)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise
