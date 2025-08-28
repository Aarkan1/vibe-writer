from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QToolButton, QMenu

from notes_db import NotesDB


class NotesSidebar(QWidget):
	"""Left sidebar for Notes: search, list, and new note.

	Signals:
	- note_selected(int)
	- new_note_created(int)
	"""

	note_selected = pyqtSignal(int)
	new_note_created = pyqtSignal(int)

	def __init__(self, parent=None):
		super().__init__(parent)
		self._search_text = ''
		self._suppress_item_changed = False
		self._build_ui()
		try:
			NotesDB.initialize()
		except Exception:
			pass
		self.refresh()

	def _build_ui(self):
		v = QVBoxLayout(self)
		v.setContentsMargins(0, 0, 0, 0)
		v.setSpacing(6)
		row = QHBoxLayout()
		row.setContentsMargins(0, 0, 0, 0)
		row.setSpacing(6)
		self.new_btn = QToolButton(self)
		self.new_btn.setText("+ New")
		self.new_btn.setStyleSheet("QToolButton { color: #B5B9C0; background: rgba(255,255,255,0.06); border: 1px solid #3A4048; border-radius: 6px; padding: 2px 6px; font-size: 11px; }")
		self.new_btn.clicked.connect(self._on_new_note)
		row.addStretch(1)
		row.addWidget(self.new_btn)
		v.addLayout(row)

		self.search = QLineEdit(self)
		try:
			self.search.setPlaceholderText("Search notes…")
		except Exception:
			pass
		try:
			self.search.setClearButtonEnabled(True)
		except Exception:
			pass
		self.search.setStyleSheet("QLineEdit { background: rgba(255,255,255,0.03); color: #E8EAED; border: 1px solid #2E333B; border-radius: 8px; padding: 6px 8px; font-size: 12px; }")
		self.search.textChanged.connect(self._on_search_changed)
		v.addWidget(self.search)

		self.list = QListWidget(self)
		self.list.setStyleSheet(
			"QListWidget { background: rgba(255,255,255,0.03); border: 1px solid #2E333B; border-radius: 8px; color: #E8EAED; font-size: 12px; }"
			" QListWidget::item { padding: 8px 10px; border-radius: 6px; }"
			" QListWidget::item:selected { background: rgba(255,255,255,0.10); }"
			" QListWidget::item:hover { background: rgba(255,255,255,0.06); }"
		)
		try:
			self.list.setTextElideMode(Qt.ElideRight)
			self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
			self.list.setUniformItemSizes(True)
		except Exception:
			pass
		self.list.itemClicked.connect(self._on_item_clicked)
		try:
			self.list.setContextMenuPolicy(Qt.CustomContextMenu)
			self.list.customContextMenuRequested.connect(self._on_context_menu)
		except Exception:
			pass
		v.addWidget(self.list, 1)

	def refresh(self, preserve_selection: bool = True):
		"""Reload list from DB, optionally preserving selection."""
		try:
			q = (self._search_text or '').strip()
			notes = NotesDB.search_notes(q) if q else NotesDB.list_notes()
		except Exception:
			notes = []
		current_id = self.current_note_id()
		self.list.clear()
		row_to_select = None
		for idx, n in enumerate(notes):
			name = str(n.get('name') or '')
			item = QListWidgetItem(name)
			item.setData(Qt.UserRole, int(n.get('id') or 0))
			try:
				item.setToolTip(name)
			except Exception:
				pass
			self.list.addItem(item)
			if preserve_selection and current_id and int(n.get('id') or 0) == current_id:
				row_to_select = idx
		if row_to_select is not None:
			self.list.setCurrentRow(int(row_to_select))

	def current_note_id(self) -> int:
		try:
			item = self.list.currentItem()
			return int(item.data(Qt.UserRole)) if item else 0
		except Exception:
			return 0

	def _on_new_note(self):
		try:
			nid = NotesDB.create_note('New Note', '')
		except Exception:
			nid = 0
		self.refresh(preserve_selection=False)
		# Select the new note (likely first row due to updated_at ordering)
		if self.list.count() > 0:
			self.list.setCurrentRow(0)
		self.new_note_created.emit(int(nid))
		if nid:
			self.note_selected.emit(int(nid))

	def _on_search_changed(self, text: str):
		self._search_text = (text or '').strip()
		self.refresh()

	def _on_item_clicked(self, item: QListWidgetItem):
		try:
			cid = int(item.data(Qt.UserRole))
			self.note_selected.emit(cid)
		except Exception:
			pass

	def _on_context_menu(self, pos):
		try:
			global_pos = self.list.mapToGlobal(pos)
			item = self.list.itemAt(pos)
			if item is None:
				return
			menu = QMenu(self)
			open_action = menu.addAction("Open")
			rename_action = menu.addAction("Rename…")
			delete_action = menu.addAction("Delete")
			action = menu.exec_(global_pos)
			if action == delete_action:
				self._delete_note_from_item(item)
			elif action == open_action:
				self._emit_selected_from_item(item)
			elif action == rename_action:
				self._prompt_rename(item)
		except Exception:
			pass

	def _delete_note_from_item(self, item: QListWidgetItem):
		try:
			nid = int(item.data(Qt.UserRole))
			NotesDB.delete_note(nid)
			self.refresh(preserve_selection=False)
		except Exception:
			pass

	def _emit_selected_from_item(self, item: QListWidgetItem):
		try:
			nid = int(item.data(Qt.UserRole))
			self.note_selected.emit(nid)
		except Exception:
			pass

	def _prompt_rename(self, item: QListWidgetItem):
		from PyQt5.QtWidgets import QInputDialog
		text, ok = QInputDialog.getText(self, "Rename note", "Name:", text=item.text())
		if ok:
			try:
				NotesDB.rename_note(int(item.data(Qt.UserRole)), text)
			except Exception:
				pass
			self.refresh()


