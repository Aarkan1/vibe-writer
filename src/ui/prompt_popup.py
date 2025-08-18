from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QRectF, QEvent
from utils import ConfigManager, sanitize_text_for_output
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QApplication, QLabel, QToolButton, QSizePolicy, QFrame, QScrollArea, QHBoxLayout, QTextBrowser


class TypingIndicatorWidget(QWidget):
	"""
	Animated three-dot typing indicator, suitable for dark backgrounds.
	"""

	def __init__(self, parent=None):
		super().__init__(parent)
		self._timer = QTimer(self)
		self._timer.timeout.connect(self._tick)
		self._phase = 0
		self._dot_color = QColor('#DDE2E7')
		self._diameter = 4
		self._spacing = 2
		w = self._diameter * 3 + self._spacing * 2
		h = self._diameter
		self.setFixedSize(w, h)
		self.setAttribute(Qt.WA_TranslucentBackground, True)

	def start(self):
		if not self._timer.isActive():
			# animation speed is 150ms
			self._timer.start(150)

	def stop(self):
		if self._timer.isActive():
			self._timer.stop()

	def _tick(self):
		self._phase = (self._phase + 1) % 3
		self.update()

	def paintEvent(self, _):
		painter = QPainter(self)
		painter.setRenderHint(QPainter.Antialiasing)
		base_alpha = 90
		bright_alpha = 220
		for i in range(3):
			alpha = bright_alpha if i == self._phase else base_alpha
			color = QColor(self._dot_color)
			color.setAlpha(alpha)
			painter.setBrush(QBrush(color))
			painter.setPen(Qt.NoPen)
			x = i * (self._diameter + self._spacing)
			painter.drawEllipse(x, 0, self._diameter, self._diameter)


