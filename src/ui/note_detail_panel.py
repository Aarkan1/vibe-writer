from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTextEdit, QToolButton, QScrollArea, QLabel

from notes_db import NotesDB
from ui.checklist_widget import ChecklistWidget


class NoteDetailPanel(QWidget):
	"""Right-side note editor: title, description, and multiple checklists.

	Signals are not exposed; panel persists directly via NotesDB and asks the parent to refresh.
	"""

	request_sidebar_refresh = pyqtSignal()

	def __init__(self, parent=None):
		super().__init__(parent)
		self._note_id = 0
		self._active_checklist_id = 0
		self._build_ui()

	def _build_ui(self):
		v = QVBoxLayout(self)
		v.setContentsMargins(0, 0, 0, 0)
		v.setSpacing(8)

		title_row = QHBoxLayout()
		title_row.setContentsMargins(0, 0, 0, 0)
		title_row.setSpacing(6)
		self.title_edit = QLineEdit(self)
		self.title_edit.setPlaceholderText("Note title…")
		self.title_edit.setStyleSheet("QLineEdit { background: rgba(255,255,255,0.04); color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 6px 8px; font-size: 14px; font-weight: 600; }")
		self.title_edit.editingFinished.connect(self._on_title_changed)
		title_row.addWidget(self.title_edit, 1)
		self.add_checklist_btn = QToolButton(self)
		self.add_checklist_btn.setText("+ Checklist")
		self.add_checklist_btn.setToolTip("Add checklist")
		self.add_checklist_btn.clicked.connect(self._on_add_checklist)
		title_row.addWidget(self.add_checklist_btn)
		v.addLayout(title_row)

		self.desc_edit = QTextEdit(self)
		self.desc_edit.setPlaceholderText("Description…")
		self.desc_edit.setAcceptRichText(False)
		self.desc_edit.textChanged.connect(self._on_description_changed)
		self.desc_edit.setStyleSheet("QTextEdit { background: rgba(255,255,255,0.04); color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 6px; font-size: 13px; }")
		v.addWidget(self.desc_edit)

		self.scroll = QScrollArea(self)
		self.scroll.setWidgetResizable(True)
		self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
		self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
		self.inner = QWidget(self)
		self.inner_v = QVBoxLayout(self.inner)
		self.inner_v.setContentsMargins(0, 0, 0, 0)
		self.inner_v.setSpacing(10)
		self.inner.setLayout(self.inner_v)
		self.scroll.setWidget(self.inner)
		v.addWidget(self.scroll, 1)

		self._updating_desc = False

	def set_note_id(self, note_id: int):
		self._note_id = int(note_id or 0)
		self._reload()

	def active_checklist_id(self) -> int:
		"""Return the current active checklist id (newest or first)."""
		return int(self._active_checklist_id or 0)

	def _reload(self):
		# Clear checklists area
		while self.inner_v.count():
			item = self.inner_v.takeAt(0)
			w = item.widget()
			if w:
				w.setParent(None)
		# Load
		if not self._note_id:
			self.title_edit.setText("")
			self.desc_edit.blockSignals(True)
			self.desc_edit.setPlainText("")
			self.desc_edit.blockSignals(False)
			return
		try:
			note = NotesDB.get_full_note(self._note_id) or {}
		except Exception:
			note = {}
		self.title_edit.setText(str(note.get('name') or ''))
		# Avoid recursive textChanged while setting programmatically
		self._updating_desc = True
		self.desc_edit.setPlainText(str(note.get('description') or ''))
		self._updating_desc = False
		checklists = (note.get('checklists') or [])
		# Choose an active checklist: preserve existing if still present, else newest, else first
		new_ids = [int(c.get('id') or 0) for c in checklists]
		if self._active_checklist_id not in new_ids:
			self._active_checklist_id = (new_ids[-1] if new_ids else 0)
		for cl in checklists:
			w = ChecklistWidget(cl, parent=self.inner)
			w.request_refresh.connect(self._on_child_request_refresh)
			self.inner_v.addWidget(w)
		self.inner_v.addStretch(1)

	def _on_child_request_refresh(self):
		# Child asked to refresh (after DB update). Reload panel and hint sidebar refresh.
		self._reload()
		self.request_sidebar_refresh.emit()

	def _on_add_checklist(self):
		if not self._note_id:
			return
		try:
			cid = NotesDB.add_checklist(self._note_id, 'Checklist')
		except Exception:
			cid = 0
		self._active_checklist_id = int(cid or 0)
		self._reload()
		self.request_sidebar_refresh.emit()

	def _on_title_changed(self):
		if not self._note_id:
			return
		try:
			NotesDB.rename_note(self._note_id, self.title_edit.text())
		except Exception:
			pass
		self.request_sidebar_refresh.emit()

	def _on_description_changed(self):
		if self._updating_desc:
			return
		if not self._note_id:
			return
		try:
			NotesDB.update_note_description(self._note_id, self.desc_edit.toPlainText())
		except Exception:
			pass


