import os
import sqlite3
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime


def _now_iso() -> str:
	"""Return a compact UTC timestamp for updated_at/created_at fields."""
	return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


class NotesDB:
	"""SQLite wrapper for notes, checklists, and items.

	Tables (created on initialize):
	- notes(id INTEGER PK, name TEXT NOT NULL, description TEXT DEFAULT '', created_at TEXT, updated_at TEXT)
	- checklists(id INTEGER PK, note_id INTEGER NOT NULL, name TEXT NOT NULL, show_completed INTEGER DEFAULT 1,
	             FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE)
	- checklist_items(id INTEGER PK, checklist_id INTEGER NOT NULL, text TEXT NOT NULL,
	                  completed INTEGER DEFAULT 0, position INTEGER,
	                  FOREIGN KEY(checklist_id) REFERENCES checklists(id) ON DELETE CASCADE)

	Design notes:
	- Keep functions short-lived (open/close connection per call) to avoid cross-thread issues.
	- All mutating operations update parent updated_at and return primitive values.
	- Return rows as plain dicts for easy UI consumption.
	"""

	_initialized = False
	_lock = threading.Lock()
	_db_path = os.path.join('src', 'chats.db')  # reuse the same DB file used by chats

	@classmethod
	def initialize(cls, db_path: Optional[str] = None) -> None:
		"""Ensure tables exist. Safe to call multiple times."""
		with cls._lock:
			if db_path:
				cls._db_path = db_path
			if cls._initialized:
				return
			os.makedirs(os.path.dirname(cls._db_path), exist_ok=True)
			conn = sqlite3.connect(cls._db_path)
			try:
				cur = conn.cursor()
				cur.execute(
					"""
					CREATE TABLE IF NOT EXISTS notes (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						name TEXT NOT NULL,
						description TEXT DEFAULT '',
						created_at TEXT NOT NULL,
						updated_at TEXT NOT NULL
					);
					"""
				)
				cur.execute(
					"""
					CREATE TABLE IF NOT EXISTS checklists (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						note_id INTEGER NOT NULL,
						name TEXT NOT NULL,
						show_completed INTEGER NOT NULL DEFAULT 1,
						FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
					);
					"""
				)
				cur.execute(
					"""
					CREATE TABLE IF NOT EXISTS checklist_items (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						checklist_id INTEGER NOT NULL,
						text TEXT NOT NULL,
						completed INTEGER NOT NULL DEFAULT 0,
						position INTEGER,
						FOREIGN KEY(checklist_id) REFERENCES checklists(id) ON DELETE CASCADE
					);
					"""
				)
				# Minimal indices to keep lists fast
				cur.execute("CREATE INDEX IF NOT EXISTS idx_checklists_note_id ON checklists(note_id)")
				cur.execute("CREATE INDEX IF NOT EXISTS idx_items_checklist_id ON checklist_items(checklist_id)")
				cur.execute("CREATE INDEX IF NOT EXISTS idx_items_position ON checklist_items(position)")
				conn.commit()
			finally:
				conn.close()
			cls._initialized = True

	@classmethod
	def _connect(cls) -> sqlite3.Connection:
		if not cls._initialized:
			cls.initialize()
		return sqlite3.connect(cls._db_path)

	# ----------------------------- Notes --------------------------------- #
	@classmethod
	def create_note(cls, name: str = 'New Note', description: str = '') -> int:
		conn = cls._connect()
		try:
			now = _now_iso()
			cur = conn.cursor()
			cur.execute(
				"INSERT INTO notes(name, description, created_at, updated_at) VALUES (?,?,?,?)",
				(name or 'New Note', description or '', now, now),
			)
			nid = cur.lastrowid
			conn.commit()
			return int(nid)
		finally:
			conn.close()

	@classmethod
	def rename_note(cls, note_id: int, new_name: str) -> None:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute(
				"UPDATE notes SET name=?, updated_at=? WHERE id=?",
				(new_name or 'Untitled', _now_iso(), note_id),
			)
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def update_note_description(cls, note_id: int, description: str) -> None:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute(
				"UPDATE notes SET description=?, updated_at=? WHERE id=?",
				(description or '', _now_iso(), note_id),
			)
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def delete_note(cls, note_id: int) -> None:
		"""Delete a note and all its checklists/items explicitly."""
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("DELETE FROM checklist_items WHERE checklist_id IN (SELECT id FROM checklists WHERE note_id=?)", (note_id,))
			cur.execute("DELETE FROM checklists WHERE note_id=?", (note_id,))
			cur.execute("DELETE FROM notes WHERE id=?", (note_id,))
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def list_notes(cls) -> List[Dict[str, object]]:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("SELECT id, name, description, created_at, updated_at FROM notes ORDER BY datetime(updated_at) DESC, id DESC")
			rows = cur.fetchall()
			return [
				{'id': int(r[0]), 'name': str(r[1] or ''), 'description': str(r[2] or ''), 'created_at': str(r[3] or ''), 'updated_at': str(r[4] or '')}
				for r in rows
			]
		finally:
			conn.close()

	@classmethod
	def search_notes(cls, query: str) -> List[Dict[str, object]]:
		q = (query or '').strip().lower()
		if not q:
			return cls.list_notes()
		pattern = f"%{q}%"
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute(
				"""
				SELECT n.id, n.name, n.description, n.created_at, n.updated_at,
				       CASE WHEN LOWER(n.name) LIKE ? THEN 1 ELSE 0 END AS title_match,
				       MAX(CASE WHEN LOWER(n.description) LIKE ? THEN 1 ELSE 0 END) AS desc_match,
				       MAX(CASE WHEN LOWER(i.text) LIKE ? THEN 1 ELSE 0 END) AS item_match
				FROM notes n
				LEFT JOIN checklists c ON c.note_id = n.id
				LEFT JOIN checklist_items i ON i.checklist_id = c.id
				GROUP BY n.id
				HAVING title_match = 1 OR desc_match = 1 OR item_match = 1
				ORDER BY title_match DESC, desc_match DESC, item_match DESC, datetime(n.updated_at) DESC, n.id DESC
				""",
				(pattern, pattern, pattern),
			)
			rows = cur.fetchall()
			return [
				{'id': int(r[0]), 'name': str(r[1] or ''), 'description': str(r[2] or ''), 'created_at': str(r[3] or ''), 'updated_at': str(r[4] or '')}
				for r in rows
			]
		finally:
			conn.close()

	@classmethod
	def get_note(cls, note_id: int) -> Optional[Dict[str, object]]:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("SELECT id, name, description, created_at, updated_at FROM notes WHERE id=?", (note_id,))
			row = cur.fetchone()
			if not row:
				return None
			return {'id': int(row[0]), 'name': str(row[1] or ''), 'description': str(row[2] or ''), 'created_at': str(row[3] or ''), 'updated_at': str(row[4] or '')}
		finally:
			conn.close()

	@classmethod
	def get_full_note(cls, note_id: int) -> Optional[Dict[str, object]]:
		"""Return note with checklists and items."""
		note = cls.get_note(note_id)
		if not note:
			return None
		checklists = cls.list_checklists(note_id)
		for cl in checklists:
			items = cls.list_items(int(cl['id']), include_completed=True)
			cl['items'] = items
		note['checklists'] = checklists
		return note

	# --------------------------- Checklists ------------------------------ #
	@classmethod
	def add_checklist(cls, note_id: int, name: str = 'Checklist') -> int:
		conn = cls._connect()
		try:
			now = _now_iso()
			cur = conn.cursor()
			cur.execute("INSERT INTO checklists(note_id, name, show_completed) VALUES(?,?,1)", (note_id, name or 'Checklist'))
			cid = cur.lastrowid
			cur.execute("UPDATE notes SET updated_at=? WHERE id=?", (now, note_id))
			conn.commit()
			return int(cid)
		finally:
			conn.close()

	@classmethod
	def rename_checklist(cls, checklist_id: int, name: str) -> None:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("UPDATE checklists SET name=? WHERE id=?", (name or 'Checklist', checklist_id))
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def set_checklist_show_completed(cls, checklist_id: int, show_completed: bool) -> None:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("UPDATE checklists SET show_completed=? WHERE id=?", (1 if show_completed else 0, checklist_id))
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def delete_checklist(cls, checklist_id: int) -> None:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("DELETE FROM checklist_items WHERE checklist_id=?", (checklist_id,))
			cur.execute("DELETE FROM checklists WHERE id=?", (checklist_id,))
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def list_checklists(cls, note_id: int) -> List[Dict[str, object]]:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("SELECT id, name, show_completed FROM checklists WHERE note_id=? ORDER BY id ASC", (note_id,))
			rows = cur.fetchall()
			return [{'id': int(r[0]), 'name': str(r[1] or ''), 'show_completed': bool(r[2])} for r in rows]
		finally:
			conn.close()

	# ------------------------------ Items -------------------------------- #
	@classmethod
	def add_item(cls, checklist_id: int, text: str) -> int:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			# choose next position (simple append)
			cur.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM checklist_items WHERE checklist_id=?", (checklist_id,))
			next_pos = int((cur.fetchone() or [1])[0] or 1)
			cur.execute(
				"INSERT INTO checklist_items(checklist_id, text, completed, position) VALUES(?,?,0,?)",
				(checklist_id, text or '', next_pos),
			)
			item_id = cur.lastrowid
			conn.commit()
			return int(item_id)
		finally:
			conn.close()

	@classmethod
	def rename_item(cls, item_id: int, text: str) -> None:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("UPDATE checklist_items SET text=? WHERE id=?", (text or '', item_id))
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def toggle_item_completed(cls, item_id: int, completed: bool) -> None:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("UPDATE checklist_items SET completed=? WHERE id=?", (1 if completed else 0, item_id))
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def delete_item(cls, item_id: int) -> None:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("DELETE FROM checklist_items WHERE id=?", (item_id,))
			conn.commit()
		finally:
			conn.close()

	@classmethod
	def list_items(cls, checklist_id: int, include_completed: bool = True) -> List[Dict[str, object]]:
		conn = cls._connect()
		try:
			cur = conn.cursor()
			if include_completed:
				cur.execute(
					"SELECT id, text, completed, position FROM checklist_items WHERE checklist_id=? ORDER BY COALESCE(position, id) ASC",
					(checklist_id,),
				)
			else:
				cur.execute(
					"SELECT id, text, completed, position FROM checklist_items WHERE checklist_id=? AND completed=0 ORDER BY COALESCE(position, id) ASC",
					(checklist_id,),
				)
			rows = cur.fetchall()
			return [{'id': int(r[0]), 'text': str(r[1] or ''), 'completed': bool(r[2]), 'position': int(r[3] or 0)} for r in rows]
		finally:
			conn.close()

	# ---------------------------- Progress -------------------------------- #
	@classmethod
	def checklist_progress(cls, checklist_id: int) -> Tuple[int, int, float]:
		"""Return (completed, total, percent). Percent in 0..100."""
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("SELECT COUNT(1), SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) FROM checklist_items WHERE checklist_id=?", (checklist_id,))
			row = cur.fetchone() or (0, 0)
			total = int(row[0] or 0)
			completed = int(row[1] or 0)
			pct = (completed / total * 100.0) if total > 0 else 0.0
			return completed, total, pct
		finally:
			conn.close()

	@classmethod
	def note_progress(cls, note_id: int) -> Tuple[int, int, float]:
		"""Aggregate progress across all checklists in the note."""
		conn = cls._connect()
		try:
			cur = conn.cursor()
			cur.execute("SELECT id FROM checklists WHERE note_id=?", (note_id,))
			rows = cur.fetchall()
			completed = 0
			total = 0
			for r in rows:
				cid = int(r[0])
				c_done, c_total, _ = cls.checklist_progress(cid)
				completed += c_done
				total += c_total
			pct = (completed / total * 100.0) if total > 0 else 0.0
			return completed, total, pct
		finally:
			conn.close()


