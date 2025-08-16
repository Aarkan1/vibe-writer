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
	preview_requested = pyqtSignal(str)
	cancelled = pyqtSignal()

	def __init__(self, parent=None):
		super().__init__(parent)
		self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
		self.setAttribute(Qt.WA_TranslucentBackground, True)
		# Ensure closing the popup never quits the whole app
		self.setAttribute(Qt.WA_QuitOnClose, False)
		self.setWindowModality(Qt.ApplicationModal)
		self.setFocusPolicy(Qt.StrongFocus)
		# Enable passive tracking so we can change the cursor near edges for resizing.
		self.setMouseTracking(True)
		# Use logical size with moderate minimum to avoid oversized popup on Windows
		min_w, min_h = 420, 250
		w, h = 500, 280
		self.setMinimumSize(min_w, min_h)
		# Initial size only; allow user resizing afterward.
		self.resize(max(w, min_w), max(h, min_h))

		# --- Custom resize state (needed because window is frameless) ---
		# We implement a simple 8px resize border around the window. We only
		# start a resize when the pointer is inside this border area.
		self._RESIZE_MARGIN = 8
		self._is_resizing = False
		self._resize_left = False
		self._resize_right = False
		self._resize_top = False
		self._resize_bottom = False
		self._resize_start_geo = None
		self._resize_start_mouse = None

		layout = QVBoxLayout(self)
		layout.setContentsMargins(14, 14, 14, 14)
		layout.setSpacing(8)

		self.hint_label = QLabel("Type instructions. Enter: submit • Ctrl+Enter: preview • Shift+Enter: newline • Esc: cancel")
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

		# Read-only result area shown for Ctrl+Enter previews
		self.result_view = QTextEdit(self)
		self.result_view.setReadOnly(True)
		self.result_view.setStyleSheet(
			"QTextEdit { background: rgba(255,255,255,0.04); color: #DDE2E7; border: 1px solid #3A4048; border-radius: 8px; padding: 8px; font-size: 14px; }"
		)
		self.result_view.hide()
		layout.addWidget(self.result_view, stretch=1)

		# Enable click-drag to move the popup (frameless window)
		# We allow dragging from the background and the hint label so we don't
		# interfere with text selection inside the editors.
		self._is_dragging = False
		self._drag_offset = None
		self.hint_label.installEventFilter(self)

		# No global outside-click filter; closing is handled on deactivate only

	def reset(self):
		"""Clear input and reset UI state so popup opens empty and ready."""
		self.set_loading(False)
		self.text_edit.clear()
		self.result_view.clear()
		self.result_view.hide()

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
		# Closing will be handled when the window deactivates

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
			# Ctrl+Enter: preview result inside popup
			if mods & Qt.ControlModifier:
				text = self.text_edit.toPlainText().strip()
				self.preview_requested.emit(text)
				return
			# Ignore Alt/Meta modified Enter
			if mods & (Qt.AltModifier | Qt.MetaModifier):
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
				# Ctrl+Enter → preview
				if mods & Qt.ControlModifier:
					text = self.text_edit.toPlainText().strip()
					self.preview_requested.emit(text)
					return True
				# Ignore Alt/Meta modified Enter (no submit)
				if mods & (Qt.AltModifier | Qt.MetaModifier):
					return True
				text = self.text_edit.toPlainText().strip()
				self.submitted.emit(text)
				return True
			if key == Qt.Key_Escape:
				self.cancelled.emit()
				return True
		# Handle drag-to-move from the hint label (safe; does not conflict with typing)
		if obj is self.hint_label:
			if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
				self._begin_drag(event.globalPos())
				return True
			elif event.type() == QEvent.MouseMove and self._is_dragging and (event.buttons() & Qt.LeftButton):
				self._perform_drag(event.globalPos())
				return True
			elif event.type() == QEvent.MouseButtonRelease and self._is_dragging:
				self._end_drag()
				return True
		return super().eventFilter(obj, event)

	def event(self, event):
		# Close when the window deactivates (user clicks to another app / window)
		if event.type() == QEvent.WindowDeactivate:
			self.cancelled.emit()
		return super().event(event)

	def closeEvent(self, event):
		super().closeEvent(event)

	# No outside-click filter methods needed

	def mousePressEvent(self, event):
		# Start resize if the cursor is on a resize border.
		if event.button() == Qt.LeftButton:
			edges = self._get_resize_edges(event.pos())
			if any(edges):
				self._begin_resize(event.globalPos(), edges)
				return
			# Otherwise allow dragging from background areas (outside editors)
			if self._pos_in_draggable_region(event.pos()):
				self._begin_drag(event.globalPos())
				return
		super().mousePressEvent(event)

	def mouseMoveEvent(self, event):
		# While resizing, update geometry.
		if self._is_resizing and (event.buttons() & Qt.LeftButton):
			self._perform_resize(event.globalPos())
			return
		# While dragging, move the window.
		if self._is_dragging and (event.buttons() & Qt.LeftButton):
			self._perform_drag(event.globalPos())
			return
		# Update cursor when hovering near edges (only when not resizing/dragging)
		self._update_cursor_for_pos(event.pos())
		super().mouseMoveEvent(event)

	def mouseReleaseEvent(self, event):
		if self._is_resizing:
			self._end_resize()
			return
		if self._is_dragging:
			self._end_drag()
			return
		super().mouseReleaseEvent(event)

	def _pos_in_draggable_region(self, pos):
		"""Return True if position is not over the editors (so we don't block selection)."""
		child = self.childAt(pos)
		if child is None:
			return True
		try:
			if self.text_edit.isAncestorOf(child):
				return False
			if self.result_view.isAncestorOf(child):
				return False
		except Exception:
			pass
		return True

	def _begin_drag(self, global_pos):
		self._is_dragging = True
		try:
			self._drag_offset = global_pos - self.frameGeometry().topLeft()
		except Exception:
			self._drag_offset = None

	def _perform_drag(self, global_pos):
		if not self._is_dragging:
			return
		if self._drag_offset is None:
			self.move(global_pos)
			return
		self.move(global_pos - self._drag_offset)

	def _end_drag(self):
		self._is_dragging = False
		self._drag_offset = None

	def _get_resize_edges(self, pos):
		"""Return a tuple of booleans (left, right, top, bottom) if pos is near edges.
		We use a fixed margin so the user can grab the frameless window borders.
		"""
		rect = self.rect()
		m = self._RESIZE_MARGIN
		left = pos.x() <= rect.left() + m
		right = pos.x() >= rect.right() - m
		top = pos.y() <= rect.top() + m
		bottom = pos.y() >= rect.bottom() - m
		return (left, right, top, bottom)

	def _update_cursor_for_pos(self, pos):
		"""Update the cursor shape to indicate resizable edges/corners."""
		left, right, top, bottom = self._get_resize_edges(pos)
		if (left and top) or (right and bottom):
			self.setCursor(Qt.SizeFDiagCursor)
		elif (right and top) or (left and bottom):
			self.setCursor(Qt.SizeBDiagCursor)
		elif left or right:
			self.setCursor(Qt.SizeHorCursor)
		elif top or bottom:
			self.setCursor(Qt.SizeVerCursor)
		else:
			self.unsetCursor()

	def _begin_resize(self, global_pos, edges):
		self._is_resizing = True
		self._resize_left, self._resize_right, self._resize_top, self._resize_bottom = edges
		self._resize_start_mouse = global_pos
		self._resize_start_geo = self.geometry()

	def _perform_resize(self, global_pos):
		if not self._is_resizing:
			return
		delta = global_pos - self._resize_start_mouse
		geo = self._resize_start_geo
		new_x = geo.x()
		new_y = geo.y()
		new_w = geo.width()
		new_h = geo.height()

		if self._resize_left:
			new_x = geo.x() + delta.x()
			new_w = geo.width() - delta.x()
		if self._resize_right:
			new_w = geo.width() + delta.x()
		if self._resize_top:
			new_y = geo.y() + delta.y()
			new_h = geo.height() - delta.y()
		if self._resize_bottom:
			new_h = geo.height() + delta.y()

		# Clamp to minimum size to avoid inverting
		min_w = self.minimumWidth()
		min_h = self.minimumHeight()
		if new_w < min_w:
			# Adjust x when clamping left resize so the right edge stays put
			if self._resize_left:
				new_x += (new_w - min_w)
			new_w = min_w
		if new_h < min_h:
			if self._resize_top:
				new_y += (new_h - min_h)
			new_h = min_h

		self.setGeometry(new_x, new_y, new_w, new_h)

	def _end_resize(self):
		self._is_resizing = False
		self._resize_left = self._resize_right = False
		self._resize_top = self._resize_bottom = False
		self._resize_start_mouse = None
		self._resize_start_geo = None

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
			self.hint_label.setText("Type instructions. Enter: submit • Ctrl+Enter: preview • Shift+Enter: newline • Esc: cancel")

	def set_result_text(self, text: str):
		"""Show the result text in the read-only area below the input."""
		self.result_view.setPlainText(text or "")
		self.result_view.show()
		# Move cursor to start for readability
		try:
			cursor = self.result_view.textCursor()
			cursor.movePosition(cursor.Start)
			self.result_view.setTextCursor(cursor)
		except Exception:
			pass


