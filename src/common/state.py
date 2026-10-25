"""Persistent sync state (which remote items were imported / books exported).

The database lives in calibre's configuration directory and is the reason a
second sync does not re-import every remote file or re-upload every book. It
also stores the OneDrive delta links so remote change detection is incremental.
"""

from __future__ import annotations

import os
import sqlite3
import time

from .models import ExportRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imported_items (
    drive_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    name TEXT,
    e_tag TEXT,
    size INTEGER,
    book_id INTEGER,
    imported_at REAL NOT NULL,
    PRIMARY KEY (drive_id, item_id)
);
CREATE TABLE IF NOT EXISTS exported_books (
    book_id INTEGER NOT NULL,
    fmt TEXT NOT NULL,
    drive_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    name TEXT NOT NULL,
    e_tag TEXT,
    size INTEGER,
    local_mtime REAL,
    exported_at REAL NOT NULL,
    PRIMARY KEY (book_id, fmt)
);
"""


def default_state_path() -> str:
    """Return ``<calibre config>/plugins/onedrive_sync/state.sqlite``."""
    base = os.environ.get("CALIBRE_CONFIG_DIRECTORY", "")
    if not base:
        try:
            from calibre.utils.config import config_dir

            base = str(config_dir)
        except Exception:  # noqa: BLE001 - headless/tests
            base = os.path.join(os.path.expanduser("~"), ".config", "calibre")
    return os.path.join(base, "plugins", "onedrive_sync", "state.sqlite")


class StateStore:
    """SQLite-backed, durable sync state."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_state_path()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- generic metadata ---------------------------------------------------

    def get_meta(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # -- OneDrive delta -----------------------------------------------------

    @staticmethod
    def _delta_key(drive_id: str, folder_id: str | None) -> str:
        return f"delta::{drive_id}::{folder_id or 'root'}"

    def get_delta_link(self, drive_id: str, folder_id: str | None) -> str:
        return self.get_meta(self._delta_key(drive_id, folder_id))

    def set_delta_link(self, drive_id: str, folder_id: str | None, link: str) -> None:
        self.set_meta(self._delta_key(drive_id, folder_id), link)

    # -- import bookkeeping -------------------------------------------------

    def is_imported(self, drive_id: str, item_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM imported_items WHERE drive_id = ? AND item_id = ?",
            (drive_id, item_id),
        ).fetchone()
        return row is not None

    def mark_imported(
        self,
        drive_id: str,
        item_id: str,
        *,
        name: str = "",
        e_tag: str = "",
        size: int = 0,
        book_id: int | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO imported_items(drive_id, item_id, name, e_tag, size, book_id, "
            "imported_at) VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(drive_id, item_id) DO UPDATE SET name = excluded.name, "
            "e_tag = excluded.e_tag, size = excluded.size, book_id = excluded.book_id, "
            "imported_at = excluded.imported_at",
            (drive_id, item_id, name, e_tag, int(size), book_id, time.time()),
        )
        self._conn.commit()

    # -- export bookkeeping -------------------------------------------------

    def get_export(self, book_id: int, fmt: str) -> ExportRecord | None:
        row = self._conn.execute(
            "SELECT * FROM exported_books WHERE book_id = ? AND fmt = ?",
            (book_id, fmt.lower()),
        ).fetchone()
        if row is None:
            return None
        return ExportRecord(
            book_id=int(row["book_id"]),
            fmt=str(row["fmt"]),
            drive_id=str(row["drive_id"]),
            item_id=str(row["item_id"]),
            name=str(row["name"]),
            e_tag=str(row["e_tag"] or ""),
            size=int(row["size"] or 0),
            local_mtime=float(row["local_mtime"] or 0.0),
        )

    def is_exported(self, book_id: int, fmt: str, local_mtime: float, size: int) -> bool:
        record = self.get_export(book_id, fmt)
        if record is None:
            return False
        return record.size == int(size) and abs(record.local_mtime - local_mtime) < 0.001

    def is_exported_item(self, drive_id: str, item_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM exported_books WHERE drive_id = ? AND item_id = ?",
            (drive_id, item_id),
        ).fetchone()
        return row is not None

    def mark_exported(self, record: ExportRecord) -> None:
        self._conn.execute(
            "INSERT INTO exported_books(book_id, fmt, drive_id, item_id, name, e_tag, size, "
            "local_mtime, exported_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(book_id, fmt) DO UPDATE SET drive_id = excluded.drive_id, "
            "item_id = excluded.item_id, name = excluded.name, e_tag = excluded.e_tag, "
            "size = excluded.size, local_mtime = excluded.local_mtime, "
            "exported_at = excluded.exported_at",
            (
                int(record.book_id),
                record.fmt.lower(),
                record.drive_id,
                record.item_id,
                record.name,
                record.e_tag,
                int(record.size),
                float(record.local_mtime),
                time.time(),
            ),
        )
        self._conn.commit()
