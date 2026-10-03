"""Персистентная очередь ошибок отправки (SQLite).

Неотправленные файлы попадают сюда и переживают перезапуск программы.
Хранится путь к уже ОБРАБОТАННОЙ копии в папке экспорта (оригинал не трогаем).
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("queue")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS failed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    error TEXT DEFAULT '',
    created REAL NOT NULL,
    retries INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
);
"""


class ErrorQueue:
    """SQLite-очередь неотправленных файлов."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init(self) -> None:
        with self._lock, self._connect() as con:
            con.executescript(_SCHEMA)

    def add(self, file_path: str, error: str = "") -> int:
        """Добавить файл в очередь. Возвращает id записи."""
        with self._lock, self._connect() as con:
            cur = con.execute(
                "INSERT INTO failed (file_path, error, created, retries, status)"
                " VALUES (?, ?, ?, 0, 'pending')",
                (str(file_path), str(error)[:2000], time.time()),
            )
            con.commit()
            log.warning("в очередь ошибок: %s — %s", file_path, error)
            return int(cur.lastrowid)

    def list_all(self) -> list:
        """Все записи очереди (новые сверху)."""
        with self._lock, self._connect() as con:
            cur = con.execute("SELECT * FROM failed ORDER BY id DESC")
            return [dict(r) for r in cur.fetchall()]

    def pending(self) -> list:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "SELECT * FROM failed WHERE status='pending' ORDER BY id"
            )
            return [dict(r) for r in cur.fetchall()]

    def count_pending(self) -> int:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "SELECT COUNT(*) c FROM failed WHERE status='pending'"
            )
            row = cur.fetchone()
            return int(row["c"]) if row else 0

    def remove(self, record_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM failed WHERE id=?", (record_id,))
            con.commit()

    def mark_done(self, record_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE failed SET status='done' WHERE id=?", (record_id,)
            )
            con.commit()

    def mark_error(self, record_id: int, error: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE failed SET status='pending', error=?, retries=retries+1 WHERE id=?",
                (str(error)[:2000], record_id),
            )
            con.commit()

    def clear_done(self) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM failed WHERE status='done'")
            con.commit()
