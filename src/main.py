import os
import sys
import time
import sounddevice as sd
import soundfile as sf
from pynput.keyboard import Controller
from PyQt5.QtCore import QObject, QProcess
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox

from key_listener import KeyListener
from result_thread import ResultThread
from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from transcription import create_local_model
from input_simulation import InputSimulator
from utils import ConfigManager
import pyperclip
from openrouter_helper import generate_with_openrouter


class WhisperWriterApp(QObject):
    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        self.app = QApplication(sys.argv)
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
        self.key_listener.add_callback("on_activate", self.on_activation)
        self.key_listener.add_callback("on_deactivate", self.on_deactivation)

        self.input_simulator = InputSimulator()

        model_options = ConfigManager.get_config_section('model_options')
        model_path = model_options.get('local', {}).get('model_path')
        self.local_model = create_local_model() if not model_options.get('use_api') else None

        self.result_thread = None

        self.main_window = MainWindow()
        self.main_window.openSettings.connect(self.settings_window.show)
        self.main_window.startListening.connect(self.key_listener.start)
        self.main_window.closeApp.connect(self.exit_app)

        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()

        self.create_tray_icon()

        # Start listening for the activation key immediately
        try:
            self.key_listener.start()
            ak = ConfigManager.get_config_value('recording_options', 'activation_key')
            ConfigManager.console_print(f'Listening for activation key: {ak}')
        except Exception as e:
            print(f'Key listener failed to start: {e}')

    def create_tray_icon(self):
        """
        Create the system tray icon and its context menu.
        """
        self.tray_icon = QSystemTrayIcon(QIcon(os.path.join('assets', 'ww-logo.png')), self.app)

        tray_menu = QMenu()

        show_action = QAction('WhisperWriter Main Menu', self.app)
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
        When the transcription is complete, decide whether to paste it directly or
        to use the clipboard selection as context for an OpenRouter prompt.

        Flow:
        1) Save the transcription in a local variable.
        2) Trigger a system copy (Ctrl/Cmd+C) so selected text goes to clipboard.
        3) Read clipboard; if it has at least 2 words, call OpenRouter with
           clipboard as CONTEXT and transcription as INSTRUCTIONS, and type the
           response. Otherwise, type the transcription directly.
        """
        transcription_text = result or ''

        # Attempt to copy any current selection so we can optionally use it as context
        copy_sent = self.input_simulator.copy_selection_to_clipboard()

        # Give the system a brief moment to update the clipboard after copy
        # Note: very short sleep to avoid user-visible lag while improving reliability
        time.sleep(0.12)

        # Read clipboard and check if it contains at least 2 words
        clipboard_text = (pyperclip.paste() or '').strip()
        if not clipboard_text and hasattr(self, 'app'):
            # Fallback to Qt clipboard if pyperclip has no access (e.g., sandbox/WSL)
            try:
                clipboard_text = (self.app.clipboard().text() or '').strip()
            except Exception:
                clipboard_text = ''
        clipboard_words = [w for w in clipboard_text.split() if w]
        has_enough_context = len(clipboard_words) >= 2

        # Log diagnostics about the decision path
        ConfigManager.console_print(
            f"Transcription complete | len(transcription)={len(transcription_text)} | copy_sent={copy_sent} | "
            f"len(clipboard)={len(clipboard_text)} | words={len(clipboard_words)} | use_prompt={has_enough_context and bool(transcription_text)}"
        )

        final_output = ''
        if has_enough_context and transcription_text:
            # Use OpenRouter with clipboard context and spoken instructions
            ConfigManager.console_print("Invoking OpenRouter with clipboard context and spoken instructions...")
            final_output = generate_with_openrouter(clipboard_text, transcription_text) or ''
            if final_output:
                ConfigManager.console_print(f"OpenRouter returned output len={len(final_output)}")
            else:
                ConfigManager.console_print("OpenRouter returned empty result. Falling back to plain transcription.")

        if not final_output:
            # Fallback to plain transcription
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
    app = WhisperWriterApp()
    app.run()
