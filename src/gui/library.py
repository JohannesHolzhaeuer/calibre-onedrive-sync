"""calibre database adapter used by the sync engine.

This is the only module that reaches into ``gui.current_db``. Keeping it in the
GUI layer means the engine in :mod:`common.sync` stays pure and testable.
"""

from __future__ import annotations

import os

from ..common.log import get_logger

log = get_logger("onedrive_sync.library")


class CalibreLibrary:
    """Read books/formats from the currently open calibre library."""

    def __init__(self, gui) -> None:
        self.gui = gui
        self.api = gui.current_db.new_api

    def all_books(self) -> list[int]:
        return [int(book_id) for book_id in self.api.all_book_ids()]

    def title(self, book_id: int) -> str:
        return str(self.api.field_for("title", int(book_id), default_value="") or "")

    def authors(self, book_id: int) -> str:
        value = self.api.field_for("authors", int(book_id), default_value=[]) or []
        if isinstance(value, str):
            return value
        return " & ".join(str(author) for author in value)

    def formats(self, book_id: int) -> list[str]:
        value = self._field("formats", book_id, [])
        if isinstance(value, str):
            return [value.lower()]
        if isinstance(value, dict):
            return [str(fmt).lower() for fmt in value]
        return [str(fmt).lower() for fmt in value or []]

    def format_file(self, book_id: int, fmt: str) -> tuple[str, float, int] | None:
        path = self._format_path(book_id, fmt)
        if not path or not os.path.isfile(path):
            return None
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return path, stat.st_mtime, stat.st_size

    def _field(self, name: str, book_id: int, default):
        try:
            return self.api.field_for(name, int(book_id), default_value=default)
        except Exception:  # noqa: BLE001 - older/alternate calibre API
            return default

    def _format_path(self, book_id: int, fmt: str) -> str | None:
        book_id = int(book_id)
        fmt = fmt.lower()
        getter = getattr(self.api, "format_abspath", None)
        if getter is not None:
            for candidate in (fmt, fmt.upper()):
                try:
                    path = getter(book_id, candidate)
                except Exception:  # noqa: BLE001
                    continue
                if path:
                    return str(path)
        legacy = getattr(self.gui.current_db, "format", None)
        if legacy is not None:
            try:
                path = legacy(book_id, fmt.upper(), as_file=True)
            except Exception:  # noqa: BLE001
                return None
            if path:
                return str(path)
        return None


def add_book_to_library(gui, path: str) -> int | None:
    """Import ``path`` through calibre's normal add-books API (GUI thread)."""
    fmt = os.path.splitext(path)[1].lstrip(".").upper()
    if not fmt:
        return None
    from calibre.ebooks.metadata.meta import get_metadata

    api = gui.current_db.new_api
    with open(path, "rb") as stream:
        metadata = get_metadata(stream, fmt.lower())
    ids, _duplicates = api.add_books([(metadata, {fmt: path})], add_duplicates=True)
    ids = [int(book_id) for book_id in ids or []]
    _refresh_library_view(gui, ids)
    return ids[0] if ids else None


def _refresh_library_view(gui, ids: list[int]) -> None:
    """Make freshly added book ids visible in the library view."""
    if not ids:
        return
    try:
        model = gui.library_view.model()
        model.db.data.books_added(ids)
        model.books_added(len(ids))
    except Exception:  # noqa: BLE001 - fall back to a full refresh
        log.debug("Incremental library view refresh failed", exc_info=True)
        try:
            gui.library_view.model().refresh()
        except Exception:  # noqa: BLE001 - cosmetic
            return
    for refresh in (
        lambda: gui.tags_view.recount(),
        lambda: gui.refresh_cover_browser(),
        lambda: gui.library_view.select_rows(ids),
    ):
        try:
            refresh()
        except Exception:  # noqa: BLE001 - cosmetic extras
            log.debug("Optional view refresh failed", exc_info=True)
