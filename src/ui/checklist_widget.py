from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
	QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QToolButton, QCheckBox, QListWidget,
	QListWidgetItem, QProgressBar, QLabel
)

from notes_db import NotesDB


class ChecklistWidget(QWidget):
	"""A single checklist editor with progress and item list.

	Signals:
	- request_refresh(): ask parent to reload from DB after a change
	"""

	request_refresh = pyqtSignal()

	def __init__(self, checklist: dict, parent=None):
		super().__init__(parent)
		self._checklist = checklist or {}
		self._build_ui()
		self._load()

	def _build_ui(self):
		v = QVBoxLayout(self)
		v.setContentsMargins(0, 0, 0, 0)
		v.setSpacing(6)
		# Header: name edit, show-completed toggle, progress, delete
		head = QHBoxLayout()
		head.setContentsMargins(0, 0, 0, 0)
		head.setSpacing(6)
		self.name_edit = QLineEdit(self)
		self.name_edit.setText(str(self._checklist.get('name') or 'Checklist'))
		self.name_edit.setStyleSheet("QLineEdit { background: rgba(255,255,255,0.04); color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 4px 6px; font-size: 12px; }")
		self.name_edit.editingFinished.connect(self._on_rename)
		head.addWidget(self.name_edit, 1)
		self.toggle_show = QCheckBox("Show completed", self)
		self.toggle_show.setChecked(bool(self._checklist.get('show_completed')))
		self.toggle_show.stateChanged.connect(self._on_toggle_show_completed)
		head.addWidget(self.toggle_show)
		self.progress = QProgressBar(self)
		self.progress.setTextVisible(True)
		self.progress.setFixedHeight(14)
		self.progress.setRange(0, 100)
		head.addWidget(self.progress)
		self.del_btn = QToolButton(self)
		self.del_btn.setText("🗑")
		self.del_btn.setToolTip("Delete checklist")
		self.del_btn.clicked.connect(self._on_delete)
		head.addWidget(self.del_btn)
		v.addLayout(head)

		# Items list
		self.items = QListWidget(self)
		self.items.setStyleSheet(
			"QListWidget { background: rgba(255,255,255,0.03); border: 1px solid #2E333B; border-radius: 8px; color: #E8EAED; font-size: 12px; }"
			" QListWidget::item { padding: 4px 6px; }"
		)
		v.addWidget(self.items)

		# Add-item row
		add_row = QHBoxLayout()
		add_row.setContentsMargins(0, 0, 0, 0)
		add_row.setSpacing(6)
		self.add_input = QLineEdit(self)
		self.add_input.setPlaceholderText("Add item…")
		self.add_input.returnPressed.connect(self._on_add_item)
		add_row.addWidget(self.add_input, 1)
		self.add_btn = QToolButton(self)
		self.add_btn.setText("+")
		self.add_btn.setToolTip("Add item")
		self.add_btn.clicked.connect(self._on_add_item)
		add_row.addWidget(self.add_btn)
		v.addLayout(add_row)

	def checklist_id(self) -> int:
		try:
			return int(self._checklist.get('id') or 0)
		except Exception:
			return 0

	def _load(self):
		# Load items and progress
		cid = self.checklist_id()
		show_completed = bool(self._checklist.get('show_completed'))
		try:
			items = NotesDB.list_items(cid, include_completed=True)
		except Exception:
			items = []
		self.items.clear()
		visible_count = 0
		for it in items:
			if (not show_completed) and bool(it.get('completed')):
				continue
			visible_count += 1
			w = _ChecklistItemRow(it, parent=self)
			w.request_refresh.connect(self.request_refresh)
			li = QListWidgetItem(self.items)
			li.setSizeHint(w.sizeHint())
			self.items.addItem(li)
			self.items.setItemWidget(li, w)
		# Progress uses all items, not only visible
		try:
			completed, total, pct = NotesDB.checklist_progress(cid)
			self.progress.setValue(int(round(pct)))
			self.progress.setFormat(f"{completed}/{total} ({int(round(pct))}%)")
		except Exception:
			self.progress.setValue(0)
			self.progress.setFormat("0/0 (0%)")

	def _on_rename(self):
		try:
			NotesDB.rename_checklist(self.checklist_id(), self.name_edit.text())
		except Exception:
			pass
		self.request_refresh.emit()

	def _on_toggle_show_completed(self):
		try:
			NotesDB.set_checklist_show_completed(self.checklist_id(), bool(self.toggle_show.isChecked()))
		except Exception:
			pass
		self.request_refresh.emit()

	def _on_delete(self):
		try:
			NotesDB.delete_checklist(self.checklist_id())
		except Exception:
			pass
		self.request_refresh.emit()

	def _on_add_item(self):
		text = (self.add_input.text() or '').strip()
		if not text:
			return
		try:
			NotesDB.add_item(self.checklist_id(), text)
		except Exception:
			pass
		self.add_input.clear()
		self.request_refresh.emit()


class _ChecklistItemRow(QWidget):
	"""Row widget for a single checklist item: checkbox + editable text + delete button."""

	request_refresh = pyqtSignal()

	def __init__(self, item: dict, parent=None):
		super().__init__(parent)
		self._item = item or {}
		self._build_ui()

	def _build_ui(self):
		row = QHBoxLayout(self)
		row.setContentsMargins(6, 4, 6, 4)
		row.setSpacing(6)
		self.cb = QCheckBox(self)
		self.cb.setChecked(bool(self._item.get('completed')))
		self.cb.stateChanged.connect(self._on_toggle_completed)
		row.addWidget(self.cb)
		self.edit = QLineEdit(self)
		self.edit.setText(str(self._item.get('text') or ''))
		self.edit.setStyleSheet("QLineEdit { background: rgba(255,255,255,0.02); color: #E8EAED; border: 1px solid #2E333B; border-radius: 6px; padding: 3px 6px; font-size: 12px; }")
		self.edit.editingFinished.connect(self._on_rename)
		row.addWidget(self.edit, 1)
		self.del_btn = QToolButton(self)
		self.del_btn.setText("🗑")
		self.del_btn.setToolTip("Delete item")
		self.del_btn.clicked.connect(self._on_delete)
		row.addWidget(self.del_btn)

	def _item_id(self) -> int:
		try:
			return int(self._item.get('id') or 0)
		except Exception:
			return 0

	def _on_toggle_completed(self):
		try:
			NotesDB.toggle_item_completed(self._item_id(), bool(self.cb.isChecked()))
		except Exception:
			pass
		self.request_refresh.emit()

	def _on_rename(self):
		try:
			NotesDB.rename_item(self._item_id(), self.edit.text())
		except Exception:
			pass
		self.request_refresh.emit()

	def _on_delete(self):
		try:
			NotesDB.delete_item(self._item_id())
		except Exception:
			pass
		self.request_refresh.emit()


