# 🎬 Subtitle Local Workflow | 双语字幕生成工作流

<div align="center">
  <p><strong>🔥 视频创作者的终极生产力工具！一键搞定视频下载、语音识别、双语翻译与硬字幕压制。</strong></p>
  <p>🚀 完全基于本地高精度 ASR 引擎（Parakeet / Whisper），隐私安全、告别繁琐繁重的后期处理！</p>
</div>

---

## ✨ 核心价值与亮点 (Why You Need This)

*   **⚡️ 极简一键全自动**：输入一段视频 URL（或本地文件），稍等片刻，直接输出带有中英双语硬字幕的 MP4 视频，自媒体搬运与本土化神器。
*   **🎯 极致的本地语音识别准确率**：独家配置高可用 ASR 梯队：`Parakeet TDT 0.6B v2 -> MLX Whisper -> faster-whisper -> whisper.cpp server`。即使复杂场景也能自动平滑降级，保证输出不中断。
*   **🎨 广播级专业字幕排版**：内置 `news-box` 等高档次样式模板，完美契合新闻、访谈类视频。英语高亮暖黄、中文纯白，自带半透明黑底遮罩，专设「第一帧防黑屏修复」（完美适配社交平台缩略图封面抓取）。
*   **📦 丰富的产物全保留**：除了最终视频，自动生成源视频、双语 SRT、压制专用 ASS、Review 文本、校验帧，全方位满足二次精修需求。
*   **🔒 数据隐私与离线优先**：支持本地 Argos 翻译，所有音频转录均在本地 Apple Silicon / GPU 算力上运行，保护您的创意和隐私数据安全。

## 📥 大满贯输出 (What You Get in One Command)

只敲一行命令，你将同时获得：
1. 📥 下载的原始高清视频
2. 🇺🇸 纯英文 SRT 原文字幕
3. 🇨🇳 中文翻译初稿 SRT 字幕
4. 🎭 **双语 SRT 字幕 (经典上英下中排版)**
5. 🎬 压制专用高阶 ASS 字幕文件
6. 📺 **最终成品：已压制双语硬字幕的 MP4 视频**
7. 📝 供校对复核的 txt 文本文件
8. 🖼 用于验收字幕效果的关键视频截图
9. ⚙️ `manifest.json` 处理元数据清单

---

## 🛠 快速开始 (Quick Start)

### 1. 环境准备
你需要 macOS 或 Linux 系统，并安装好以下依赖：
- Python 3.11
- `uv` (新一代极致快速的 Python 包管理器)
- `ffmpeg` (必须带有 `libass` 支持)

**macOS 用户一键安装：**
```bash
brew install libass ffmpeg-full
```

### 2. 🚀 一键召唤魔法

**处理网络视频链接 (URL)：**
```bash
./burn_bilingual_link.sh "https://example.com/video-url"
```

**处理本地视频文件：**
```bash
./burn_bilingual_link.sh "/absolute/path/to/video.mp4"
```

🎉 就是这么简单粗暴！默认输出目录将保存在：`$HOME/Downloads/视频字幕输出`。

**自定义输出路径：**
```bash
SUBTITLE_OUTPUT_ROOT="/absolute/path/to/output" ./burn_bilingual_link.sh "https://example.com/video-url"
```

---

## 🔧 高阶玩法 (Advanced Usage)

### 🎨 切换字幕排版 (Subtitle Profiles)
预设了多种字幕样式，一个参数随意切换，满足多平台发版要求：
```bash
# 默认：新闻访谈类，带黑底框
./burn_bilingual_link.sh "URL" --subtitle-profile news-box

# 标准：无底框常规双语
./burn_bilingual_link.sh "URL" --subtitle-profile standard

# 安全区：避开短视频平台 UI 遮挡
./burn_bilingual_link.sh "URL" --subtitle-profile news-safe

# 自定义字体大小与背景色
./burn_bilingual_link.sh "URL" --cn-size 50 --en-size 44 --ass-back-color '&H80000000&'
```

### 🏎️ 定制 ASR 引擎与速度模式
你可以根据自己的硬件配置，强制选择特定的转录引擎：
```bash
# 极速且精准 (Apple Silicon 首选)
./burn_bilingual_link.sh "URL" --transcriber parakeet-v2  

# 均衡稳定
./burn_bilingual_link.sh "URL" --transcriber mlx          

# 跳过 Parakeet，直接使用轻量化流程
./burn_bilingual_link.sh "URL" --quality fast             
```
*注：为保持系统环境干净整洁，模型文件统一沙盒缓存在 `$HOME/Tools/Local-LLM`（可通过 `SUBTITLE_MODEL_CACHE_ROOT` 环境变量更改）。*

### 🧩 SmartSub 接入 Parakeet v2
SmartSub 原生只识别 Whisper / whisper.cpp。若想让 SmartSub 的「本地 Whisper 命令」实际调用 Parakeet v2，可以使用本仓库提供的 Whisper-compatible bridge：

```bash
chmod +x smartsub_parakeet_whisper.sh smartsub_parakeet_whisper.py
ln -sf "$PWD/smartsub_parakeet_whisper.sh" /opt/homebrew/bin/whisper
```

然后在 SmartSub 设置里开启「使用本地 Whisper 命令」，命令填：

```bash
/absolute/path/to/subtitle-local-workflow/smartsub_parakeet_whisper.sh "${audioFile}" --model ${whisperModel} --output_format srt --output_dir "${outputDir}" --language ${sourceLanguage}
```

这个桥只支持英文识别和 SRT 输出，模型仍会缓存到 `$HOME/Tools/Local-LLM`。

### 🌍 纯本地翻译支持 (Offline Translation)
默认使用 Google 翻译引擎。如需体验完全离线的本地翻译体验，可以使用 `argostranslate`：
```bash
uv run --python 3.11 --with argostranslate \
  python subtitle_pipeline_local.py "/absolute/path/to/video.mp4" \
  --source-lang en \
  --target-lang zh-CN \
  --translator-backend argos
```

### ✏️ 外挂精翻时间轴对齐 (Translation Alignment)
如果你已经有了人工精翻的文本文件，只想自动化打时间轴对齐到视频的语音上：
```bash
./bootstrap_subaligner_env.sh
python3 align_existing_translation.py "/path/to/video.mp4" "/path/to/translated.txt"
```
（自动优先调用 Subaligner，失败则平滑降级为本地启发式对齐算法）。

### 🤖 纯字幕流水线（不压制）
如果只需要生成多语种字幕文件而无需渲染 MP4：
```bash
uv run --python 3.11 --with deep-translator \
  python subtitle_pipeline_local.py "/absolute/path/to/video.mp4" \
  --glossary ./glossary.example.json
```

---

## 👨‍💻 给开发者的 Agent SOP
对于通过 AI Coding Agents 接入使用本仓库的场景，我们准备了专门的对接指引，请查阅：[`docs/agent-sop.md`](docs/agent-sop.md)

## 📝 许可协议
本项目遵循开源协议。有关详细信息，请查看 [LICENSE](LICENSE) 文件。
