# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- New settings window to configure WhisperWriter.
- New main window to either start the keyboard listener or open the settings window.
- New continuous recording mode ([Issue #40](https://github.com/savbell/whisper-writer/issues/40)).
- New option to play a sound when transcription finishes ([Issue #40](https://github.com/savbell/whisper-writer/issues/40)).
- Chat interface with chat history, message action buttons, and auto-generated chat names.
- Chat search bar to quickly find conversations and messages.
- Streaming prompts with a setting to toggle streaming and automatic fallback when unavailable.
- Markdown rendering for output and markdown formatting support in prompts.
- Inline prompt popup with preview; clipboard prompting and clipboard context preview.
- Voice transcription available directly within the popup.
- Popup window enhancements: draggable, resizable, and click-outside-to-close with a toggle in settings.
- LLM provider selection (OpenAI/OpenRouter), editable model inputs, and unified helpers for context-based responses.
- Windows convenience script `vibe-writer.bat` to run the app.

### Changed

- Migrated status window from using `tkinter` to `PyQt5`.
- Migrated from using JSON to using YAML to store configuration settings.
- Upgraded to latest versions of `openai` and `faster-whisper`, including support for local API ([Issue #32](https://github.com/savbell/whisper-writer/issues/32)).
- Unified LLM generation flow and updated OpenRouter API key handling.
- Improved key chord handling and validation; enhanced typing mechanism with clipboard paste fallback; swapped popup keys for preview/paste.
- UI/UX improvements: side panel animation and open-state, settings navigation moved to the left, updated settings theme and scrollbars to match popup, and general UI responsiveness improvements.
- Windows-specific sizing tweaks for better layout.
- Repository hygiene: ignore database files (`*.db`) and stop tracking existing database files.

### Fixed

- Ensure input field retains focus in chat view.
- Abort any pending prompt when a new message is sent.
- Smoother animations with tighter dots and faster timing.
- Side panel animation glitches.

### Removed

- No longer using `keyboard` package to listen for key presses.

## [1.0.1] - 2024-01-28

### Added

- New message to identify whether Whisper was being called using the API or running locally.
- Additional hold-to-talk ([PR #28](https://github.com/savbell/whisper-writer/pull/28)) and press-to-toggle recording methods ([Issue #21](https://github.com/savbell/whisper-writer/issues/21)).
- New configuration options to:
  - Choose recording method (defaulting to voice activity detection).
  - Choose which sound device and sample rate to use.
  - Hide the status window ([PR #28](https://github.com/savbell/whisper-writer/pull/28)).

### Changed

- Migrated from `whisper` to `faster-whisper` ([Issue #11](https://github.com/savbell/whisper-writer/issues/11)).
- Migrated from `pyautogui` to `pynput` ([PR #10](https://github.com/savbell/whisper-writer/pull/10)).
- Migrated from `webrtcvad` to `webrtcvad-wheels` ([PR #17](https://github.com/savbell/whisper-writer/pull/17)).
- Changed default activation key combo from `ctrl+alt+space` to `ctrl+shift+space`.
- Changed to using a local model rather than the API by default.
- Revamped README.md, including new Roadmap, Contributing, and Credits sections.

### Fixed

- Local model is now only loaded once at start-up, rather than every time the activation key combo was pressed.
- Default configuration now auto-chooses compute type for the local model to avoid warnings.
- Graceful degradation to CPU if CUDA isn't available ([PR #30](https://github.com/savbell/whisper-writer/pull/30)).
- Removed long prefix of spaces in transcription ([PR #19](https://github.com/savbell/whisper-writer/pull/19)).

## [1.0.0] - 2023-05-29

### Added

- Initial release of WhisperWriter.
- Added CHANGELOG.md.
- Added Versioning and Known Issues to README.md.

### Changed

- Updated Whisper Python package; the local model is now compatible with Python 3.11.

[Unreleased]: https://github.com/savbell/whisper-writer/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/savbell/whisper-writer/releases/tag/v1.0.0...v1.0.1
[1.0.0]: https://github.com/savbell/whisper-writer/releases/tag/v1.0.0
