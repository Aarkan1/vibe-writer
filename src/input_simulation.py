import subprocess
import os
import signal
import time
import sys
from pynput.keyboard import Controller as PynputController, Key as PynputKey
import pyperclip

from utils import ConfigManager, sanitize_text_for_output, transliterate_for_typing

def run_command_or_exit_on_failure(command):
    """
    Run a shell command and exit if it fails.

    Args:
        command (list): The command to run as a list of strings.
    """
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        exit(1)

class InputSimulator:
    """
    A class to simulate keyboard input using various methods.
    """

    def __init__(self):
        """
        Initialize the InputSimulator with the specified configuration.
        """
        self.input_method = ConfigManager.get_config_value('post_processing', 'input_method')
        self.dotool_process = None

        if self.input_method == 'pynput':
            self.keyboard = PynputController()
        elif self.input_method == 'dotool':
            self._initialize_dotool()

    def _initialize_dotool(self):
        """
        Initialize the dotool process for input simulation.
        """
        self.dotool_process = subprocess.Popen("dotool", stdin=subprocess.PIPE, text=True)
        assert self.dotool_process.stdin is not None

    def _terminate_dotool(self):
        """
        Terminate the dotool process if it's running.
        """
        if self.dotool_process:
            os.kill(self.dotool_process.pid, signal.SIGINT)
            self.dotool_process = None

    def typewrite(self, text):
        """
        Write the given text to the active application.

        Prefers a single paste operation (copy entire text to clipboard, then send
        system paste), which avoids per-key intervals and is significantly faster.
        If clipboard/paste fails for any reason, falls back to per-key typing
        using the configured input method and key press delay.

        Args:
            text (str): The text to type.
        """
        # Sanitize text to avoid narrow/no-break spaces that can mojibake in targets
        text = sanitize_text_for_output(text)
        # First, try to paste everything in one go via the system clipboard.
        # This is preferred to avoid any inter-key delay and produce instant output.
        try:
            # Copy entire text to clipboard in one shot.
            pyperclip.copy(text)
            # Send a single paste command (Cmd/Ctrl+V). If successful, we're done.
            if self.paste_from_clipboard():
                return
        except Exception:
            # If clipboard is unavailable or paste fails, fall back to key-by-key typing below.
            pass

        # Fallback: type per key using the selected backend and configured delay.
        interval = ConfigManager.get_config_value('post_processing', 'writing_key_press_delay')
        # For per-key typing fallback, conservatively transliterate problematic glyphs
        safe_text = transliterate_for_typing(text)
        if self.input_method == 'pynput':
            self._typewrite_pynput(safe_text, interval)
        elif self.input_method == 'ydotool':
            self._typewrite_ydotool(safe_text, interval)
        elif self.input_method == 'dotool':
            self._typewrite_dotool(safe_text, interval)

    def _typewrite_pynput(self, text, interval):
        """
        Simulate typing using pynput.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
        """
        for char in text:
            self.keyboard.press(char)
            self.keyboard.release(char)
            time.sleep(interval)

    def _typewrite_ydotool(self, text, interval):
        """
        Simulate typing using ydotool.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
        """
        cmd = "ydotool"
        run_command_or_exit_on_failure([
            cmd,
            "type",
            "--key-delay",
            str(interval * 1000),
            "--",
            text,
        ])

    def _typewrite_dotool(self, text, interval):
        """
        Simulate typing using dotool.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
        """
        assert self.dotool_process and self.dotool_process.stdin
        self.dotool_process.stdin.write(f"typedelay {interval * 1000}\n")
        self.dotool_process.stdin.write(f"type {text}\n")
        self.dotool_process.stdin.flush()

    def cleanup(self):
        """
        Perform cleanup operations, such as terminating the dotool process.
        """
        if self.input_method == 'dotool':
            self._terminate_dotool()

    def copy_selection_to_clipboard(self):
        """
        Send the system shortcut to copy the current selection to clipboard.

        For simplicity, supports only the 'pynput' input method. On macOS uses
        CMD+C, otherwise CTRL+C. Returns True if a copy command was sent.
        """
        # Always attempt to send a system copy using pynput, regardless of input_method
        controller = None
        try:
            controller = self.keyboard if self.input_method == 'pynput' else PynputController()
        except Exception:
            controller = None

        if controller is None:
            return False

        modifier_key = PynputKey.cmd if sys.platform == 'darwin' else PynputKey.ctrl
        try:
            # Send system copy (Cmd/Ctrl+C)
            controller.press(modifier_key)
            controller.press('c')
            controller.release('c')
            controller.release(modifier_key)
            # Small delay to allow clipboard to update
            time.sleep(0.1)
            ConfigManager.console_print("Sent system copy (Cmd/Ctrl+C) for clipboard context.")
            return True
        except Exception:
            return False

    def paste_from_clipboard(self):
        """
        Send the system shortcut to paste the clipboard contents.

        Uses CMD+V on macOS, CTRL+V otherwise. Returns True if a paste command was sent.
        """
        # Attempt to send a system paste using pynput regardless of configured input_method.
        # This mirrors copy behavior and allows single-shot paste even when using ydotool/dotool for typing.
        controller = None
        try:
            controller = self.keyboard if self.input_method == 'pynput' else PynputController()
        except Exception:
            controller = None

        if controller is None:
            return False

        modifier_key = PynputKey.cmd if sys.platform == 'darwin' else PynputKey.ctrl
        try:
            controller.press(modifier_key)
            controller.press('v')
            controller.release('v')
            controller.release(modifier_key)
            time.sleep(0.06)
            ConfigManager.console_print("Sent system paste (Cmd/Ctrl+V) for output.")
            return True
        except Exception:
            return False
