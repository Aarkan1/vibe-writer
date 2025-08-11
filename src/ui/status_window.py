import sys
import os
import math
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout

# Keep path consistent with the rest of the app (no BaseWindow needed here)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class EqualizerWidget(QWidget):
    """
    Smooth, minimal equalizer animation.
    - 5 bars animated with phase-offset sine waves.
    - Designed for a dark background.
    - No text, no icons. Subtle, modern look.
    """
    def __init__(self, fps=60, parent=None):
        super().__init__(parent)
        self.setFixedHeight(42)  # compact height for sleek appearance
        self._fps = fps
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        # Bar colors chosen to pop on a dark background
        self._bar_color = QColor('#6EE7B7')  # mint green
        self._bars = 5
        self._period = 1.05  # seconds
        self._amplitude = 1.0

    def start(self):
        if not self._timer.isActive():
            self._timer.start(int(1000 / self._fps))

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()

    def _tick(self):
        self._t = (self._t + 1.0 / self._fps) % 10.0
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Geometry: tighter spacing, consistent widths
        spacing = max(6.0, w * 0.03)
        total_spacing = spacing * (self._bars - 1)
        bar_width = max(5.0, (w - total_spacing) / self._bars)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self._bar_color))

        for i in range(self._bars):
            phase_offset = (i / max(1, self._bars - 1)) * math.pi
            phase = (self._t % self._period) / self._period
            s = 0.5 + 0.5 * math.sin(2 * math.pi * phase + phase_offset)

            # Height varies between 28% and 100% of half-height for smoothness
            min_h = 0.28
            scale = min_h + (self._amplitude * (1 - min_h)) * s
            bar_h = (h * 0.52) * scale  # a touch over half height for balance
            x = i * (bar_width + spacing)
            y = (h - bar_h) / 2.0

            rect = QRectF(x, y, bar_width, bar_h)
            painter.drawRoundedRect(rect, bar_width * 0.35, bar_width * 0.35)


class StatusWindow(QWidget):
    """
    Dark, minimal status window that only shows a sleek equalizer animation.
    - Frameless, on top, translucent background.
    - Minimal padding. No title bar, no close button.
    """
    statusSignal = pyqtSignal(str)
    closeSignal = pyqtSignal()

    def __init__(self):
        super().__init__()

        # Window styling and behavior
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # Size tuned for sleekness; content is centered
        self.setFixedSize(160, 56)

        # Layout: minimal padding, no extra content
        self.equalizer = EqualizerWidget()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)  # remove most padding
        layout.addStretch(1)
        layout.addWidget(self.equalizer, stretch=1)
        layout.addStretch(1)

        # Connect status updates
        self.statusSignal.connect(self.updateStatus)

    def paintEvent(self, _):
        """
        Paint the dark rounded background.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Deep neutral background with subtle border
        bg = QColor(15, 17, 21, 235)  # near-black, slightly translucent
        border = QColor(58, 64, 72, 200)  # subtle cool-gray border

        rect = self.rect().adjusted(0, 0, -1, -1)
        radius = 14

        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(rect, radius, radius)

    def show(self):
        """
        Position in bottom-center. Only the equalizer is visible.
        """
        screen = QApplication.primaryScreen()
        g = screen.geometry()
        x = (g.width() - self.width()) // 2
        y = g.height() - self.height() - 120
        self.move(x, y)
        super().show()

    def closeEvent(self, event):
        """
        Stop animation and emit close signal when closed.
        """
        self.equalizer.stop()
        self.closeSignal.emit()
        super().closeEvent(event)

    @pyqtSlot(str)
    def updateStatus(self, status):
        """
        recording/transcribing -> show/start equalizer
        idle/error/cancel -> stop/close
        """
        if status in ('recording', 'transcribing'):
            self.equalizer.start()
            self.show()
        elif status in ('idle', 'error', 'cancel'):
            self.equalizer.stop()
            self.close()


if __name__ == '__main__':
    app = QApplication(sys.argv)

    status_window = StatusWindow()
    status_window.show()

    # Simulate recording -> transcribing -> idle
    QTimer.singleShot(1000, lambda: status_window.statusSignal.emit('recording'))
    QTimer.singleShot(3500, lambda: status_window.statusSignal.emit('transcribing'))
    QTimer.singleShot(7000, lambda: status_window.statusSignal.emit('idle'))

    sys.exit(app.exec_())