class PromptPopup(QWidget):
	"""
	Centered dark popup with a text area for inline instructions.
	- Shift+Enter inserts a newline
	- Enter previews
	- Ctrl+Enter pastes
	- Esc cancels
	- Chat-style UI: messages in bubbles (you → right, assistant → left)
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
		min_w, min_h = 420, 450
		w, h = 500, 480
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

		self.hint_label = QLabel("Type instructions. Enter: preview • Ctrl+Enter: paste • Shift+Enter: newline • Esc: cancel")
		self.hint_label.setStyleSheet("color: #B5B9C0; font-size: 12px;")
		layout.addWidget(self.hint_label)

		# --- Accordion: Clipboard context preview (read-only) ---
		self.clipboard_header = QToolButton(self)
		self.clipboard_header.setText("Clipboard (read-only)")
		self.clipboard_header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
		self.clipboard_header.setCheckable(True)
		# Default closed
		self.clipboard_header.setChecked(False)
		try:
			self.clipboard_header.setArrowType(Qt.RightArrow)
		except Exception:
			pass
		self.clipboard_header.setStyleSheet(
			"QToolButton { color: #DDE2E7; background: transparent; border: none; font-size: 13px; }"
		)
		# Do not add to layout here; we place it above the input, after messages

		self.clipboard_frame = QFrame(self)
		self.clipboard_frame.setFrameShape(QFrame.NoFrame)
		self.clipboard_frame.setStyleSheet("QFrame { background: rgba(255,255,255,0.04); border: 1px solid #3A4048; border-radius: 8px; }")
		# Make the accordion content a fixed height container; inner editor scrolls
		self.clipboard_frame.setFixedHeight(140)
		self.clipboard_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		clipboard_v = QVBoxLayout(self.clipboard_frame)
		clipboard_v.setContentsMargins(8, 8, 8, 8)
		clipboard_v.setSpacing(4)

		self.clipboard_view = QTextEdit(self.clipboard_frame)
		self.clipboard_view.setReadOnly(True)
		self.clipboard_view.setAcceptRichText(False)
		self.clipboard_view.setStyleSheet(
			"QTextEdit { background: transparent; color: #E8EAED; border: none; padding: 0px; font-size: 13px; "
			"font-family: 'Segoe UI', 'Segoe UI Emoji', 'Noto Color Emoji', 'Arial Unicode MS', 'Noto Sans Symbols', 'Noto Sans', 'DejaVu Sans', sans-serif; } "
			+ self._scrollbar_qss()
		)
		# Let the inner editor expand within the fixed container and scroll as needed
		self.clipboard_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
		self.clipboard_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
		self.clipboard_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		clipboard_v.addWidget(self.clipboard_view)

		# Do not add to layout here; we place it above the input, after messages

		def _toggle_clipboard_section(checked: bool):
			# Show/hide and rotate arrow (up when open because content sits above)
			self.clipboard_frame.setVisible(checked)
			try:
				self.clipboard_header.setArrowType(Qt.UpArrow if checked else Qt.RightArrow)
			except Exception:
				pass
		# Connect after defining handler
		self.clipboard_header.toggled.connect(_toggle_clipboard_section)
		self.clipboard_frame.setVisible(False)

		# --- Chat messages area (scrollable) ---
		self.messages_scroll = QScrollArea(self)
		self.messages_scroll.setWidgetResizable(True)
		self.messages_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		# Match chat background to popup background by keeping it transparent (popup paints bg)
		self.messages_scroll.setStyleSheet(
			"QScrollArea { border: none; background: transparent; } " + self._scrollbar_qss()
		)
		self.messages_widget = QWidget(self)
		self.messages_widget.setStyleSheet("background: transparent;")
		self.messages_layout = QVBoxLayout(self.messages_widget)
		self.messages_layout.setContentsMargins(2, 2, 2, 2)
		self.messages_layout.setSpacing(8)
		# Stretch at bottom keeps messages packed to top but allows growth
		self._messages_spacer = QWidget(self.messages_widget)
		self._messages_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
		self.messages_layout.addWidget(self._messages_spacer)
		self.messages_scroll.setWidget(self.messages_widget)
		layout.addWidget(self.messages_scroll, stretch=1)
		# Typing indicator (three dots); hidden by default, positioned above the clipboard preview
		self.loader = TypingIndicatorWidget()
		self.loader.hide()
		loader_row = QHBoxLayout()
		loader_row.setContentsMargins(0, 0, 0, 0)
		loader_row.addStretch(1)
		loader_row.addWidget(self.loader, 0)
		layout.addLayout(loader_row)
		# Place clipboard preview right above the chat input (closed by default): frame above, header below
		layout.addWidget(self.clipboard_frame)
		layout.addWidget(self.clipboard_header)
		# Track bubble widgets to update widths on resize
		self._message_bubbles = []

		# --- Input box at the bottom ---
		self.text_edit = QTextEdit(self)
		self.text_edit.setPlaceholderText("Write your instructions…")
		self.text_edit.setStyleSheet(
			"QTextEdit { background: transparent; color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 8px; font-size: 14px; "
			"font-family: 'Segoe UI', 'Segoe UI Emoji', 'Noto Color Emoji', 'Arial Unicode MS', 'Noto Sans Symbols', 'Noto Sans', 'DejaVu Sans', sans-serif; } "
			+ self._scrollbar_qss()
		)
		self.text_edit.setAcceptRichText(False)
		self.text_edit.setTabChangesFocus(False)
		self.text_edit.setFocusPolicy(Qt.StrongFocus)
		# Keep internal scrolling off until needed; we auto-resize up to a limit
		self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
		# Intercept Enter/Escape while the editor has focus
		self.text_edit.installEventFilter(self)
		layout.addWidget(self.text_edit)

		# --- Auto-resize the input: 1-line tall by default, expand with content ---
		self._input_min_height = 0
		self._input_max_height = 0
		self._input_extra_padding = 0
		self._init_input_auto_resize()

		# Loader row already placed above the clipboard preview

		# Track last assistant message for Ctrl+Enter paste
		self._last_assistant_text = ""
		# Track chat history for LLM calls. We store a list of
		# dicts like { 'role': 'user'|'assistant', 'content': str }.
		# This lets us include past turns in the completion request.
		self._history_messages = []
		# Streaming state for assistant message (live updates)
		self._streaming_viewer = None
		self._streaming_bubble = None
		self._streaming_container = None
		self._streaming_text = ''

		# Build rules to suppress accidental spaces when holding hotkey chords
		# like Ctrl+Alt+Space for recording. We read the configured hotkeys and
		# if they include SPACE, we store the required modifier mask so that
		# when those modifiers are held, SPACE keypresses inside the popup text
		# editor are ignored. This prevents a flood of spaces in the textarea
		# while the user is holding the recording combo.
		self._space_suppress_mods = self._build_space_suppression_rules()

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
		self.clear_messages()
		self._last_assistant_text = ""
		self._streaming_viewer = None
		self._streaming_bubble = None
		self._streaming_container = None
		self._streaming_text = ''
		# Also clear stored chat history
		self._history_messages = []

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
		# Refresh clipboard preview on open
		try:
			cb_text = QApplication.clipboard().text() or ""
			# Sanitize clipboard preview to avoid mojibake in the read-only area
			cb_text = sanitize_text_for_output(cb_text)
			self.clipboard_view.setPlainText(cb_text)
			# Move cursor to start for readability
			cur = self.clipboard_view.textCursor()
			cur.movePosition(cur.Start)
			self.clipboard_view.setTextCursor(cur)
		except Exception:
			pass
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
		# Enter: preview; Shift+Enter: newline; Ctrl+Enter: paste; Esc: cancel
		if event.key() in (Qt.Key_Return, Qt.Key_Enter):
			mods = event.modifiers()
			if mods & Qt.ShiftModifier:
				# Insert newline
				self.text_edit.insertPlainText("\n")
				return
			# Ctrl+Enter: submit/paste
			if mods & Qt.ControlModifier:
				text = self.text_edit.toPlainText().strip()
				self.submitted.emit(text)
				return
			# Ignore Alt/Meta modified Enter
			if mods & (Qt.AltModifier | Qt.MetaModifier):
				return
			# Preview for plain Enter
			text = self.text_edit.toPlainText().strip()
			self.preview_requested.emit(text)
			return
		elif event.key() == Qt.Key_Escape:
			self.cancelled.emit()
			return
		# Default handling (e.g., typing)
		super().keyPressEvent(event)

	def eventFilter(self, obj, event):
		# Ensure plain Enter previews even when QTextEdit has focus
		if obj is self.text_edit and event.type() == QEvent.KeyPress:
			key = event.key()
			mods = event.modifiers()
			# Suppress SPACE while the configured recording chords that use SPACE
			# are held (e.g., Ctrl+Alt+Space). This avoids inserting spaces into
			# the popup text area while starting/holding the recording hotkey.
			if key == Qt.Key_Space:
				for required in self._space_suppress_mods:
					if required is not None and (mods & required) == required:
						return True
			if key in (Qt.Key_Return, Qt.Key_Enter):
				if mods & Qt.ShiftModifier:
					self.text_edit.insertPlainText("\n")
					return True
				# Ctrl+Enter → submit/paste
				if mods & Qt.ControlModifier:
					text = self.text_edit.toPlainText().strip()
					self.submitted.emit(text)
					return True
				# Ignore Alt/Meta modified Enter (no action)
				if mods & (Qt.AltModifier | Qt.MetaModifier):
					return True
				# Plain Enter → preview
				text = self.text_edit.toPlainText().strip()
				self.preview_requested.emit(text)
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

	def resizeEvent(self, event):
		# Ensure input height and bubble widths are recalculated when the popup resizes
		super().resizeEvent(event)
		try:
			self._recalculate_input_height()
		except Exception:
			pass
		try:
			self._update_bubble_widths()
		except Exception:
			pass

	def _build_space_suppression_rules(self):
		"""Compute modifier masks for any configured hotkeys that use SPACE.

		We only suppress SPACE when these modifiers are held, so normal typing of
		plain spaces still works. Handles both 'activation_key' and
		'prompt_activation_key'.
		"""
		try:
			ak = ConfigManager.get_config_value('recording_options', 'activation_key') or ''
			pak = ConfigManager.get_config_value('recording_options', 'prompt_activation_key') or ''
		except Exception:
			ak, pak = '', ''
		mods = []
		m1 = self._parse_space_combo_to_qt_mods(ak)
		if m1 is not None:
			mods.append(m1)
		m2 = self._parse_space_combo_to_qt_mods(pak)
		if m2 is not None:
			mods.append(m2)
		return mods

	def _parse_space_combo_to_qt_mods(self, combo: str):
		"""Return Qt modifier mask if combo includes SPACE; otherwise None.

		Recognizes CTRL, ALT, SHIFT, META (and CMD, WIN as META alias).
		"""
		if not combo:
			return None
		tokens = [t.strip().upper() for t in combo.split('+') if t.strip()]
		if 'SPACE' not in tokens:
			return None
		required = 0
		if 'CTRL' in tokens:
			required |= Qt.ControlModifier
		if 'ALT' in tokens:
			required |= Qt.AltModifier
		if 'SHIFT' in tokens:
			required |= Qt.ShiftModifier
		if 'META' in tokens or 'CMD' in tokens or 'WIN' in tokens:
			required |= Qt.MetaModifier
		return required

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
			# Do not drag while interacting with messages area
			if hasattr(self, 'messages_widget') and self.messages_widget.isAncestorOf(child):
				return False
			# Do not drag when interacting with the clipboard accordion or its contents
			if hasattr(self, 'clipboard_view') and self.clipboard_view.isAncestorOf(child):
				return False
			if hasattr(self, 'clipboard_frame') and self.clipboard_frame.isAncestorOf(child):
				return False
			if hasattr(self, 'clipboard_header') and self.clipboard_header.isAncestorOf(child):
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
			self.hint_label.setText("Type instructions. Enter: preview • Ctrl+Enter: paste • Shift+Enter: newline • Esc: cancel")

	# ------------------------- Input auto-resize helpers ------------------------- #

	def _init_input_auto_resize(self):
		"""Initialize 1-line input height and connect signals to grow with content.

		We compute a base single-line height from the current font metrics and add
		a small padding to account for style padding/frame. The input grows with its
		document height up to a max number of rows; beyond that, it scrolls.
		"""
		try:
			fm = self.text_edit.fontMetrics()
			line_h = max(1, fm.lineSpacing())
			# Style padding is 8px top/bottom; add frame width and a small fudge
			frame = max(0, int(self.text_edit.frameWidth())) * 2
			self._input_extra_padding = 16 + frame + 4
			self._input_min_height = line_h + self._input_extra_padding
			# Allow auto-growth up to ~6 lines by default
			self._input_max_height = (line_h * 6) + self._input_extra_padding
			self.text_edit.setFixedHeight(self._input_min_height)
			# Recalculate on text/document size and when the viewport width changes
			self.text_edit.textChanged.connect(self._recalculate_input_height)
			try:
				layout = self.text_edit.document().documentLayout()
				layout.documentSizeChanged.connect(lambda _=None: self._recalculate_input_height())
			except Exception:
				pass
		except Exception:
			pass
		# Initial calculation
		self._recalculate_input_height()

	def _recalculate_input_height(self):
		"""Resize the input field height to fit content up to a multi-line cap."""
		try:
			doc = self.text_edit.document()
			# Ensure wrapping width matches current viewport width
			doc.setTextWidth(self.text_edit.viewport().width())
			doc_h = int(doc.size().height())
			target = max(self._input_min_height, min(self._input_max_height, doc_h + self._input_extra_padding))
			if self.text_edit.height() != target:
				self.text_edit.setFixedHeight(target)
				# Keep the latest text visible when we grow
				try:
					cursor = self.text_edit.textCursor()
					self.text_edit.setTextCursor(cursor)
				except Exception:
					pass
		except Exception:
			pass

	def add_user_message(self, text: str):
		"""Add a right-aligned user message bubble to the chat and scroll to bottom."""
		# Sanitize to avoid mojibake (e.g., U+202F shown as â¯) before storing/rendering
		clean = sanitize_text_for_output(text or "")
		# Record in history first so callers can snapshot before/after as needed
		self._history_messages.append({ 'role': 'user', 'content': clean })
		bubble = self._create_bubble(clean, is_user=True)
		self._insert_message_widget(bubble)
		# Allow UI to update before scrolling by adding a small delay
		QTimer.singleShot(100, lambda: None)
		self._scroll_to_bottom()

	def add_assistant_message(self, text: str):
		"""Add a left-aligned assistant message bubble.

		We scroll only until the start of the assistant message aligns with the top
		of the viewport, then stop auto-scrolling.
		"""
		clean = sanitize_text_for_output(text or "")
		self._last_assistant_text = clean
		# Record in history
		self._history_messages.append({ 'role': 'assistant', 'content': self._last_assistant_text })
		container = self._create_bubble(self._last_assistant_text, is_user=False)
		self._insert_message_widget(container)

	def begin_streaming_assistant_message(self):
		"""Create an empty assistant bubble and prepare to append streamed text."""
		# If a previous streaming session exists, finish it first
		if self._streaming_viewer is not None:
			try:
				self.finish_streaming_assistant_message()
			except Exception:
				pass
		self._streaming_text = ''
		container = QWidget(self.messages_widget)
		row = QHBoxLayout(container)
		row.setContentsMargins(0, 0, 0, 0)
		row.setSpacing(0)
		bubble = QFrame(container)
		bubble.setFrameShape(QFrame.NoFrame)
		bubble.setStyleSheet("QFrame { background: transparent; border: none; }")
		inner = QVBoxLayout(bubble)
		inner.setContentsMargins(8, 8, 8, 8)
		inner.setSpacing(4)
		viewer = QTextBrowser(bubble)
		viewer.setOpenExternalLinks(False)
		viewer.setOpenLinks(False)
		viewer.setReadOnly(True)
		viewer.setFrameShape(QFrame.NoFrame)
		viewer.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		viewer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		viewer.setFocusPolicy(Qt.NoFocus)
		viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		viewer.setMinimumHeight(1)
		viewer.setStyleSheet(
			"QTextBrowser { color: #E8EAED; font-size: 13px; border: none; background: transparent; "
			"font-family: 'Segoe UI', 'Segoe UI Emoji', 'Noto Color Emoji', 'Arial Unicode MS', 'Noto Sans Symbols', 'Noto Sans', 'DejaVu Sans', sans-serif; }"
		)
		try:
			doc = viewer.document()
			doc.setDocumentMargin(0)
			doc.setDefaultStyleSheet(
				"p, ul, ol, pre, h1, h2, h3, h4, h5, h6 { margin-top: 0px; margin-bottom: 6px; }"
				"p:last-child, ul:last-child, ol:last-child, pre:last-child, h1:last-child, h2:last-child, h3:last-child, h4:last-child, h5:last-child, h6:last-child { margin-bottom: 0px; }"
			)
		except Exception:
			pass
		try:
			viewer.setMarkdown("")
		except Exception:
			viewer.setPlainText("")
		bubble.setProperty('is_user', False)
		inner.addWidget(viewer)
		row.addWidget(bubble, 0)
		row.addStretch(1)
		# Insert before spacer
		index = max(0, self.messages_layout.count() - 1)
		self.messages_layout.insertWidget(index, container)
		# Width cap like normal assistant bubbles
		try:
			vw = self.messages_scroll.viewport().width()
			max_w = int(vw * 0.7)
			# Fix assistant bubble width to a fraction of viewport so it resizes with popup
			bubble.setMinimumWidth(max_w)
			bubble.setMaximumWidth(max_w)
		except Exception:
			pass
		# Initial height
		self._adjust_text_browser_height(viewer)
		# Track
		self._streaming_viewer = viewer
		self._streaming_bubble = bubble
		self._streaming_container = container
		self._message_bubbles.append(bubble)
		# Align the start of this assistant message to the top of the viewport once
		QTimer.singleShot(100, lambda: None)
		self._scroll_assistant_container_to_top(container)

	def append_streaming_assistant_delta(self, delta_text: str):
		"""Append streamed text to the live assistant bubble and resize."""
		if not delta_text:
			return
		# Sanitize each delta to avoid accumulating problematic sequences
		delta_text = sanitize_text_for_output(delta_text)
		self._streaming_text = (self._streaming_text or '') + delta_text
		self._last_assistant_text = self._streaming_text
		viewer = self._streaming_viewer
		if viewer is None:
			return
		try:
			viewer.setMarkdown(self._streaming_text)
		except Exception:
			viewer.setPlainText(self._streaming_text)
		self._adjust_text_browser_height(viewer)

	def finish_streaming_assistant_message(self):
		"""Finalize the streaming message: commit to history and stop loader."""
		text = self._streaming_text or ''
		if text:
			# Record in history now that the full assistant message is available
			self._history_messages.append({ 'role': 'assistant', 'content': text })
		self.set_loading(False)
		# Clear streaming refs
		self._streaming_viewer = None
		self._streaming_bubble = None
		self._streaming_container = None
		self._streaming_text = ''

	def get_last_assistant_text(self) -> str:
		"""Return the most recent assistant message text for paste action."""
		return self._last_assistant_text or ""

	def clear_messages(self):
		"""Remove all message bubbles from the chat area, keeping the spacer."""
		# Remove all items except the spacer at the end
		count = self.messages_layout.count()
		for i in reversed(range(count)):
			item = self.messages_layout.itemAt(i)
			w = item.widget()
			if w is self._messages_spacer:
				continue
			if w is not None:
				self.messages_layout.removeWidget(w)
				w.setParent(None)
		# Do not clear history here; reset() controls history lifecycle

	def _insert_message_widget(self, w: QWidget):
		# Insert before the spacer so spacer remains last
		index = max(0, self.messages_layout.count() - 1)
		self.messages_layout.insertWidget(index, w)

	def get_chat_history_messages(self):
		"""Return a shallow copy of chat history as a list of {role, content}.

		The history includes messages added via add_user_message/add_assistant_message
		in the order they were added during this popup session.
		"""
		return list(self._history_messages)

	def _create_bubble(self, text: str, is_user: bool) -> QWidget:
		container = QWidget(self.messages_widget)
		h = QHBoxLayout(container)
		h.setContentsMargins(0, 0, 0, 0)
		h.setSpacing(0)
		bubble = QFrame(container)
		bubble.setFrameShape(QFrame.NoFrame)
		# Bubble styling:
		# - Right (user) bubbles keep subtle background + border for emphasis.
		# - Left (assistant) bubbles have no background (transparent) and no border.
		#   This makes assistant messages appear as plain text on the popup background.
		if is_user:
			# Match clipboard read-only area styling for user bubble
			bubble.setStyleSheet("QFrame { background: rgba(255,255,255,0.04); border: none; border-radius: 8px; }")
		else:
			# Assistant bubble: no visual container
			bubble.setStyleSheet("QFrame { background: transparent; border: none; }")
		inner = QVBoxLayout(bubble)
		inner.setContentsMargins(8, 8, 8, 8)
		inner.setSpacing(4)
		# Use QTextBrowser to render Markdown content in chat bubbles.
		# We keep it read-only and style it to look like a plain label.
		viewer = QTextBrowser(bubble)
		viewer.setOpenExternalLinks(False)
		viewer.setOpenLinks(False)
		viewer.setReadOnly(True)
		viewer.setFrameShape(QFrame.NoFrame)
		viewer.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		viewer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		viewer.setFocusPolicy(Qt.NoFocus)
		# Prevent vertical stretching; we will control height explicitly.
		viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		viewer.setMinimumHeight(1)
		# Match label/clipboard styling
		viewer.setStyleSheet(
			"QTextBrowser { color: #E8EAED; font-size: 13px; border: none; background: transparent; "
			"font-family: 'Segoe UI', 'Segoe UI Emoji', 'Noto Color Emoji', 'Arial Unicode MS', 'Noto Sans Symbols', 'Noto Sans', 'DejaVu Sans', sans-serif; }"
		)
		# Reduce default document and block margins so bubbles hug content.
		try:
			doc = viewer.document()
			doc.setDocumentMargin(0)
			doc.setDefaultStyleSheet(
				"p, ul, ol, pre, h1, h2, h3, h4, h5, h6 { margin-top: 0px; margin-bottom: 6px; }"
				"p:last-child, ul:last-child, ol:last-child, pre:last-child, h1:last-child, h2:last-child, h3:last-child, h4:last-child, h5:last-child, h6:last-child { margin-bottom: 0px; }"
			)
		except Exception:
			pass
		# Prefer native Qt Markdown rendering (Qt 5.14+). Fallback to plain text.
		try:
			viewer.setMarkdown(sanitize_text_for_output(text or ""))
		except Exception:
			viewer.setPlainText(sanitize_text_for_output(text or ""))
		# Mark bubble type for later width updates
		bubble.setProperty('is_user', is_user)
		# Initial sizing before layout paint to avoid oversized first render
		self._adjust_text_browser_height(viewer)
		inner.addWidget(viewer)
		if is_user:
			h.addStretch(1)
			h.addWidget(bubble, 0)
		else:
			h.addWidget(bubble, 0)
			h.addStretch(1)
		# Set bubble width constraints
		try:
			vw = self.messages_scroll.viewport().width()
			max_w = int(vw * 0.7)
			if is_user:
				# Fit user bubble to content width (with padding), capped at 70% viewport
				target = self._compute_bubble_target_width(viewer, max_w)
				bubble.setMinimumWidth(target)
				bubble.setMaximumWidth(target)
			else:
				# Assistant bubble: fix width to a fraction of viewport so it tracks window size
				bubble.setMinimumWidth(max_w)
				bubble.setMaximumWidth(max_w)
		except Exception:
			pass
		# Recalculate after width constraint for an accurate height
		self._adjust_text_browser_height(viewer)
		# Auto-size the text viewer height to its document contents so bubbles expand naturally.
		try:
			layout = viewer.document().documentLayout()
			layout.documentSizeChanged.connect(lambda _=None, v=viewer: self._adjust_text_browser_height(v))
			# Initial sizing after it is laid out
			QTimer.singleShot(0, lambda v=viewer: self._adjust_text_browser_height(v))
		except Exception:
			pass
		# Track for future resize adjustments
		self._message_bubbles.append(bubble)
		return container

	def _scroll_to_bottom(self):
		"""Scroll to the very bottom of the messages area with extra margin."""
		try:
			bar = self.messages_scroll.verticalScrollBar()
			# Use maximum plus some extra to ensure we really hit the bottom
			extra_margin = 200  # Add extra pixels to ensure complete scroll
			bar.setValue(bar.maximum() + extra_margin)
		except Exception:
			pass

	def _scroll_assistant_container_to_top(self, container: QWidget):
		"""Scroll so the assistant message container's top aligns with viewport top.

		We do this only once per assistant message to avoid continuous auto-scrolling
		during streaming. User messages still force scroll-to-bottom for chat UX.
		"""
		def _align():
			try:
				bar = self.messages_scroll.verticalScrollBar()
				# y-position within the scroll content widget
				top = max(bar.minimum(), min(bar.maximum(), max(0, container.pos().y())))
				# Use maximum plus some extra to ensure we really hit the bottom
				extra_margin = 100  # Add extra pixels to ensure complete scroll
				top = max(top, bar.maximum() + extra_margin)
				bar.setValue(top)
			except Exception:
				pass
		# Defer to next cycle so layouts are up-to-date
		QTimer.singleShot(0, _align)

	def _update_bubble_widths(self):
		"""Update existing message bubbles to keep width at 70% of chat viewport."""
		try:
			vw = self.messages_scroll.viewport().width()
			max_w = int(vw * 0.7)
			for bubble in list(self._message_bubbles):
				if bubble is None or bubble.parent() is None:
					continue
				viewer = None
				try:
					viewer = bubble.findChild(QTextBrowser)
				except Exception:
					viewer = None
				if bool(bubble.property('is_user')) and viewer is not None:
					# Recompute content-fit width for user bubble with current cap
					target = self._compute_bubble_target_width(viewer, max_w)
					bubble.setMinimumWidth(target)
					bubble.setMaximumWidth(target)
					self._adjust_text_browser_height(viewer)
				else:
					# Assistant bubble tracks popup width: fix to fraction of viewport
					bubble.setMinimumWidth(max_w)
					bubble.setMaximumWidth(max_w)
					if viewer is not None:
						self._adjust_text_browser_height(viewer)
		except Exception:
			pass

	def _adjust_text_browser_height(self, text_browser: QTextBrowser):
		"""Resize a QTextBrowser's height to fit its document contents.

		This keeps chat bubbles sized to their content with no inner scrollbars.
		"""
		try:
			# Constrain layout to current viewport width so wrapping is correct
			doc = text_browser.document()
			doc.setTextWidth(text_browser.viewport().width())
			doc_h = int(doc.size().height())
			# Add a small fudge factor to avoid clipping descenders
			new_h = max(1, doc_h + 2)
			if text_browser.height() != new_h:
				text_browser.setFixedHeight(new_h)
		except Exception:
			pass

	def _compute_bubble_target_width(self, viewer: QTextBrowser, max_width: int) -> int:
		"""Return desired bubble width for a user message: content width + padding, capped.

		We measure the ideal document width without wrapping, then add padding to fit
		the inner layout margins. Finally, we cap the result to the provided max width.
		For short messages, we ensure a reasonable minimum width to avoid clipping.
		"""
		try:
			doc = viewer.document()
			# Measure ideal width without wrapping
			old_width = doc.textWidth()
			doc.setTextWidth(-1)
			ideal = int(doc.idealWidth())
			# Restore previous text width (height calc will set it later as needed)
			doc.setTextWidth(old_width)
			# Inner layout horizontal margins = 8 + 8; add small fudge for border
			padding = 16 + 2
			# Add extra padding for short messages to prevent clipping
			target_width = ideal + padding
			# Set a reasonable minimum width (100px) to avoid overly narrow bubbles
			min_width = 100
			return max(min_width, min(max_width, target_width))
		except Exception:
			# Fallback to cap if anything goes wrong
			return max(100, max_width)

	def _scrollbar_qss(self) -> str:
		"""Return QSS rules for scrollbars that match the popup's dark theme.

		This string is appended to widget-specific stylesheets so it only affects
		the scrollbars within those widgets (QTextEdit, QScrollArea) inside the popup.
		"""
		return (
			"QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }"
			"QScrollBar::handle:vertical { background: rgba(255,255,255,0.16); min-height: 24px; border-radius: 5px; }"
			"QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.26); }"
			"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
			"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
			"QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }"
			"QScrollBar::handle:horizontal { background: rgba(255,255,255,0.16); min-width: 24px; border-radius: 5px; }"
			"QScrollBar::handle:horizontal:hover { background: rgba(255,255,255,0.26); }"
			"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }"
			"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
		)


