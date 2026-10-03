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
CREATE TABLE IF NOT EXISTS seen (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL
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


class SeenCache:
    """Уже обработанные исходники (путь + размер + mtime).

    Рестарты и первичное сканирование не должны слать одно и то же
    в PACS повторно. Изменившийся файл (другой размер/mtime) обработается.
    """

    def __init__(self, db_path):
        from pathlib import Path as _P
        self.db_path = _P(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._lock, self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _sig(path: str) -> tuple | None:
        import os
        try:
            st = os.stat(path)
            return (st.st_size, st.st_mtime)
        except OSError:
            return None

    def is_seen(self, path: str) -> bool:
        """Обрабатывался ли уже этот файл в таком виде."""
        sig = self._sig(path)
        if sig is None:
            return False
        with self._lock, self._connect() as con:
            cur = con.execute("SELECT size, mtime FROM seen WHERE path=?",
                              (str(path),))
            row = cur.fetchone()
            if not row:
                return False
            return int(row["size"]) == sig[0] and float(row["mtime"]) == sig[1]

    def mark(self, path: str) -> None:
        """Запомнить файл как обработанный."""
        sig = self._sig(path)
        if sig is None:
            return
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO seen (path, size, mtime) VALUES (?, ?, ?)",
                (str(path), sig[0], sig[1]),
            )
            con.commit()
