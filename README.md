## <img src="./assets/ww-logo.png" alt="Vibe Writer icon" width="25" height="25"> Vibe Writer

![version](https://img.shields.io/badge/version-2.0.0-blue)

<p align="center">
  <em>Lightweight speech‑to‑text that types for you in any app.</em>
  <br/>
</p>

## Overview

Vibe Writer turns your voice into text and types it into the active window. It can run fully local via `faster-whisper` or use an API endpoint, and it stays out of the way until you hit the global hotkey.

### Highlights

- **Fast dictation**: Transcribe locally or via API.
- **Four recording modes**: `continuous`, `voice_activity_detection`, `press_to_toggle`, `hold_to_record`.
- **Global hotkey**: `ctrl+shift+space` by default; customizable in Settings.
- **Minimal UI**: Compact status window you can hide.
- **Configurable post‑processing**: Add trailing space, lowercase, and more.
- **GPU optional**: Use CUDA for speed with supported NVIDIA libraries.

## Quick start

### 1) System packages (Linux, for audio/GUI)

```bash
sudo apt install -y \
  libportaudio2 libx11-xcb1 libxcb1 libxcb-render0 libxcb-shape0 libxcb-xfixes0 \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-util1 \
  libxrender1 libxkbcommon-x11-0 libxcb-xinerama0
```

### 2) Create env and install deps (uv – recommended)

```bash
uv venv --python 3.11
uv pip install -r requirements.txt
```

Alternatively, using `venv` + `pip`:

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

### 3) Run

```bash
uv run run.py
# or
python run.py
```

On first run, the Settings window appears. Save your preferences, then press Start to enable the hotkey listener. Use the activation key to begin/stop recording.

## Usage

- **Start/Stop**: Press the activation key (`ctrl+shift+space` by default).
- **Modes**:
  - `continuous`: Auto‑restart after pauses; press the hotkey again to stop.
  - `voice_activity_detection`: Stop after a pause; press hotkey to start again.
  - `press_to_toggle`: Start/stop with the hotkey.
  - `hold_to_record`: Record only while holding the hotkey.
- **Typing output**: Transcription is typed into the active window. You can hide the status window if you prefer a clean screen.

## Configuration

Open the Settings window to adjust options. Common settings include:

- **Model**: Local (`faster-whisper`) or API (OpenAI‑compatible base URL + API key).
- **Language/temperature/prompt**: Control transcription behavior.
- **Device/compute type**: `auto`, `cpu`, or `cuda` with quantization modes.
- **VAD filter**: Remove silence before transcription.
- **Recording**: Activation key, input backend, device index, sample rate.
- **Post‑processing**: Trailing space, lowercase, key‑press delay, etc.

For a full reference, see `src/config_schema.yaml`. If an option is missing or invalid, sensible defaults are used.

## GPU notes (optional)

If you want local transcription on GPU, install NVIDIA libraries compatible with CUDA 12 (e.g., cuBLAS and cuDNN 8). See the `faster-whisper` docs for details. On Linux you can also try:

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
export LD_LIBRARY_PATH=`python3 -c 'import os; import nvidia.cublas.lib; import nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))'`
```

## Development

```bash
git clone https://github.com/your-username/vibe-writer.git
cd vibe-writer
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate  # Windows
pip install -r requirements.txt
python run.py
```

## Contributing

Ideas, bug reports, and pull requests are welcome. Please keep changes small and focused.

## Credits

- Originally forked from `savbell/whisper-writer` — huge thanks to the original author and contributors.
- [OpenAI](https://openai.com/) for Whisper and APIs. Much of the early iteration of the original project used AI tooling.
- [Guillaume Klein](https://github.com/guillaumekln) for [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## License

This project is licensed under the GNU General Public License. See the `LICENSE` file for details.
