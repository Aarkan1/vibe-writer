from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QRectF, QEvent
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QApplication, QLabel


class SpinnerWidget(QWidget):
	"""
	Minimal spinning arc loader. Dark-theme friendly.
	"""

	def __init__(self, diameter: int = 28, parent=None):
		super().__init__(parent)
		self._diameter = diameter
		self._angle = 0.0
		self._timer = QTimer(self)
		self._timer.timeout.connect(self._tick)
		self.setFixedSize(self._diameter, self._diameter)

	def start(self):
		if not self._timer.isActive():
			self._timer.start(16)

	def stop(self):
		if self._timer.isActive():
			self._timer.stop()

	def _tick(self):
		self._angle = (self._angle + 6.0) % 360.0
		self.update()

	def paintEvent(self, _):
		p = QPainter(self)
		p.setRenderHint(QPainter.Antialiasing)
		pen = QPen(QColor('#6EE7B7'))
		pen.setWidthF(max(2.0, self._diameter * 0.08))
		pen.setCapStyle(Qt.RoundCap)
		p.setPen(pen)
		p.setBrush(Qt.NoBrush)
		m = pen.widthF()
		rect = QRectF(m, m, self._diameter - 2*m, self._diameter - 2*m)
		span_deg = 120 * 16  # 120 degrees in Qt's 1/16th units
		start_deg = int(self._angle * 16)
		p.drawArc(rect, start_deg, span_deg)


class PromptPopup(QWidget):
	"""
	Centered dark popup with a text area for inline instructions.
	- Shift+Enter inserts a newline
	- Enter submits
	- Esc cancels
	- Uses the same dark rounded theme as the status equalizer
	"""

	submitted = pyqtSignal(str)
	cancelled = pyqtSignal()

	def __init__(self, parent=None):
		super().__init__(parent)
		self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
		self.setAttribute(Qt.WA_TranslucentBackground, True)
		# Ensure closing the popup never quits the whole app
		self.setAttribute(Qt.WA_QuitOnClose, False)
		self.setWindowModality(Qt.ApplicationModal)
		self.setFocusPolicy(Qt.StrongFocus)
		self.setFixedSize(700, 360)

		layout = QVBoxLayout(self)
		layout.setContentsMargins(14, 14, 14, 14)
		layout.setSpacing(8)

		self.hint_label = QLabel("Type instructions. Enter: submit • Shift+Enter: newline • Esc: cancel")
		self.hint_label.setStyleSheet("color: #B5B9C0; font-size: 12px;")
		layout.addWidget(self.hint_label)

		self.text_edit = QTextEdit(self)
		self.text_edit.setPlaceholderText("Write your instructions…")
		self.text_edit.setStyleSheet(
			"QTextEdit { background: transparent; color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 8px; font-size: 14px; }"
		)
		self.text_edit.setAcceptRichText(False)
		self.text_edit.setTabChangesFocus(False)
		self.text_edit.setFocusPolicy(Qt.StrongFocus)
		# Intercept Enter/Escape while the editor has focus
		self.text_edit.installEventFilter(self)
		layout.addWidget(self.text_edit, stretch=1)

		# Subtle spinner loader; hidden by default
		self.loader = SpinnerWidget(diameter=28)
		self.loader.hide()
		layout.addWidget(self.loader)

	def reset(self):
		"""Clear input and reset UI state so popup opens empty and ready."""
		self.set_loading(False)
		self.text_edit.clear()

	def show(self):
		# Center on screen
		screen = QApplication.primaryScreen()
		g = screen.geometry()
		x = (g.width() - self.width()) // 2
		y = (g.height() - self.height()) // 2
		self.move(x, y)
		super().show()
		self.raise_()
		self.activateWindow()
		self.text_edit.setFocus(Qt.ActiveWindowFocusReason)
		# Some platforms need a brief delay to reliably focus after show
		QTimer.singleShot(60, self.force_focus)

	def force_focus(self):
		self.raise_()
		try:
			wh = self.windowHandle()
			if wh is not None:
				wh.requestActivate()
		except Exception:
			pass
		self.activateWindow()
		self.text_edit.setFocus(Qt.ActiveWindowFocusReason)

	def paintEvent(self, _):
		# Dark rounded background, same as StatusWindow theme
		painter = QPainter(self)
		painter.setRenderHint(QPainter.Antialiasing)
		bg = QColor(15, 17, 21, 235)
		border = QColor(58, 64, 72, 200)
		rect = self.rect().adjusted(0, 0, -1, -1)
		radius = 14
		painter.setPen(QPen(border, 1))
		painter.setBrush(QBrush(bg))
		painter.drawRoundedRect(rect, radius, radius)

	def keyPressEvent(self, event):
		# Enter: submit; Shift+Enter: newline; Esc: cancel
		if event.key() in (Qt.Key_Return, Qt.Key_Enter):
			mods = event.modifiers()
			if mods & Qt.ShiftModifier:
				# Insert newline
				self.text_edit.insertPlainText("\n")
				return
			# Do not submit when Ctrl/Alt/Meta are held
			if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
				return
			# Submit only for plain Enter
			text = self.text_edit.toPlainText().strip()
			self.submitted.emit(text)
			return
		elif event.key() == Qt.Key_Escape:
			self.cancelled.emit()
			return
		# Default handling (e.g., typing)
		super().keyPressEvent(event)

	def eventFilter(self, obj, event):
		# Ensure plain Enter submits even when QTextEdit has focus
		if obj is self.text_edit and event.type() == QEvent.KeyPress:
			key = event.key()
			mods = event.modifiers()
			if key in (Qt.Key_Return, Qt.Key_Enter):
				if mods & Qt.ShiftModifier:
					self.text_edit.insertPlainText("\n")
					return True
				if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
					# Ignore modified Enter (no submit)
					return True
				text = self.text_edit.toPlainText().strip()
				self.submitted.emit(text)
				return True
			if key == Qt.Key_Escape:
				self.cancelled.emit()
				return True
		return super().eventFilter(obj, event)

	def set_loading(self, is_loading: bool):
		"""Show/hide loader and optionally disable input while waiting."""
		if is_loading:
			self.loader.show()
			self.loader.start()
			self.text_edit.setDisabled(True)
			self.hint_label.setText("Running… (Esc to cancel)")
		else:
			self.loader.stop()
			self.loader.hide()
			self.text_edit.setDisabled(False)
			self.hint_label.setText("Type instructions. Enter: submit • Shift+Enter: newline • Esc: cancel")


