#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from subtitle_pipeline_local import SubtitleBlock, validate_blocks, write_atomic
from video_link_bilingual_burn import (
    WorkflowError,
    apply_subtitle_profile_defaults,
    burn_subtitles,
    extract_verification_frames,
    pick_ass_sizes,
    probe_video_height,
    render_bilingual_srt,
    render_english_top_ass,
    representative_frame_times,
    resolve_media_tools,
)


def parse_srt_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
    if not match:
        raise ValueError(f"invalid SRT timestamp: {value}")
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_srt(path: Path) -> list[SubtitleBlock]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        raise ValueError(f"SRT is empty: {path}")

    blocks: list[SubtitleBlock] = []
    for chunk in re.split(r"\n{2,}", raw):
        lines = [line.rstrip() for line in chunk.splitlines() if line.strip()]
        if len(lines) < 3:
            raise ValueError(f"invalid SRT block in {path}: {chunk!r}")
        try:
            index = int(lines[0].strip())
        except ValueError as exc:
            raise ValueError(f"invalid SRT index in {path}: {lines[0]!r}") from exc
        time_match = re.fullmatch(r"(.+?)\s+-->\s+(.+)", lines[1].strip())
        if time_match is None:
            raise ValueError(f"invalid SRT timing line in {path}: {lines[1]!r}")
        start = parse_srt_timestamp(time_match.group(1))
        end = parse_srt_timestamp(time_match.group(2))
        text = "\n".join(lines[2:]).strip()
        blocks.append(SubtitleBlock(index=index, start=start, end=end, text=text))

    validate_blocks(blocks)
    return blocks


