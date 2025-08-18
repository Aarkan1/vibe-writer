import os
import sys
import time
import threading
import sounddevice as sd
import soundfile as sf
from pynput.keyboard import Controller
from PyQt5.QtCore import QObject, QProcess, pyqtSignal, pyqtSlot, Qt, QTimer, QCoreApplication
from PyQt5.QtGui import QIcon, QGuiApplication
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox

from key_listener import KeyListener
from result_thread import ResultThread
from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from ui.prompt_popup import PromptPopup
from transcription import create_local_model
from input_simulation import InputSimulator
from utils import ConfigManager
import pyperclip
from llm_helper import generate_with_llm


class VibeWriterApp(QObject):
    # Bridge signals to ensure UI actions are executed on the Qt main thread
    showInlinePopupSignal = pyqtSignal()
    submitInlinePromptSignal = pyqtSignal(str)
    previewInlinePromptSignal = pyqtSignal(str)
    closeInlinePopupSignal = pyqtSignal()
    # Background completion result signals (emitted from worker threads)
    inlinePreviewReady = pyqtSignal(str)
    inlinePromptReady = pyqtSignal(str)

    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        # Enable robust High-DPI scaling before creating the QApplication
        try:
            QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
            QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        except Exception:
            pass
        # Fallback env toggles for platforms where attributes might be ignored
        try:
            os.environ.setdefault('QT_AUTO_SCREEN_SCALE_FACTOR', '1')
        except Exception:
            pass
        # Create the application after setting attributes
        self.app = QApplication(sys.argv)
        # Improve scaling rounding on Qt 5.15+ (prevents tiny windows on Windows)
        try:
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        except Exception:
            pass
        # Keep running in tray even if all windows are closed (e.g., popup closed via Esc)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))

        ConfigManager.initialize()

        self.settings_window = SettingsWindow()
        self.settings_window.settings_closed.connect(self.on_settings_closed)
        self.settings_window.settings_saved.connect(self.restart_app)

        if ConfigManager.config_file_exists():
            self.initialize_components()
        else:
            print('No valid configuration file found. Opening settings window...')
            self.settings_window.show()

    def initialize_components(self):
        """
        Initialize the components of the application.
        """
        self.key_listener = KeyListener()
        # Normal transcription hotkey
        self.key_listener.add_callback("on_activate", self.on_activation)
        self.key_listener.add_callback("on_deactivate", self.on_deactivation)
        # Prompting hotkey
        self.key_listener.add_callback("on_activate_prompt", self.on_activation_prompt)
        self.key_listener.add_callback("on_deactivate_prompt", self.on_deactivation_prompt)
        # Inline prompt popup hotkey (no recording)
        self.key_listener.add_callback("on_activate_inline_prompt", self.on_activation_inline_prompt)
        self.key_listener.add_callback("on_deactivate_inline_prompt", self.on_deactivation_inline_prompt)

        self.input_simulator = InputSimulator()

        model_options = ConfigManager.get_config_section('model_options')
        model_path = model_options.get('local', {}).get('model_path')
        self.local_model = create_local_model() if not model_options.get('use_api') else None

        self.result_thread = None
        # Track whether we should prompt after transcription ('prompt') or paste raw ('normal')
        self.current_mode = None

        self.main_window = MainWindow()
        self.main_window.openSettings.connect(self.settings_window.show)
        self.main_window.startListening.connect(self.key_listener.start)
        self.main_window.closeApp.connect(self.exit_app)

        # Inline prompt popup UI (lazy-initialized when needed)
        self.prompt_popup = None

        # Connect thread-safe UI signals
        self.showInlinePopupSignal.connect(self._show_inline_popup_on_ui)
        self.submitInlinePromptSignal.connect(self._handle_inline_prompt_submit_on_ui)
        self.previewInlinePromptSignal.connect(self._handle_inline_preview_on_ui)
        self.closeInlinePopupSignal.connect(self._close_inline_popup_on_ui)
        # Connect background completion signals
        self.inlinePreviewReady.connect(self._on_inline_preview_ready_on_ui)
        self.inlinePromptReady.connect(self._on_inline_prompt_ready_on_ui)

        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()

        self.create_tray_icon()

        # Start listening for the activation keys immediately
        try:
            self.key_listener.start()
            ak = ConfigManager.get_config_value('recording_options', 'activation_key')
            pak = ConfigManager.get_config_value('recording_options', 'prompt_activation_key')
            ConfigManager.console_print(f'Listening for hotkeys | paste: {ak} | prompt: {pak}')
        except Exception as e:
            print(f'Key listener failed to start: {e}')

    def create_tray_icon(self):
        """
        Create the system tray icon and its context menu.
        """
        self.tray_icon = QSystemTrayIcon(QIcon(os.path.join('assets', 'ww-logo.png')), self.app)

        tray_menu = QMenu()

        show_action = QAction('VibeWriter Main Menu', self.app)
        show_action.triggered.connect(self.main_window.show)
        tray_menu.addAction(show_action)

        settings_action = QAction('Open Settings', self.app)
        settings_action.triggered.connect(self.settings_window.show)
        tray_menu.addAction(settings_action)

        exit_action = QAction('Exit', self.app)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def cleanup(self):
        # Be robust when settings were not completed yet
        key_listener = getattr(self, 'key_listener', None)
        if key_listener:
            key_listener.stop()
        input_simulator = getattr(self, 'input_simulator', None)
        if input_simulator:
            input_simulator.cleanup()

    def exit_app(self):
        """
        Exit the application.
        """
        self.cleanup()
        QApplication.quit()

    def restart_app(self):
        """Restart the application to apply the new settings."""
        self.cleanup()
        QApplication.quit()
        QProcess.startDetached(sys.executable, sys.argv)

    def on_settings_closed(self):
        """
        If settings is closed without saving on first run, initialize the components with default values.
        """
        if not os.path.exists(os.path.join('src', 'config.yaml')):
            QMessageBox.information(
                self.settings_window,
                'Using Default Values',
                'Settings closed without saving. Default values are being used.'
            )
            self.initialize_components()

    def on_activation(self):
        """
        Called when the activation key combination is pressed.
        """
        self.current_mode = 'normal'
        if self.result_thread and self.result_thread.isRunning():
            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if recording_mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif recording_mode == 'continuous':
                self.stop_result_thread()
            return

        self.start_result_thread()

    def on_activation_prompt(self):
        """Called when the prompt activation key combination is pressed."""
        self.current_mode = 'prompt'
        if self.result_thread and self.result_thread.isRunning():
            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if recording_mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif recording_mode == 'continuous':
                self.stop_result_thread()
            return

        self.start_result_thread()

    def on_deactivation(self):
        """
        Called when the activation key combination is released.
        """
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def on_deactivation_prompt(self):
        """Called when the prompt activation key combination is released."""
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def start_result_thread(self):
        """
        Start the result thread to record audio and transcribe it.
        """
        if self.result_thread and self.result_thread.isRunning():
            return

        self.result_thread = ResultThread(self.local_model)
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
            self.status_window.closeSignal.connect(self.stop_result_thread)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        self.result_thread.start()

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    def on_transcription_complete(self, result):
        """
        When the transcription is complete, either paste it directly (normal mode)
        or use the clipboard selection as context for an OpenRouter prompt (prompt mode).

        Flow:
        1) Save the transcription in a local variable.
        2) If in prompt mode, trigger a system copy (Ctrl/Cmd+C), read clipboard,
           call OpenRouter with clipboard as CONTEXT and transcription as INSTRUCTIONS.
           Otherwise, paste the transcription directly.
        """
        transcription_text = result or ''

        final_output = ''
        if self.current_mode == 'prompt' and transcription_text:
            # In prompt mode: copy selection and read clipboard (no 2-word check)
            copy_sent = self.input_simulator.copy_selection_to_clipboard()
            time.sleep(0.12)
            clipboard_text = (pyperclip.paste() or '').strip()
            if not clipboard_text and hasattr(self, 'app'):
                try:
                    clipboard_text = (self.app.clipboard().text() or '').strip()
                except Exception:
                    clipboard_text = ''
            ConfigManager.console_print(
                f"Transcription complete (prompt mode) | len(transcription)={len(transcription_text)} | copy_sent={copy_sent} | len(clipboard)={len(clipboard_text)}"
            )
            final_output = generate_with_llm(clipboard_text, transcription_text) or ''
            if not final_output:
                ConfigManager.console_print("LLM provider returned empty result. Falling back to plain transcription.")
                final_output = transcription_text
        else:
            # Normal mode: just paste the transcription as-is
            ConfigManager.console_print(
                f"Transcription complete (normal mode) | len(transcription)={len(transcription_text)}"
            )
            final_output = transcription_text

        self.input_simulator.typewrite(final_output)

        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            play_beep()

        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
            self.start_result_thread()
        else:
            self.key_listener.start()

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())

    # ---------------- Inline Prompt Popup (no recording) ---------------- #

    def _ensure_prompt_popup(self):
        if self.prompt_popup is None:
            self.prompt_popup = PromptPopup()
            self.prompt_popup.submitted.connect(self.on_inline_prompt_submitted)
            self.prompt_popup.preview_requested.connect(self.on_inline_preview_requested)
            self.prompt_popup.cancelled.connect(self.on_inline_prompt_cancelled)

    def on_activation_inline_prompt(self):
        """Open the inline prompt popup. Copy selection to clipboard first."""
        # Stop listener while we capture input in the popup
        try:
            self.key_listener.stop()
        except Exception:
            pass
        # Prepare in UI thread: clear clipboard, copy selection (Ctrl/Cmd+C), then show popup
        self.showInlinePopupSignal.emit()

    def on_deactivation_inline_prompt(self):
        """No-op for now; popup flow is handled on submit/cancel."""
        return

    def on_inline_prompt_submitted(self, instructions_text: str):
        """Handle popup submit: call OpenRouter with clipboard as context, then paste."""
        # Defer to UI thread handler to control focus and window lifetime
        self.submitInlinePromptSignal.emit(instructions_text)

    def on_inline_preview_requested(self, instructions_text: str):
        """Handle Enter in popup: generate and display a preview inside the popup."""
        self.previewInlinePromptSignal.emit(instructions_text)

    def on_inline_prompt_cancelled(self):
        """Close popup and resume listening without action."""
        self.closeInlinePopupSignal.emit()

    # ---------------- UI-thread helpers for inline popup ---------------- #

    def _show_inline_popup_on_ui(self):
        # 1) Clear clipboard, 2) send system copy, 3) verify clipboard, 4) show popup
        # Step 1: Clear clipboard (both pyperclip and Qt)
        try:
            import pyperclip as _pc
            _pc.copy("")
        except Exception:
            pass
        try:
            cb = self.app.clipboard()
            cb.clear()
            cb.setText("")
        except Exception:
            pass
        # Step 2: small delay to ensure previous modifiers settle, then copy
        QTimer.singleShot(40, self._do_system_copy_for_inline_popup)

    def _do_system_copy_for_inline_popup(self):
        copy_sent = self.input_simulator.copy_selection_to_clipboard()
        ConfigManager.console_print(f"Inline prompt: copy_sent={copy_sent}")
        # Step 3: wait for OS to populate clipboard, then verify
        QTimer.singleShot(140, self._verify_clipboard_then_show)

    def _verify_clipboard_then_show(self):
        # Check clipboard; if empty, retry copy once before showing
        try:
            import pyperclip as _pc
            text = (_pc.paste() or '').strip()
        except Exception:
            text = ''
        if not text:
            try:
                text = (self.app.clipboard().text() or '').strip()
            except Exception:
                text = ''
        if not text:
            # Retry copy once
            retry_sent = self.input_simulator.copy_selection_to_clipboard()
            ConfigManager.console_print(f"Inline prompt: retry copy_sent={retry_sent}")
            QTimer.singleShot(140, self._do_show_inline_popup)
        else:
            self._do_show_inline_popup()

    def _do_show_inline_popup(self):
        self._ensure_prompt_popup()
        # Ensure the text area is empty every time it opens
        try:
            self.prompt_popup.reset()
        except Exception:
            pass
        self.prompt_popup.show()
        self.prompt_popup.raise_()
        self.prompt_popup.activateWindow()

    def _handle_inline_prompt_submit_on_ui(self, instructions_text: str):
        import pyperclip as _pc
        # Ctrl+Enter (submit): If there is a previous assistant message, paste it.
        # Otherwise, generate now and paste when ready.
        last_assistant = ''
        try:
            if self.prompt_popup:
                last_assistant = (self.prompt_popup.get_last_assistant_text() or '').strip()
        except Exception:
            last_assistant = ''
        if last_assistant:
            if self.prompt_popup:
                self.prompt_popup.set_loading(False)
                self.prompt_popup.close()
                try:
                    self.prompt_popup.reset()
                except Exception:
                    pass
            self._paste_with_verification_and_fallback(last_assistant)
            if ConfigManager.get_config_value('misc', 'noise_on_completion'):
                play_beep()
            try:
                self.key_listener.start()
            except Exception:
                pass
            return
        # Read context from clipboard after earlier copy
        context_text = (_pc.paste() or '').strip()
        if not context_text and hasattr(self, 'app'):
            try:
                context_text = (self.app.clipboard().text() or '').strip()
            except Exception:
                context_text = ''
        # Add user's message bubble, clear input, keep focus, and show loader while we compute
        if self.prompt_popup:
            try:
                self.prompt_popup.add_user_message(instructions_text)
            except Exception:
                pass
            try:
                self.prompt_popup.text_edit.clear()
                self.prompt_popup.text_edit.setFocus(Qt.ActiveWindowFocusReason)
            except Exception:
                pass
            self.prompt_popup.set_loading(True)
        # Start background completion so UI stays responsive
        def _worker():
            final_output = generate_with_llm(context_text, instructions_text) or ''
            self.inlinePromptReady.emit(final_output)
        threading.Thread(target=_worker, daemon=True).start()

    def _handle_inline_preview_on_ui(self, instructions_text: str):
        import pyperclip as _pc
        # Add user's message bubble, clear input, keep focus, then read context
        if self.prompt_popup:
            try:
                self.prompt_popup.add_user_message(instructions_text)
            except Exception:
                pass
            try:
                self.prompt_popup.text_edit.clear()
                self.prompt_popup.text_edit.setFocus(Qt.ActiveWindowFocusReason)
            except Exception:
                pass
        context_text = (_pc.paste() or '').strip()
        if not context_text and hasattr(self, 'app'):
            try:
                context_text = (self.app.clipboard().text() or '').strip()
            except Exception:
                context_text = ''
        # Show loader but keep popup open
        if self.prompt_popup:
            self.prompt_popup.set_loading(True)
        # Start background preview computation
        def _worker():
            final_output = generate_with_llm(context_text, instructions_text) or ''
            self.inlinePreviewReady.emit(final_output)
        threading.Thread(target=_worker, daemon=True).start()

    def _complete_inline_preview(self, context_text: str, instructions_text: str):
        final_output = generate_with_llm(context_text, instructions_text) or ''
        if not final_output:
            final_output = ''
        if self.prompt_popup:
            self.prompt_popup.set_loading(False)
            self.prompt_popup.set_result_text(final_output)

    def _complete_inline_prompt_after_focus(self, context_text: str, instructions_text: str):
        final_output = generate_with_llm(context_text, instructions_text) or ''
        if not final_output:
            final_output = ''
        # Close popup now so focus returns to previous window
        if self.prompt_popup:
            self.prompt_popup.set_loading(False)
            self.prompt_popup.close()
            # Clear input so next open is empty
            try:
                self.prompt_popup.reset()
            except Exception:
                pass
        # Put result into clipboard and use system paste so it lands where the hotkey was triggered
        try:
            import pyperclip as _pc
            _pc.copy(final_output)
        except Exception:
            pass
        try:
            cb = self.app.clipboard()
            cb.setText(final_output)
        except Exception:
            pass
        # Give the OS a brief moment to settle focus, then paste
        # Paste with clipboard verification and fallback to typing
        self._paste_with_verification_and_fallback(final_output)
        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            play_beep()
        # Resume hotkey listening
        try:
            self.key_listener.start()
        except Exception:
            pass

    def _close_inline_popup_on_ui(self):
        if self.prompt_popup:
            self.prompt_popup.close()
        try:
            self.key_listener.start()
        except Exception:
            pass

    @pyqtSlot(str)
    def _on_inline_preview_ready_on_ui(self, final_output: str):
        # Render preview result as assistant bubble, stop loader, and keep input focused
        if self.prompt_popup:
            self.prompt_popup.set_loading(False)
            try:
                self.prompt_popup.add_assistant_message(final_output or '')
            except Exception:
                pass
            try:
                self.prompt_popup.text_edit.setFocus(Qt.ActiveWindowFocusReason)
            except Exception:
                pass

    @pyqtSlot(str)
    def _on_inline_prompt_ready_on_ui(self, final_output: str):
        # Close popup, paste the assistant message, and resume hotkey listening
        if self.prompt_popup:
            self.prompt_popup.set_loading(False)
            try:
                # Add assistant bubble before closing so the full chat is visible briefly
                self.prompt_popup.add_assistant_message(final_output or '')
            except Exception:
                pass
            self.prompt_popup.close()
            try:
                self.prompt_popup.reset()
            except Exception:
                pass
        # Paste with clipboard verification and fallback to typing
        self._paste_with_verification_and_fallback(final_output or '')
        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            play_beep()
        try:
            self.key_listener.start()
        except Exception:
            pass

    def _paste_with_verification_and_fallback(self, text: str, delay_ms: int = 50):
        """Attempt to paste by setting clipboard and sending paste.

        If clipboard cannot be set (e.g., Wayland/X11 ownership issues), fall back to typing.
        """
        def _attempt():
            # On Linux desktops, clipboard ownership can be restricted; prefer typing directly.
            try:
                if sys.platform.startswith('linux'):
                    self.input_simulator.typewrite(text)
                    return
            except Exception:
                pass
            # Try both pyperclip and Qt clipboard
            try:
                import pyperclip as _pc
                _pc.copy(text)
            except Exception:
                pass
            try:
                cb = self.app.clipboard()
                cb.setText(text)
            except Exception:
                pass
            # Verify clipboard contents
            qt_ok = False
            pc_ok = False
            try:
                qt_text = (self.app.clipboard().text() or '').strip()
                qt_ok = (qt_text == (text or '').strip())
            except Exception:
                qt_ok = False
            try:
                import pyperclip as _pc
                pc_text = (_pc.paste() or '').strip()
                pc_ok = (pc_text == (text or '').strip())
            except Exception:
                pc_ok = False
            # If clipboard seems correct, try system paste; otherwise type
            if qt_ok or pc_ok:
                sent = self.input_simulator.paste_from_clipboard()
                if sent:
                    return
            # Fallback: simulate typing
            self.input_simulator.typewrite(text)

        # Slight delay to allow focus return and clipboard propagation before paste
        QTimer.singleShot(delay_ms, _attempt)


def play_beep():
    """
    Play a short WAV beep using sounddevice/soundfile.
    This avoids system-level GI/GStreamer deps.
    """
    try:
        data, sr = sf.read(os.path.join('assets', 'beep.wav'), dtype='float32')
        sd.play(data, sr)
        sd.wait()
    except Exception as e:
        print(f'Beep playback failed: {e}')


if __name__ == '__main__':
    app = VibeWriterApp()
    app.run()
