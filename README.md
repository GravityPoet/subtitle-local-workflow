# 🎬 Subtitle Local Workflow

<div align="center">
  <p><strong>🔥 The Ultimate Automated Bilingual Subtitle & Video Hardsubbing Pipeline for Creators</strong></p>
  <p>🚀 Powered entirely by local high-accuracy ASR engines (Parakeet / Whisper). 100% offline-friendly, privacy-secure, and hassle-free.</p>
</div>

<p align="center">
  🌐 <a href="./README_zh.md">简体中文</a>
</p>

---

## ✨ Core Values & Highlights (Why You Need This)

*   **⚡️ One-Command Automation**: Pass in a video URL (or local media path), sit back, and get a beautifully styled bilingual hardsubbed MP4 video. Perfect for content creators, localizers, and cross-border video platforms.
*   **🎯 Heavy-Duty ASR Pipeline**: Features a resilient, multi-tiered transcription fallback hierarchy: `Parakeet TDT 0.6B v2 ➔ MLX Whisper ➔ faster-whisper ➔ whisper.cpp server`. It smoothly downgrades to find the best local transcriber for your hardware, preventing processing interruptions.
*   **🎨 Broadcast-Quality Typography**: Includes premium pre-configured styling templates (such as `news-box`). Subtitles feature clean, high-contrast layouts (warm yellow English, crisp white Chinese) with semi-transparent background blocks, and first-frame black screen prevention for perfect social media thumbnails.
*   **📦 Comprehensive Asset Outputs**: Retains all intermediate assets. In addition to the final video, you get the raw video, original bilingual SRT, ASS files, proofread-ready TXT transcripts, and keyframe validation snapshots for easy manual adjustments.
*   **🔒 Local-First & Privacy-Secure**: Supports Argos Translate for 100% offline machine translation. Audio processing runs entirely locally on Apple Silicon or local GPU, securing your intellectual property.

## 📥 Unified Asset Outputs (What You Get in One Command)

Running a single command automatically generates:
1. 📥 Downloaded original high-definition video
2. 🇺🇸 Source English SRT subtitle file
3. 🇨🇳 Translated Chinese SRT subtitle file
4. 🎭 **Bilingual SRT subtitle file (standard top-English/bottom-Chinese layout)**
5. 🎬 Advanced ASS typesetting subtitle file used for rendering
6. 📺 **Final Hardsubbed Video: Completed MP4 with burned-in bilingual subtitles**
7. 📝 Plaintext transcript (.txt) for quick proofreading
8. 🖼 Keyframe screenshots validating subtitle alignment and rendering
9. ⚙️ `manifest.json` processing metadata manifest

---

## 🛠 Quick Start

### 1. Prerequisites
You need macOS or Linux with the following dependencies installed:
- Python 3.11
- `uv` (The next-generation ultra-fast Python package manager)
- `ffmpeg` (compiled with `libass` support)

**macOS Installation via Homebrew:**
```bash
brew install libass ffmpeg-full
```

### 2. 🚀 One-Command Execution

**Process an online video URL:**
```bash
./burn_bilingual_link.sh "https://example.com/video-url"
```

**Process a local video file:**
```bash
./burn_bilingual_link.sh "/absolute/path/to/video.mp4"
```

🎉 The processed assets will be saved to your default output directory: `$HOME/Downloads/视频字幕输出`.

**Customize output directory:**
```bash
SUBTITLE_OUTPUT_ROOT="/absolute/path/to/output" ./burn_bilingual_link.sh "https://example.com/video-url"
```

---

## 🔧 Advanced Features

