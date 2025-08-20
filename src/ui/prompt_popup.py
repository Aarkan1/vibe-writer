from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QRectF, QRect, QEvent, QCoreApplication, QPoint, QPropertyAnimation, QEasingCurve
from utils import ConfigManager, sanitize_text_for_output
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont, QFontDatabase
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QApplication, QLabel, QToolButton, QSizePolicy, QFrame, QScrollArea, QHBoxLayout, QTextBrowser, QListWidget, QListWidgetItem, QAbstractItemView, QMenu, QInputDialog, QLineEdit, QCheckBox
from chat_db import ChatDB
import threading
from llm_helper import generate_with_llm
import re


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
		# Platform detection for font normalization (Windows vs Linux)
		import sys as _sys
		self._is_linux = _sys.platform.startswith('linux')
		self._is_windows = _sys.platform.startswith('win')
		self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
		self.setAttribute(Qt.WA_TranslucentBackground, True)
		# Ensure closing the popup never quits the whole app
		self.setAttribute(Qt.WA_QuitOnClose, False)
		self.setWindowModality(Qt.ApplicationModal)
		self.setFocusPolicy(Qt.StrongFocus)
		# Enable passive tracking so we can change the cursor near edges for resizing.
		self.setMouseTracking(True)
		# Use logical size with moderate minimum to avoid oversized popup on Windows
		min_w, min_h = 520, 650
		w, h = 500, 680
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

		# Root layout now has a left sidebar (chat list) and right content (existing UI)
		root = QHBoxLayout(self)
		root.setContentsMargins(14, 14, 14, 14)
		root.setSpacing(8)

		# --- Left Sidebar: list of chats ---
		self.sidebar = QWidget(self)
		# Sidebar opens outward: keep it visually above chat and not consuming chat stretch
		# by default. We still place it in the layout but manage window geometry when toggling
		# so the chat area width remains constant. See _animate_sidebar and _toggle_sidebar.
		side_v = QVBoxLayout(self.sidebar)
		side_v.setContentsMargins(0, 0, 0, 0)
		side_v.setSpacing(6)
		top_row = QHBoxLayout()
		top_row.setContentsMargins(0, 0, 0, 0)
		top_row.setSpacing(6)
		# Sidebar toggle icon (hamburger)
		self.sidebar_toggle_btn = QToolButton(self.sidebar)
		self.sidebar_toggle_btn.setText("☰")
		self.sidebar_toggle_btn.setToolTip("Show/Hide chats")
		self.sidebar_toggle_btn.setStyleSheet("QToolButton { color: #DDE2E7; background: transparent; border: none; font-size: 14px; padding: 0 4px; }")
		self.sidebar_toggle_btn.clicked.connect(lambda: self._toggle_sidebar())
		self.chats_label = QLabel("Chats")
		self.chats_label.setStyleSheet("color: #DDE2E7; font-size: 12px;")
		self.new_chat_btn = QToolButton(self.sidebar)
		self.new_chat_btn.setText("+ New")
		self.new_chat_btn.setStyleSheet("QToolButton { color: #B5B9C0; background: rgba(255,255,255,0.06); border: 1px solid #3A4048; border-radius: 6px; padding: 2px 6px; font-size: 11px; }")
		self.new_chat_btn.clicked.connect(lambda: self._create_new_chat())
		top_row.addWidget(self.sidebar_toggle_btn)
		top_row.addWidget(self.chats_label)
		top_row.addStretch(1)
		top_row.addWidget(self.new_chat_btn)
		side_v.addLayout(top_row)
		self.chat_list = QListWidget(self.sidebar)
		# Styling closer to ChatGPT's sidebar
		self.chat_list.setStyleSheet(
			"QListWidget { background: rgba(255,255,255,0.03); border: 1px solid #2E333B; border-radius: 8px; color: #E8EAED; font-size: 12px; }"
			" QListWidget::item { padding: 8px 10px; border-radius: 6px; }"
			" QListWidget::item:selected { background: rgba(255,255,255,0.10); }"
			" QListWidget::item:hover { background: rgba(255,255,255,0.06); }"
		)
		# Keep names on one line with ellipsis and disable horizontal scrollbars.
		try:
			self.chat_list.setTextElideMode(Qt.ElideRight)
			self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
			# Force single-line display; QListWidget respects the item's text metrics
			# We avoid word-wrapping by default, but ensure uniform row heights.
			self.chat_list.setUniformItemSizes(True)
		except Exception:
			pass
		self.chat_list.itemClicked.connect(self._on_chat_item_clicked)
		# Enable inline rename: double-click or F2
		try:
			self.chat_list.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked)
			self.chat_list.itemChanged.connect(self._on_chat_item_changed)
			# Context menu per item (right-click)
			self.chat_list.setContextMenuPolicy(Qt.CustomContextMenu)
			self.chat_list.customContextMenuRequested.connect(self._on_chat_list_context_menu)
		except Exception:
			pass
		# Keep sidebar at a fixed width when visible to avoid layout jitter.
		# We still animate open/close by temporarily relaxing the minimum width.
		self._sidebar_target_width = 160
		# Start with a fixed width so it never grows wider during content changes
		self.sidebar.setMinimumWidth(self._sidebar_target_width)
		self.sidebar.setMaximumWidth(self._sidebar_target_width)
		# Animation handle kept as a field to avoid garbage collection.
		self._sidebar_anim = None
		side_v.addWidget(self.chat_list, 1)

		# --- Right Content: existing popup UI in a container ---
		right_container = QWidget(self)
		layout = QVBoxLayout(right_container)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(8)

		self.hint_label = QLabel("Type instructions. Enter: preview • Ctrl+Enter: paste • Shift+Enter: newline • Esc: cancel")
		self.hint_label.setStyleSheet("color: #B5B9C0; font-size: 12px;")
		# Add a header row with a global sidebar toggle so it remains available
		# even when the sidebar is hidden.
		header_row = QHBoxLayout()
		header_row.setContentsMargins(0, 0, 0, 0)
		header_row.setSpacing(6)
		self.header_toggle_btn = QToolButton(right_container)
		self.header_toggle_btn.setText("☰")
		self.header_toggle_btn.setToolTip("Show/Hide chats")
		self.header_toggle_btn.setStyleSheet("QToolButton { color: #DDE2E7; background: transparent; border: none; font-size: 14px; padding: 0 4px; }")
		self.header_toggle_btn.clicked.connect(lambda: self._toggle_sidebar())
		header_row.addWidget(self.header_toggle_btn)
		header_row.addWidget(self.hint_label)
		header_row.addStretch(1)
		layout.addLayout(header_row)

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
		# Toggle to optionally include clipboard as a user message in chat
		self.clipboard_include_checkbox = QCheckBox("Include", self)
		try:
			self.clipboard_include_checkbox.setChecked(True)
		except Exception:
			pass
		try:
			self.clipboard_include_checkbox.setStyleSheet("QCheckBox { color: #DDE2E7; font-size: 12px; } QCheckBox::indicator { width: 14px; height: 14px; }")
		except Exception:
			pass
		# Header row combines accordion button and the include toggle
		self.clipboard_header_row = QHBoxLayout()
		self.clipboard_header_row.setContentsMargins(0, 0, 0, 0)
		self.clipboard_header_row.setSpacing(6)
		self.clipboard_header_row.addWidget(self.clipboard_header)
		self.clipboard_header_row.addStretch(1)
		self.clipboard_header_row.addWidget(self.clipboard_include_checkbox)
		# Do not add to layout here; we place it above the input, after messages

		self.clipboard_frame = QFrame(self)
		self.clipboard_frame.setFrameShape(QFrame.NoFrame)
		self.clipboard_frame.setStyleSheet("QFrame { background: rgba(255,255,255,0.04); border: 1px solid #3A4048; border-radius: 8px; }")
		# Target height for the accordion content (inner editor will scroll)
		self._clipboard_target_height = 140
		# Start collapsed: allow animation by controlling maximumHeight
		self.clipboard_frame.setMinimumHeight(0)
		self.clipboard_frame.setMaximumHeight(0)
		self.clipboard_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		# Animation handle to prevent GC mid-animation
		self._clipboard_anim = None
		clipboard_v = QVBoxLayout(self.clipboard_frame)
		clipboard_v.setContentsMargins(8, 8, 8, 8)
		clipboard_v.setSpacing(4)

		self.clipboard_view = QTextEdit(self.clipboard_frame)
		self.clipboard_view.setReadOnly(True)
		self.clipboard_view.setAcceptRichText(False)
		self.clipboard_view.setStyleSheet(
			"QTextEdit { background: transparent; color: #E8EAED; border: none; padding: 0px; font-size: 13px; } "
			+ self._scrollbar_qss()
		)
		# Let the inner editor expand within the fixed container and scroll as needed
		self.clipboard_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
		self.clipboard_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
		self.clipboard_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		clipboard_v.addWidget(self.clipboard_view)

		# Do not add to layout here; we place it above the input, after messages

		def _toggle_clipboard_section(checked: bool):
			# Animate open/close and rotate arrow (up when open because content sits above)
			self._animate_clipboard_section(checked, duration_ms=180)
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
		# Place clipboard preview right above the chat input (closed by default): frame above, header row below
		layout.addWidget(self.clipboard_frame)
		layout.addLayout(self.clipboard_header_row)
		# Track bubble widgets to update widths on resize
		self._message_bubbles = []

		# --- Input box at the bottom ---
		self.text_edit = QTextEdit(self)
		self.text_edit.setPlaceholderText("Write your instructions…")
		self.text_edit.setStyleSheet(
			"QTextEdit { background: transparent; color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 8px; font-size: 14px; } "
			+ self._scrollbar_qss()
		)
		self.text_edit.setAcceptRichText(False)
		self.text_edit.setTabChangesFocus(False)
		self.text_edit.setFocusPolicy(Qt.StrongFocus)
		# Keep internal scrolling off until needed; we auto-resize up to a limit
		self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
		# Intercept Enter/Escape while the editor has focus
		self.text_edit.installEventFilter(self)
		# Apply cross-platform font normalization to input and clipboard preview
		try:
			self._chat_font_small = self._build_platform_font(point_size=11)
			self._chat_font_input = self._build_platform_font(point_size=12)
			self.text_edit.setFont(self._chat_font_input)
			self.clipboard_view.setFont(self._chat_font_small)
			# Ensure the QTextDocuments also use the same base font for consistent metrics
			try:
				self.text_edit.document().setDefaultFont(self._chat_font_input)
			except Exception:
				pass
			try:
				self.clipboard_view.document().setDefaultFont(self._chat_font_small)
			except Exception:
				pass
			# Set tab width in clipboard preview to 4 spaces for consistent code alignment
			try:
				fm = self.clipboard_view.fontMetrics()
				try:
					space_w = int(fm.horizontalAdvance(' '))
				except Exception:
					space_w = int(getattr(fm, 'width', lambda s: 8)(' '))
				# Prefer Qt 5.10+ API; fall back to older API when needed
				try:
					self.clipboard_view.setTabStopDistance(float(space_w * 8))
				except Exception:
					try:
						self.clipboard_view.setTabStopWidth(int(space_w * 8))
					except Exception:
						pass
			except Exception:
				pass
		except Exception:
			pass
		layout.addWidget(self.text_edit)

		# Compose final layout: sidebar on the left, existing content on the right
		root.addWidget(self.sidebar, 0)
		root.addWidget(right_container, 1)

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

		# Track whether clipboard has ever been included as a chat message in the current chat
		self._clipboard_ever_included_in_current_chat = False

		# Build rules to suppress accidental spaces when holding hotkey chords
		# like Ctrl+Alt+Space for recording. We read the configured hotkeys and
		# if they include SPACE, we store the required modifier mask so that
		# when those modifiers are held, SPACE keypresses inside the popup text
		# editor are ignored. This prevents a flood of spaces in the textarea
		# while the user is holding the recording combo.
		self._space_suppress_mods = self._build_space_suppression_rules()

		# Keep a reference to scroll animations to prevent garbage collection.
		self._messages_scroll_anim = None

		# Enable click-drag to move the popup (frameless window)
		# We allow dragging from the background and the hint label so we don't
		# interfere with text selection inside the editors.
		self._is_dragging = False
		self._drag_offset = None
		self.hint_label.installEventFilter(self)

		# No global outside-click filter; closing is handled on deactivate only

		# --- Database state: current chat tracking ---
		self._current_chat_id = None
		self._current_chat_name = ''
		# Track which chats have already had auto-name generation triggered
		self._autoname_run_chat_ids = set()
		self._suppress_item_changed = False
		try:
			ChatDB.initialize()
			self._load_chats_and_select_latest()
		except Exception:
			pass

	def _toggle_sidebar(self):
		"""Animate the chats sidebar open/close without shrinking the chat area.

		We expand or contract the window geometry horizontally so the right content
		(chat area) keeps its width. The sidebar slides in/out to the left.
		"""
		try:
			want_show = not self.sidebar.isVisible()
			# Measure current overall geometry and right-container width
			geo = self.geometry()
			current_w = int(geo.width())
			sidebar_w = int(self._sidebar_target_width)
			# If showing, increase window width and shift x to the left by sidebar width.
			# If hiding, decrease width and shift x to the right accordingly.
			if want_show:
				new_geo = QRect(geo.x() - sidebar_w, geo.y(), current_w + sidebar_w, geo.height())
				# Pre-show to allow animation to run
				if not self.sidebar.isVisible():
					self.sidebar.setVisible(True)
					self.sidebar.setMinimumWidth(0)
					self.sidebar.setMaximumWidth(0)
				# Animate window geometry first for outward expansion
				self._animate_window_geometry(geo, new_geo, 200)
				# Then animate sidebar sliding open
				self._animate_sidebar(True, duration_ms=200)
			else:
				# First slide sidebar closed, then shrink window back in
				self._animate_sidebar(False, duration_ms=200)
				new_geo = QRect(geo.x() + sidebar_w, geo.y(), max(0, current_w - sidebar_w), geo.height())
				self._animate_window_geometry(geo, new_geo, 200)
		except Exception:
			pass

	def _animate_sidebar(self, show: bool, duration_ms: int = 200):
		"""Animate sidebar width to slide in/out.

		We animate the sidebar's maximumWidth from its current width to either 0
		(hide) or the stored target width (show). We keep the widget visible while
		animating, and only hide it at the end when closing, so the slide looks smooth.
		"""
		try:
			# Allow animation by relaxing the minimum width during the transition
			self.sidebar.setMinimumWidth(0)
			# Stop any running animation and capture current width as start.
			if self._sidebar_anim is not None:
				try:
					self._sidebar_anim.stop()
				except Exception:
					pass
			start_w = max(0, int(self.sidebar.width()))
			end_w = self._sidebar_target_width if show else 0
			# Ensure visible before expanding so animation can be seen.
			if show and not self.sidebar.isVisible():
				self.sidebar.setVisible(True)
				# Start from 0 if fully hidden
				start_w = 0
			# Configure animation on maximumWidth to play nicely with layouts.
			self._sidebar_anim = QPropertyAnimation(self.sidebar, b"maximumWidth", self)
			self._sidebar_anim.setDuration(max(0, int(duration_ms)))
			try:
				self._sidebar_anim.setEasingCurve(QEasingCurve.InOutCubic)
			except Exception:
				pass
			self._sidebar_anim.setStartValue(start_w)
			self._sidebar_anim.setEndValue(end_w)
			# When hiding completes, actually hide the widget to free space from tab focus.
			def _on_finished():
				if not show:
					# Collapsed state: keep width constraints at 0 and hide
					try:
						self.sidebar.setMaximumWidth(0)
						self.sidebar.setMinimumWidth(0)
					except Exception:
						pass
					self.sidebar.setVisible(False)
				else:
					# Expanded state: lock to a fixed width so it never stretches during loading
					try:
						self.sidebar.setMaximumWidth(self._sidebar_target_width)
						self.sidebar.setMinimumWidth(self._sidebar_target_width)
					except Exception:
						pass
				# Keep internal state clean
				self._sidebar_anim = None
			try:
				self._sidebar_anim.finished.connect(_on_finished)
			except Exception:
				# Fallback: apply final visibility immediately if signals fail
				if not show:
					self.sidebar.setVisible(False)
					try:
						self.sidebar.setMaximumWidth(0)
						self.sidebar.setMinimumWidth(0)
					except Exception:
						pass
				else:
					try:
						self.sidebar.setMaximumWidth(self._sidebar_target_width)
						self.sidebar.setMinimumWidth(self._sidebar_target_width)
					except Exception:
						pass
			self._sidebar_anim.start()
		except Exception:
			# On any failure, fallback to immediate toggle
			self.sidebar.setVisible(show)
			try:
				if show:
					self.sidebar.setMaximumWidth(self._sidebar_target_width)
					self.sidebar.setMinimumWidth(self._sidebar_target_width)
				else:
					self.sidebar.setMaximumWidth(0)
					self.sidebar.setMinimumWidth(0)
			except Exception:
				pass

	def _animate_window_geometry(self, start: QRect, end: QRect, duration_ms: int = 200):
		"""Animate the popup window geometry from start to end.

		We use QPropertyAnimation on the QWidget.geometry property for a smooth
		shift/resize so the chat area does not get squeezed when the sidebar opens.
		"""
		try:
			anim = QPropertyAnimation(self, b"geometry", self)
			anim.setDuration(max(0, int(duration_ms)))
			try:
				anim.setEasingCurve(QEasingCurve.InOutCubic)
			except Exception:
				pass
			anim.setStartValue(QRect(start))
			anim.setEndValue(QRect(end))
			# Keep a ref on self to avoid GC until finished
			self._window_geo_anim = anim
			def _clear():
				self._window_geo_anim = None
			try:
				anim.finished.connect(_clear)
			except Exception:
				self._window_geo_anim = None
			anim.start()
		except Exception:
			# Fallback immediate apply
			try:
				self.setGeometry(end)
			except Exception:
				pass

	def _animate_clipboard_section(self, show: bool, duration_ms: int = 180):
		"""Animate the clipboard preview open/close by changing maximumHeight.

		This keeps the preview in the layout so the chat area is pushed up, not overlapped.
		During the animation we keep scrolling to bottom so the newest message remains visible.
		"""
		try:
			# Stop any running animation first
			if self._clipboard_anim is not None:
				try:
					self._clipboard_anim.stop()
				except Exception:
					pass
			# Determine start/end heights
			current_h = max(0, int(self.clipboard_frame.maximumHeight()))
			start_h = current_h if (current_h > 0 or not show) else 0
			end_h = int(self._clipboard_target_height) if show else 0
			# Ensure visible before expanding so animation is visible
			if show and not self.clipboard_frame.isVisible():
				self.clipboard_frame.setVisible(True)
				self.clipboard_frame.setMinimumHeight(0)
				self.clipboard_frame.setMaximumHeight(0)
				start_h = 0
			# Configure animation on maximumHeight
			anim = QPropertyAnimation(self.clipboard_frame, b"maximumHeight", self)
			anim.setDuration(max(0, int(duration_ms)))
			try:
				anim.setEasingCurve(QEasingCurve.InOutCubic)
			except Exception:
				pass
			anim.setStartValue(int(start_h))
			anim.setEndValue(int(end_h))
			# Keep bottom anchored while size changes
			def _keep_bottom(*_args, **_kwargs):
				try:
					self._scroll_to_bottom()
				except Exception:
					pass
			try:
				anim.valueChanged.connect(lambda *_: _keep_bottom())
			except Exception:
				pass
			def _on_finished():
				try:
					if not show:
						self.clipboard_frame.setVisible(False)
						self.clipboard_frame.setMaximumHeight(0)
					else:
						self.clipboard_frame.setMaximumHeight(int(self._clipboard_target_height))
					# Final scroll to bottom to stabilize view
					self._scroll_to_bottom()
				finally:
					self._clipboard_anim = None
			try:
				anim.finished.connect(_on_finished)
			except Exception:
				# Fallback: apply final state immediately
				if not show:
					self.clipboard_frame.setVisible(False)
					self.clipboard_frame.setMaximumHeight(0)
				else:
					self.clipboard_frame.setMaximumHeight(int(self._clipboard_target_height))
				self._clipboard_anim = None
			# Start and retain reference
			self._clipboard_anim = anim
			anim.start()
		except Exception:
			# Fallback to immediate toggle
			self.clipboard_frame.setVisible(show)
			try:
				self.clipboard_frame.setMaximumHeight(int(self._clipboard_target_height if show else 0))
			except Exception:
				pass
			self._scroll_to_bottom()

	def _on_chat_list_context_menu(self, pos):
		"""Open per-item menu with Delete action on right-click."""
		try:
			global_pos = self.chat_list.mapToGlobal(pos)
			item = self.chat_list.itemAt(pos)
			if item is None:
				return
			menu = QMenu(self)
			open_action = menu.addAction("Open")
			rename_action = menu.addAction("Rename…")
			delete_action = menu.addAction("Delete")
			action = menu.exec_(global_pos)
			if action == delete_action:
				self._delete_chat_from_item(item)
			elif action == open_action:
				self._set_current_chat_from_item(item)
			elif action == rename_action:
				# Prompt simple rename dialog
				text, ok = QInputDialog.getText(self, "Rename chat", "Name:", QLineEdit.Normal, item.text())
				if ok:
					item.setText(text)
					self._on_chat_item_changed(item)
		except Exception:
			pass

	def _delete_chat_from_item(self, item: QListWidgetItem):
		"""Delete chat in DB and update the list, selecting the next available chat."""
		try:
			cid = int(item.data(Qt.UserRole))
			ChatDB.delete_chat(cid)
			row = self.chat_list.row(item)
			self.chat_list.takeItem(row)
			# Select a sensible next item
			new_row = max(0, min(row, self.chat_list.count() - 1))
			if self.chat_list.count() > 0:
				self.chat_list.setCurrentRow(new_row)
				self._set_current_chat_from_item(self.chat_list.item(new_row))
			else:
				# No chats remain; create a new empty one
				self._create_new_chat()
		except Exception:
			pass

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
		# Reload current chat history into UI (keeps selected chat)
		try:
			self._reload_current_chat_messages()
		except Exception:
			# If anything fails, at least clear in-memory history
			self._history_messages = []
		# After reloading messages, set clipboard toggle default based on history
		try:
			self._update_clipboard_toggle_default()
		except Exception:
			pass

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
		# Track if this user message is a clipboard inclusion (prefix "clipboard:") and remember for defaults
		try:
			if (clean or '').lstrip().lower().startswith('clipboard:'):
				self._clipboard_ever_included_in_current_chat = True
		except Exception:
			pass
		# Record in history first so callers can snapshot before/after as needed
		self._history_messages.append({ 'role': 'user', 'content': clean })
		# Persist to DB if a chat is selected
		try:
			if self._current_chat_id is None:
				self._ensure_chat_exists()
			ChatDB.add_message(int(self._current_chat_id), 'user', clean)
			self._refresh_chat_list_preserve_selection()
		except Exception:
			pass
		bubble = self._create_bubble(clean, is_user=True)
		self._insert_message_widget(bubble)
		# Allow UI to update before scrolling by adding a small delay
		QTimer.singleShot(100, lambda: None)
		self._scroll_to_bottom()
		# Any history change should turn off clipboard toggle
		try:
			self.set_clipboard_toggle_checked(False)
		except Exception:
			pass

	def add_assistant_message(self, text: str):
		"""Add a left-aligned assistant message bubble.

		We scroll only until the start of the assistant message aligns with the top
		of the viewport, then stop auto-scrolling.
		"""
		clean = sanitize_text_for_output(text or "")
		self._last_assistant_text = clean
		# Record in history
		self._history_messages.append({ 'role': 'assistant', 'content': self._last_assistant_text })
		# Persist to DB
		try:
			if self._current_chat_id is None:
				self._ensure_chat_exists()
			ChatDB.add_message(int(self._current_chat_id), 'assistant', clean)
			self._refresh_chat_list_preserve_selection()
		except Exception:
			pass
		container = self._create_bubble(self._last_assistant_text, is_user=False)
		self._insert_message_widget(container)
		# Trigger auto-name generation once per chat after the first assistant reply
		# Guard: only when the chat has at most two messages (first user + first assistant)
		# This ensures we never regenerate names on later turns or reopenings.
		try:
			if (self._current_chat_id is not None) and (int(self._current_chat_id) not in self._autoname_run_chat_ids):
				if len(self._history_messages) <= 2:
					self._autoname_run_chat_ids.add(int(self._current_chat_id))
					# Apply a provisional title immediately for instant feedback
					self._apply_provisional_title()
					self._maybe_generate_chat_title_async()
		except Exception:
			pass
		# Any history change should turn off clipboard toggle
		try:
			self.set_clipboard_toggle_checked(False)
		except Exception:
			pass

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
			"QTextBrowser { color: #E8EAED; font-size: 13px; border: none; background: transparent; }"
		)
		# Normalize font across platforms to keep character/space metrics consistent
		try:
			viewer.setFont(self._chat_font_small)
		except Exception:
			pass
		try:
			doc = viewer.document()
			doc.setDocumentMargin(0)
			doc.setDefaultFont(self._chat_font_small)
			doc.setDefaultStyleSheet(self._markdown_css())
		except Exception:
			pass
		try:
			self._set_markdown_with_css(viewer, "")
		except Exception:
			viewer.setPlainText("")
		bubble.setProperty('is_user', False)
		inner.addWidget(viewer)
		# Assistant bubble should occupy full row width and scale with resizing.
		bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
		row.addWidget(bubble, 1)
		# Insert before spacer
		index = max(0, self.messages_layout.count() - 1)
		self.messages_layout.insertWidget(index, container)
		# Let layout drive width; no explicit caps so it stretches to 100% of viewport
		try:
			bubble.setMinimumWidth(0)
			bubble.setMaximumWidth(16777215)
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
			self._set_markdown_with_css(viewer, self._streaming_text)
		except Exception:
			viewer.setPlainText(self._streaming_text)
		self._adjust_text_browser_height(viewer)

	def finish_streaming_assistant_message(self):
		"""Finalize the streaming message: commit to history and stop loader."""
		text = self._streaming_text or ''
		if text:
			# Record in history now that the full assistant message is available
			self._history_messages.append({ 'role': 'assistant', 'content': text })
			# Persist to DB
			try:
				if self._current_chat_id is None:
					self._ensure_chat_exists()
				ChatDB.add_message(int(self._current_chat_id), 'assistant', text)
				self._refresh_chat_list_preserve_selection()
			except Exception:
				pass
		self.set_loading(False)
		# Clear streaming refs
		self._streaming_viewer = None
		self._streaming_bubble = None
		self._streaming_container = None
		self._streaming_text = ''
		# Trigger auto-name generation once per chat (works for streaming path too)
		# Guard: only when the chat has at most two messages (first user + first assistant)
		try:
			if (self._current_chat_id is not None) and (int(self._current_chat_id) not in self._autoname_run_chat_ids):
				if len(self._history_messages) <= 2:
					self._autoname_run_chat_ids.add(int(self._current_chat_id))
					# Apply a provisional title immediately for instant feedback
					self._apply_provisional_title()
					self._maybe_generate_chat_title_async()
		except Exception:
			pass
		# Any history change should turn off clipboard toggle
		try:
			self.set_clipboard_toggle_checked(False)
		except Exception:
			pass

	def abort_streaming_assistant_message(self):
		"""Abort an in-progress streaming assistant message without saving history.

		Stops loader, removes the temporary streaming bubble, and clears streaming state.
		Used when the user cancels/closes the popup or starts a new request.
		"""
		try:
			self.set_loading(False)
		except Exception:
			pass
		container = self._streaming_container
		if container is not None:
			try:
				# Remove the container from layout and delete
				self.messages_layout.removeWidget(container)
				container.setParent(None)
			except Exception:
				pass
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

	# ------------------------- Chat persistence helpers ------------------------- #

	def _ensure_chat_exists(self):
		"""Ensure we have a current chat in the DB; create one if needed.

		Chat name defaults to first user message snippet or 'New Chat'. We keep a
		simple name for now; could be improved with better heuristics later.
		"""
		if self._current_chat_id is not None:
			return
		name = self._infer_current_chat_name() or 'New Chat'
		try:
			cid = ChatDB.create_chat(name)
			self._current_chat_id = int(cid)
			self._current_chat_name = name
			self._refresh_chat_list_preserve_selection()
		except Exception:
			pass

	def _infer_current_chat_name(self) -> str:
		"""Infer a chat name from the first user message or input text."""
		for m in self._history_messages:
			if (m.get('role') or '') == 'user':
				content = (m.get('content') or '').strip()
				if content:
					return (content.splitlines()[0] or '')[:60]
		text = (self.text_edit.toPlainText() or '').strip()
		if text:
			return (text.splitlines()[0] or '')[:60]
		return ''

	def _refresh_chat_list_preserve_selection(self):
		"""Reload chat list from DB and keep the selected chat highlighted.

		If no current chat is selected (ephemeral session), do not auto-select any row.
		This preserves the behavior of defaulting to a new chat until a message is sent.
		"""
		try:
			chats = ChatDB.list_chats()
			self.chat_list.clear()
			selected_row = None
			for idx, chat in enumerate(chats):
				name = str(chat.get('name') or '')
				item = QListWidgetItem(name)
				# Show full name on hover
				try:
					item.setToolTip(name)
				except Exception:
					pass
				# Mark editable so users can rename by clicking again or pressing F2
				try:
					item.setFlags(item.flags() | Qt.ItemIsEditable)
				except Exception:
					pass
				item.setData(Qt.UserRole, int(chat.get('id') or 0))
				self.chat_list.addItem(item)
				if (self._current_chat_id is not None) and (int(chat.get('id') or 0) == int(self._current_chat_id)):
					selected_row = idx
			# Only select if we have an explicit current chat id; otherwise keep no selection
			if selected_row is not None:
				self.chat_list.setCurrentRow(int(selected_row))
			else:
				try:
					self.chat_list.clearSelection()
				except Exception:
					pass
		except Exception:
			pass

	def _load_chats_and_select_latest(self):
		"""Initial population: load chats into the list without selecting any by default.

		We no longer auto-create or auto-select a chat on startup. The popup defaults
		to an ephemeral new chat and will only create a DB chat when the first message
		is actually sent.
		"""
		try:
			chats = ChatDB.list_chats()
			self.chat_list.clear()
			for chat in chats:
				name = str(chat.get('name') or '')
				item = QListWidgetItem(name)
				try:
					item.setToolTip(name)
				except Exception:
					pass
				try:
					item.setFlags(item.flags() | Qt.ItemIsEditable)
				except Exception:
					pass
				item.setData(Qt.UserRole, int(chat.get('id') or 0))
				self.chat_list.addItem(item)
			# Ensure no selection by default; user can pick a chat explicitly from the list
			try:
				self.chat_list.clearSelection()
			except Exception:
				pass
		except Exception:
			pass

	def start_new_ephemeral_session(self):
		"""Prepare the popup to use a fresh in-memory chat until a message is sent.

		We keep `_current_chat_id` as None so the first message sent will create and
		persist a new chat. If the user closes without sending, a later popup will
		reuse this unsaved session without creating any DB rows.
		"""
		try:
			self._current_chat_id = None
			self._current_chat_name = ''
			try:
				self.chat_list.clearSelection()
			except Exception:
				pass
			# New ephemeral session: reset clipboard include history and toggle default
			self._clipboard_ever_included_in_current_chat = False
			self.set_clipboard_toggle_checked(True)
		except Exception:
			pass

	def _set_current_chat_from_item(self, item: QListWidgetItem):
		"""Switch the UI to the selected chat and render its messages."""
		try:
			cid = int(item.data(Qt.UserRole))
			self._current_chat_id = cid
			self._current_chat_name = item.text() or ''
			self._reload_current_chat_messages()
		except Exception:
			pass

	def _reload_current_chat_messages(self):
		"""Load messages for current chat from DB into UI and in-memory history."""
		self.clear_messages()
		self._history_messages = []
		if self._current_chat_id is None:
			return
		try:
			msgs = ChatDB.get_messages(int(self._current_chat_id))
			for m in msgs:
				role = (m.get('role') or '').strip()
				content = sanitize_text_for_output(m.get('content') or '')
				if role == 'user':
					self._history_messages.append({ 'role': 'user', 'content': content })
					b = self._create_bubble(content, is_user=True)
					self._insert_message_widget(b)
				elif role == 'assistant':
					self._history_messages.append({ 'role': 'assistant', 'content': content })
					c = self._create_bubble(content, is_user=False)
					self._insert_message_widget(c)
			# After a short delay (let layouts and heights settle), smoothly scroll to bottom
			# so the latest message is fully visible even for long chats.
			QTimer.singleShot(200, lambda: self._smooth_scroll_to_bottom(200))
			# Update clipboard include toggle default based on whether chat previously included clipboard
			self._update_clipboard_toggle_default()
		except Exception:
			pass

	def _on_chat_item_clicked(self, item: QListWidgetItem):
		self._set_current_chat_from_item(item)

	def _on_chat_item_changed(self, item: QListWidgetItem):
		"""Handle inline rename from the list widget."""
		try:
			if self._suppress_item_changed:
				return
			cid = int(item.data(Qt.UserRole))
			name = (item.text() or '').strip() or 'Untitled'
			ChatDB.rename_chat(cid, name)
			if self._current_chat_id and int(self._current_chat_id) == cid:
				self._current_chat_name = name
			self._refresh_chat_list_preserve_selection()
		except Exception:
			pass

	def _maybe_generate_chat_title_async(self):
		"""Generate a concise chat title one time after first reply using LLM.

		We call the same LLM provider with a minimal instruction to craft a short
		descriptive name using the first user message and first assistant reply.
		"""
		try:
			if self._current_chat_id is None:
				return
			cid = int(self._current_chat_id)
			# Safety guard: only attempt when there are at most two chat messages
			# (the very first user + assistant pair). If more than two, skip.
			try:
				msg_count = 0
				for _m in self._history_messages:
					role = (_m.get('role') or '').strip()
					if role in ('user', 'assistant'):
						msg_count += 1
				if msg_count > 2:
					return
			except Exception:
				# On any failure, do not risk re-triggering later
				return
			# Prepare content: first user line + first assistant line
			first_user = ''
			first_assistant = ''
			for m in self._history_messages:
				role = (m.get('role') or '').strip()
				content = (m.get('content') or '').strip()
				if role == 'user' and not first_user and content:
					first_user = content
				elif role == 'assistant' and not first_assistant and content:
					first_assistant = content
				if first_user and first_assistant:
					break
			if not (first_user and first_assistant):
				return
			user_line = (first_user.splitlines()[0] or '')[:120]
			assistant_line = (first_assistant.splitlines()[0] or '')[:120]
			prompt = (
				"Create a very short, descriptive chat title (max 4 words).\n"
				"Avoid quotes and punctuation at ends.\n\n"
				f"First user message: {user_line}\n"
				f"First assistant reply: {assistant_line}\n\n"
				"Title:"
			)
			def _worker():
				try:
					name = generate_with_llm('', prompt, history_messages=[]).strip()
					# Extract a clean title if the model returns Markdown like "Title: Something"
					# We intentionally keep this logic minimal and robust. If no pattern matches,
					# fall back to the raw first line.
					try:
						m = re.search(r'^\s*title\s*:\s*(.+?)\s*$', name, re.I | re.M)
						if not m:
							# Also check inside fenced blocks if returned
							f = re.search(r'```[a-zA-Z0-9]*\s*([\s\S]*?)```', name, re.I)
							if f:
								inner = f.group(1) or ''
								m = re.search(r'^\s*title\s*:\s*(.+?)\s*$', inner, re.I | re.M)
						clean_title = (m.group(1) if m else name.splitlines()[0] if name else '').strip()
						name = clean_title
					except Exception:
						# On any parsing error, proceed with the original string
						pass
					# Sanitize and clamp
					name = sanitize_text_for_output(name).replace('\n', ' ').strip().strip('"').strip("'")
					if not name:
						return
					# Persist and refresh UI
					ChatDB.rename_chat(cid, name)
					QTimer.singleShot(0, lambda: self._apply_new_name_to_list(cid, name))
				except Exception:
					pass
			threading.Thread(target=_worker, daemon=True).start()
		except Exception:
			pass

	def _apply_new_name_to_list(self, cid: int, name: str):
		"""Apply new name to the list widget and internal state if selected."""
		try:
			row_to_select = None
			for i in range(self.chat_list.count()):
				item = self.chat_list.item(i)
				if int(item.data(Qt.UserRole) or 0) == cid:
					self._suppress_item_changed = True
					item.setText(name)
					self._suppress_item_changed = False
					row_to_select = i
					break
			if self._current_chat_id and int(self._current_chat_id) == cid:
				self._current_chat_name = name
			# Keep the current selection stable and ensure immediate UI reflect
			if row_to_select is not None:
				self.chat_list.setCurrentRow(int(row_to_select))
				try:
					self.chat_list.viewport().update()
					QCoreApplication.processEvents()
				except Exception:
					pass
			# Also refresh ordering shortly, but after the immediate UI change is visible
			QTimer.singleShot(50, lambda: self._refresh_chat_list_preserve_selection())
		except Exception:
			self._suppress_item_changed = False
			pass

	def _update_clipboard_toggle_default(self):
		"""Update the Include checkbox default per current chat history.

		Default to checked unless a prior user message starts with 'clipboard:'.
		"""
		included_before = False
		try:
			for m in self._history_messages:
				role = (m.get('role') or '').strip().lower()
				content = (m.get('content') or '').strip().lower()
				if role == 'user' and content.startswith('clipboard:'):
					included_before = True
					break
		except Exception:
			included_before = False
		self._clipboard_ever_included_in_current_chat = included_before
		self.set_clipboard_toggle_checked(False if included_before else True)

	def is_clipboard_toggle_checked(self) -> bool:
		"""Return whether the 'Include' checkbox is currently checked."""
		try:
			return bool(self.clipboard_include_checkbox.isChecked())
		except Exception:
			return False

	def set_clipboard_toggle_checked(self, checked: bool):
		"""Set the 'Include' checkbox state safely."""
		try:
			self.clipboard_include_checkbox.setChecked(bool(checked))
		except Exception:
			pass

	def _apply_provisional_title(self):
		"""Set a quick, deterministic provisional name based on first lines."""
		try:
			if self._current_chat_id is None:
				return
			cid = int(self._current_chat_id)
			first_user = ''
			first_assistant = ''
			for m in self._history_messages:
				role = (m.get('role') or '').strip()
				content = (m.get('content') or '').strip()
				if role == 'user' and not first_user and content:
					first_user = content
				elif role == 'assistant' and not first_assistant and content:
					first_assistant = content
				if first_user and first_assistant:
					break
			if not (first_user and first_assistant):
				return
			user_line = (first_user.splitlines()[0] or '')[:40]
			assistant_line = (first_assistant.splitlines()[0] or '')[:40]
			name = (user_line + ' — ' + assistant_line).strip(' —')
			name = sanitize_text_for_output(name)
			if not name:
				return
			ChatDB.rename_chat(cid, name)
			self._apply_new_name_to_list(cid, name)
		except Exception:
			pass

	def _create_new_chat(self):
		try:
			name = 'New Chat'
			cid = ChatDB.create_chat(name)
			self._current_chat_id = int(cid)
			self._current_chat_name = name
			self._refresh_chat_list_preserve_selection()
			self._reload_current_chat_messages()
		except Exception:
			pass

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
			"QTextBrowser { color: #E8EAED; font-size: 13px; border: none; background: transparent; }"
		)
		# Normalize font across platforms to keep character/space metrics consistent
		try:
			viewer.setFont(self._chat_font_small)
		except Exception:
			pass
		# Reduce default document and block margins so bubbles hug content.
		try:
			doc = viewer.document()
			doc.setDocumentMargin(0)
			doc.setDefaultFont(self._chat_font_small)
			doc.setDefaultStyleSheet(self._markdown_css())
		except Exception:
			pass
		try:
			self._set_markdown_with_css(viewer, sanitize_text_for_output(text or ""))
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
			# Assistant bubble should fill the row width and scale with resize.
			bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
			h.addWidget(bubble, 1)
		# Set bubble width constraints
		try:
			vw = self.messages_scroll.viewport().width()
			max_w = max(1, int(vw) - 4)
			if is_user:
				# Fit user bubble to content width (with padding), capped at 70% viewport
				target = self._compute_bubble_target_width(viewer, max_w)
				bubble.setMinimumWidth(target)
				bubble.setMaximumWidth(target)
			else:
				# Remove explicit width caps; let layout make it 100% width.
				bubble.setMinimumWidth(0)
				bubble.setMaximumWidth(16777215)
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

	def _build_platform_font(self, point_size: int) -> QFont:
		"""Return a QFont tuned to provide consistent spacing across platforms.

		On Windows we prefer 'Segoe UI'. On Linux we prefer 'Noto Sans' or 'Ubuntu',
		and we slightly tighten letter spacing to better match Segoe's metrics.
		"""
		try:
			db = QFontDatabase()
		except Exception:
			db = None
		# Determine preferred family per platform
		family_candidates = []
		if self._is_windows:
			family_candidates = ['Segoe UI', 'Arial Unicode MS']
		elif self._is_linux:
			family_candidates = ['Noto Sans', 'Ubuntu', 'DejaVu Sans']
		else:
			family_candidates = ['Noto Sans', 'Segoe UI', 'DejaVu Sans']
		chosen_family = ''
		for fam in family_candidates:
			try:
				if (db is None) or (fam in (db.families() if db else [])):
					chosen_family = fam
					break
			except Exception:
				# Fallback to first candidate on any error
				chosen_family = fam
				break
		font = QFont(chosen_family if chosen_family else 'Sans Serif', point_size)
		# Prefer sans-serif metrics and native fallback behavior
		try:
			font.setStyleHint(QFont.SansSerif, QFont.PreferDefault)
		except Exception:
			pass
		return font

	def _scroll_to_bottom(self):
		"""Instantly scroll to the very bottom of the messages area."""
		try:
			bar = self.messages_scroll.verticalScrollBar()
			bar.setValue(bar.maximum())
		except Exception:
			pass

	def _smooth_scroll_to_bottom(self, duration_ms: int = 200, retries: int = 2, retry_delay_ms: int = 150):
		"""Smoothly scroll to bottom using a property animation on the scrollbar value.

		If the content grows further during/after the animation (e.g., long chats as
		text layouts finalize), we retry a limited number of times to reach the true
		bottom.
		"""
		try:
			bar = self.messages_scroll.verticalScrollBar()
			start = int(bar.value())
			end = int(bar.maximum())
			if end <= start:
				return
			# Stop any running animation
			if self._messages_scroll_anim is not None:
				try:
					self._messages_scroll_anim.stop()
				except Exception:
					pass
			self._messages_scroll_anim = QPropertyAnimation(bar, b"value", self)
			self._messages_scroll_anim.setDuration(max(0, int(duration_ms)))
			try:
				self._messages_scroll_anim.setEasingCurve(QEasingCurve.InOutCubic)
			except Exception:
				pass
			self._messages_scroll_anim.setStartValue(start)
			self._messages_scroll_anim.setEndValue(end)
			def _on_finished():
				try:
					current = int(bar.value())
					new_max = int(bar.maximum())
					if retries > 0 and current < new_max:
						QTimer.singleShot(max(0, int(retry_delay_ms)),
											lambda: self._smooth_scroll_to_bottom(duration_ms, retries - 1, retry_delay_ms))
				finally:
					self._messages_scroll_anim = None
			try:
				self._messages_scroll_anim.finished.connect(_on_finished)
			except Exception:
				self._messages_scroll_anim = None
			self._messages_scroll_anim.start()
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
		"""Update existing message bubbles to keep widths in sync with viewport."""
		try:
			vw = self.messages_scroll.viewport().width()
			max_w = max(1, int(vw) - 4)
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
					# Assistant bubble fills available width; avoid explicit caps so it stretches
					bubble.setMinimumWidth(0)
					bubble.setMaximumWidth(16777215)
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

	def _markdown_css(self) -> str:
		"""Return CSS applied to rendered HTML to style markdown and code blocks.

		We keep styles dark-themed and ensure fenced code blocks are readable. When
		Pygments is available (via python-markdown's codehilite extension with
		`noclasses=True`), token colors are inlined; we still provide background,
		padding, and fonts for code containers.
		"""
		return (
			# Spacing for block elements
			"p, ul, ol, pre, h1, h2, h3, h4, h5, h6 { margin-top: 0px; margin-bottom: 6px; }"
			"p:last-child, ul:last-child, ol:last-child, pre:last-child, h1:last-child, h2:last-child, h3:last-child, h4:last-child, h5:last-child, h6:last-child { margin-bottom: 0px; }"
			# Tables
			"table { border-collapse: collapse; width: 100%; margin: 6px 0; }"
			"th, td { border: none; border-bottom: 1px solid #2E333B; padding: 6px 8px; }"
			"th { background: rgba(255,255,255,0.06); color: #E8EAED; font-weight: 600; text-align: left; }"
			"td { color: #D7DBE0; }"
			# Inline code
			"code { font-family: 'Fira Code', 'JetBrains Mono', 'Consolas', 'Menlo', 'DejaVu Sans Mono', monospace;"
			" background: rgba(255,255,255,0.06); color: #E8EAED; padding: 1px 4px; border-radius: 4px; }"
			# Do not add inline code background inside fenced blocks
			"pre code { background: transparent; padding: 0; border-radius: 0; }"
			# Pre/code blocks
			"pre { font-family: 'Fira Code', 'JetBrains Mono', 'Consolas', 'Menlo', 'DejaVu Sans Mono', monospace;"
			" background: rgba(255,255,255,0.04); color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px;"
			" padding: 8px; overflow-x: auto; }"
			# CodeHilite wrapper (Markdown extension)
			".codehilite { background: rgba(255,255,255,0.04); border: 1px solid #3A4048; border-radius: 8px; padding: 8px; }"
			".codehilite pre { margin: 0; background: transparent; }"
			".codehilite code { background: transparent; }"
		)

	def _set_markdown_with_css(self, viewer: QTextBrowser, text: str):
		"""
		Render markdown with syntax highlighting when possible; otherwise fall back
		to Qt's Markdown rendering. We always end with HTML so the document
		`defaultStyleSheet` applies.
		"""
		# Try python-markdown with codehilite (Pygments) first for syntax highlighting
		try:
			import markdown as _md  # type: ignore
			# Enable fenced code blocks and inline Pygments styles (noclasses=True)
			html = _md.markdown(
				text or "",
				extensions=[
					"fenced_code",
					"tables",
					"codehilite",
				],
				extension_configs={
					"codehilite": {
						"linenums": False,
						"guess_lang": False,
						"noclasses": True,
						"pygments_style": "monokai",
					}
				},
			)
			viewer.setHtml(html)
			return
		except Exception:
			pass
		# Fallback: use Qt's Markdown pipeline and reparse as HTML so CSS applies
		try:
			viewer.setMarkdown(text or "")
			html = viewer.toHtml()
			viewer.setHtml(html)
		except Exception:
			viewer.setPlainText(text or "")
