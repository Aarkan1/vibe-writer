import os
import sqlite3
import threading
from typing import List, Dict, Optional, Tuple
from datetime import datetime


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


class ChatDB:
    """Tiny SQLite wrapper for persisting chats and messages.

    Tables:
    - chats(id INTEGER PK, name TEXT, created_at TEXT, updated_at TEXT)
    - messages(id INTEGER PK, chat_id INTEGER, role TEXT, content TEXT, created_at TEXT)

    We keep operations simple: open a short-lived connection per call to avoid
    cross-thread issues. All writes update the parent chat's updated_at.
    """

    _initialized = False
    _lock = threading.Lock()
    _db_path = os.path.join('src', 'chats.db')

    @classmethod
    def initialize(cls, db_path: Optional[str] = None) -> None:
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
                    CREATE TABLE IF NOT EXISTS chats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()
            cls._initialized = True

    @classmethod
    def _connect(cls) -> sqlite3.Connection:
        if not cls._initialized:
            cls.initialize()
        return sqlite3.connect(cls._db_path)

    @classmethod
    def create_chat(cls, name: str) -> int:
        conn = cls._connect()
        try:
            now = _now_iso()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chats(name, created_at, updated_at) VALUES(?,?,?)",
                (name or 'New Chat', now, now),
            )
            chat_id = cur.lastrowid
            conn.commit()
            return int(chat_id)
        finally:
            conn.close()

    @classmethod
    def rename_chat(cls, chat_id: int, new_name: str) -> None:
        conn = cls._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE chats SET name=?, updated_at=? WHERE id=?",
                (new_name, _now_iso(), chat_id),
            )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def list_chats(cls) -> List[Dict[str, object]]:
        conn = cls._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, created_at, updated_at FROM chats ORDER BY datetime(updated_at) DESC, id DESC"
            )
            rows = cur.fetchall()
            return [
                {
                    'id': int(r[0]),
                    'name': str(r[1] or ''),
                    'created_at': str(r[2] or ''),
                    'updated_at': str(r[3] or ''),
                }
                for r in rows
            ]
        finally:
            conn.close()

    @classmethod
    def delete_chat(cls, chat_id: int) -> None:
        """Delete a chat and all its messages.

        We explicitly delete from `messages` first to avoid depending on
        SQLite's foreign key cascade setting in different environments.
        """
        conn = cls._connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            cur.execute("DELETE FROM chats WHERE id=?", (chat_id,))
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def add_message(cls, chat_id: int, role: str, content: str) -> int:
        conn = cls._connect()
        try:
            now = _now_iso()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO messages(chat_id, role, content, created_at) VALUES(?,?,?,?)",
                (chat_id, role, content, now),
            )
            msg_id = cur.lastrowid
            cur.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
            conn.commit()
            return int(msg_id)
        finally:
            conn.close()

    @classmethod
    def get_messages(cls, chat_id: int) -> List[Dict[str, str]]:
        conn = cls._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT role, content FROM messages WHERE chat_id=? ORDER BY id ASC",
                (chat_id,),
            )
            rows = cur.fetchall()
            return [
                { 'role': str(r[0] or ''), 'content': str(r[1] or '') }
                for r in rows
            ]
        finally:
            conn.close()