### 🎨 Custom Subtitle Profiles
Switch between pre-configured subtitle styles to suit different video hosting platforms:
```bash
# Default: News/interview style with a semi-transparent background box
./burn_bilingual_link.sh "URL" --subtitle-profile news-box

# Standard: Regular bilingual text without background boxes
./burn_bilingual_link.sh "URL" --subtitle-profile standard

# Safe Area: Optimized positioning to avoid UI blockages on TikTok/Shorts
./burn_bilingual_link.sh "URL" --subtitle-profile news-safe

# Override font sizes and background colors manually
./burn_bilingual_link.sh "URL" --cn-size 50 --en-size 44 --ass-back-color '&H80000000&'
```

### 🏎️ Choose ASR Engines and Speed Profiles
Force specific transcription backends depending on your local hardware:
```bash
# Ultra-fast and accurate (Recommended for Apple Silicon)
./burn_bilingual_link.sh "URL" --transcriber parakeet-v2  

# Balanced and lightweight (using MLX backend)
./burn_bilingual_link.sh "URL" --transcriber mlx          

# Skip heavy transformers and run a fast pipeline
./burn_bilingual_link.sh "URL" --quality fast             
```
*Note: To keep your global environment clean, model files are cached locally in `$HOME/Tools/Local-LLM` (override via `SUBTITLE_MODEL_CACHE_ROOT` env).*

### 🌍 100% Offline Translation
By default, the pipeline uses Google Translate. For fully offline environments, use `argostranslate`:
```bash
uv run --python 3.11 --with argostranslate \
  python subtitle_pipeline_local.py "/absolute/path/to/video.mp4" \
  --source-lang en \
  --target-lang zh-CN \
  --translator-backend argos
```

### ✏️ Aligning Existing Translations (For Hand-crafted Subtitles)
If you already have a translated transcript (.txt) and just want to automatically align and generate timecodes matching the audio:
```bash
./bootstrap_subaligner_env.sh
python3 align_existing_translation.py "/path/to/video.mp4" "/path/to/translated.txt"
```
*(Automatically attempts Subaligner alignment, falling back to a local heuristic alignment algorithm if needed).*

### 🤖 Subtitles-Only Mode (Skip Video Rendering)
To generate subtitle files without burning them into the video:
```bash
uv run --python 3.11 --with deep-translator \
  python subtitle_pipeline_local.py "/absolute/path/to/video.mp4" \
  --glossary ./glossary.example.json
```

---

## 👨‍💻 Agent SOP
For AI coding agents integrating with this repository, we have prepared a dedicated integration guide. See: [`docs/agent-sop.md`](docs/agent-sop.md)

## 🤝 Support & Sponsorship

**Why Sponsor Subtitle Local Workflow?**

**Subtitle Local Workflow** is built on a simple promise: complete privacy, total tool control, and zero recurring fees. Keeping this project 100% local, free, and open-source requires continuous dedication, and your support directly fuels our journey:
*   **Save on Subscription Fees**: Instead of paying SaaS platforms per minute of transcription or subscribing to expensive monthly plans, this app utilizes your local GPU/CPU. We help content creators and developers save hundreds of dollars annually.
*   **Ongoing Maintenance & Testing Effort**: To provide a seamless "just unzip and run" experience, we spend significant time and effort compiling multi-architecture sidecars, adapting to different OS updates, and conducting real-device compatibility testing.
*   **Backing the Future of Offline AI**: Your donations directly support the research and implementation of next-gen offline local LLM integrations, enhanced VAD algorithms, and keeping this app free of trackers and ads.

If this app has saved your time, protected your data, or simplified your workflow, please consider:
*   🌟 Giving us a **Star** (It really helps boost our visibility!).
*   ☕ **Buying us a coffee** to support our continuous time and effort spent on maintenance and testing (please mention your GitHub account).

| PayPal | WeChat Sponsor |
| :---: | :---: |
| <img src="./docs/sponsors/paypal.jpg" width="220" alt="PayPal" /> | <img src="./docs/sponsors/wechat_pay.jpg" width="220" alt="WeChat Sponsor" /> |

---

## 📝 License
This project is open-source. For more details, see the [LICENSE](LICENSE) file.