def path_from_manifest(manifest: dict[str, Any], key: str) -> Path:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"manifest missing path field: {key}")
    return Path(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reburn a bilingual subtitle run after agent-side Chinese SRT refinement."
    )
    parser.add_argument("run_dir", type=Path, help="Run directory containing manifest.json")
    parser.add_argument("--zh-srt", type=Path, default=None, help="Refined Chinese SRT. Default: manifest chinese_draft_srt")
    parser.add_argument("--english-srt", type=Path, default=None, help="English SRT override")
    parser.add_argument("--video", type=Path, default=None, help="Source video override")
    parser.add_argument("--output", type=Path, default=None, help="Output MP4 path override")
    parser.add_argument("--ffmpeg-bin", default=None, help="Optional ffmpeg binary override. Must support libass ass filter.")
    parser.add_argument("--ffprobe-bin", default=None, help="Optional ffprobe binary override")
    parser.add_argument(
        "--subtitle-profile",
        choices=["news-box", "news-safe", "standard"],
        default="news-box",
        help="Subtitle layout profile.",
    )
    parser.add_argument("--font", default="PingFang SC", help="ASS font name")
    parser.add_argument("--cn-size", type=int, default=None, help="Chinese ASS font size override")
    parser.add_argument("--en-size", type=int, default=None, help="English ASS font size override")
    parser.add_argument("--marginv", type=int, default=None, help="ASS bottom margin")
    parser.add_argument("--ass-outline", type=float, default=None, help="ASS outline thickness")
    parser.add_argument("--ass-shadow", type=float, default=None, help="ASS shadow thickness")
    parser.add_argument("--ass-bold", type=int, choices=[0, 1], default=None, help="ASS bold flag")
    parser.add_argument("--ass-border-style", type=int, choices=[1, 3], default=None, help="ASS border style")
    parser.add_argument("--ass-back-color", default=None, help="ASS background color")
    parser.add_argument("--en-color", default=None, help="English subtitle color")
    parser.add_argument("--zh-color", default=None, help="Chinese subtitle color")
    parser.add_argument("--ass-en-wrap", type=int, default=None, help="Maximum English characters per ASS line")
    parser.add_argument("--ass-zh-wrap", type=int, default=None, help="Maximum Chinese characters per ASS line")
    parser.add_argument(
        "--no-fix-black-first-frame",
        dest="fix_black_first_frame",
        action="store_false",
        help="Disable replacing a leading black video frame with the first visible frame.",
    )
    parser.set_defaults(fix_black_first_frame=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    apply_subtitle_profile_defaults(args)

    run_dir = args.run_dir.expanduser().resolve()
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise WorkflowError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise WorkflowError(f"manifest is not an object: {manifest_path}")

    video_path = args.video or path_from_manifest(manifest, "downloaded_video")
    english_srt_path = args.english_srt or path_from_manifest(manifest, "english_srt")
    chinese_srt_path = args.zh_srt or path_from_manifest(manifest, "chinese_draft_srt")
    if not video_path.exists():
        raise WorkflowError(f"video not found: {video_path}")
    if not english_srt_path.exists():
        raise WorkflowError(f"English SRT not found: {english_srt_path}")
    if not chinese_srt_path.exists():
        raise WorkflowError(f"Chinese SRT not found: {chinese_srt_path}")

    stem = Path(str(manifest.get("downloaded_video", video_path))).stem
    output_path = args.output or (run_dir / f"{stem}.en-top.zh-bottom.agent-refined.burned.mp4")
    bilingual_srt_path = run_dir / f"{stem}.en-top.zh-bottom.agent-refined.srt"
    ass_path = run_dir / f"{stem}.en-top.zh-bottom.agent-refined.ass"

    english_blocks = parse_srt(english_srt_path)
    refined_chinese_blocks = parse_srt(chinese_srt_path)
    if len(english_blocks) != len(refined_chinese_blocks):
        raise WorkflowError(
            f"subtitle block count mismatch: English={len(english_blocks)} Chinese={len(refined_chinese_blocks)}"
        )
    chinese_blocks = [
        SubtitleBlock(index=english.index, start=english.start, end=english.end, text=chinese.text)
        for english, chinese in zip(english_blocks, refined_chinese_blocks)
    ]
    validate_blocks(chinese_blocks)

    ffmpeg_bin, ffprobe_bin, ffmpeg_detection_notes = resolve_media_tools(args.ffmpeg_bin, args.ffprobe_bin)
    height = probe_video_height(video_path, ffprobe_bin)
    cn_size, en_size = pick_ass_sizes(height, args.cn_size, args.en_size)

    write_atomic(bilingual_srt_path, render_bilingual_srt(english_blocks, chinese_blocks))
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
    burn_command, leading_black_end = burn_subtitles(
        video_path,
        ass_path,
        output_path,
        ffmpeg_bin,
        fix_black_first_frame=args.fix_black_first_frame,
    )
    frames = extract_verification_frames(
        video_path=output_path,
        frame_dir=run_dir / "frames-agent-refined",
        times=representative_frame_times(english_blocks),
        ffmpeg_bin=ffmpeg_bin,
    )

    manifest.update(
        {
            "agent_refined_at": datetime.now().isoformat(timespec="seconds"),
            "agent_refined_chinese_srt": str(chinese_srt_path),
            "agent_refined_bilingual_srt": str(bilingual_srt_path),
            "agent_refined_ass": str(ass_path),
            "agent_refined_burned_video": str(output_path),
            "agent_refined_verification_frames": [str(path) for path in frames],
            "agent_refined_burn_command": burn_command,
            "agent_refined_leading_black_end": leading_black_end,
            "ffmpeg_bin": ffmpeg_bin,
            "ffprobe_bin": ffprobe_bin,
            "ffmpeg_detection_notes": ffmpeg_detection_notes,
        }
    )
    write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print("[done] burned_video=" + str(output_path))
    print("[done] bilingual_srt=" + str(bilingual_srt_path))
    print("[done] bilingual_ass=" + str(ass_path))
    print("[done] manifest=" + str(manifest_path))
    for frame in frames:
        print("[done] verification_frame=" + str(frame))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
