"""The two-way sync engine (GUI-free and unit-testable).

One run does two things:

1. **Import** — walk the selected OneDrive folder (delta first, recursive
   listing as fallback), download book files that were not imported before, and
   hand them to an injected ``add_book`` callable. Successfully imported remote
   items are recorded so they are never imported again.
2. **Export** — walk the calibre library and upload EPUB/PDF files that are not
   yet represented in the export subfolder. The state records the remote item
   so later changes replace the same file instead of creating duplicates.

Network and calibre specifics are injected, which keeps this module pure and
easy to test.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from typing import Protocol

from .config import Settings
from .errors import GraphError, SyncError
from .log import get_logger
from .models import ExportRecord, RemoteItem, SyncResult
from .state import StateStore

log = get_logger("onedrive_sync.sync")

#: Formats calibre can normally import. Unknown files are ignored.
IMPORT_EXTENSIONS = frozenset(
    {
        "epub",
        "pdf",
        "mobi",
        "azw",
        "azw3",
        "fb2",
        "fbz",
        "lit",
        "lrf",
        "pdb",
        "prc",
        "rb",
        "rtf",
        "snb",
        "tcr",
        "txt",
        "html",
        "htm",
        "docx",
        "odt",
        "cbz",
        "cbr",
        "cb7",
    }
)

_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class Library(Protocol):
    """The calibre library operations the engine needs."""

    def all_books(self) -> list[int]: ...

    def title(self, book_id: int) -> str: ...

    def authors(self, book_id: int) -> str: ...

    def formats(self, book_id: int) -> list[str]: ...

    def format_file(self, book_id: int, fmt: str) -> tuple[str, float, int] | None: ...


def sanitize_filename(text: str, *, limit: int = 120) -> str:
    """Make ``text`` safe as a single file/folder name on Windows and OneDrive."""
    cleaned = _INVALID_FILENAME.sub("_", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:limit]


def build_export_name(title: str, authors: str, book_id: int, fmt: str) -> str:
    """Stable, human-readable export filename including the calibre book id."""
    clean_title = sanitize_filename(title) or f"Book {book_id}"
    clean_authors = sanitize_filename(authors) or "Unknown author"
    base = sanitize_filename(f"{clean_authors} - {clean_title} ({book_id})", limit=180)
    return f"{base}.{fmt.lower()}"


def normalize_export_subfolder(name: str) -> str:
    """Return a safe single folder name for the export destination."""
    cleaned = sanitize_filename(str(name or ""), limit=80)
    if not cleaned or cleaned in {".", ".."}:
        return "Calibre Export"
    return cleaned


def select_export_format(formats: list[str], preference: str) -> str | None:
    """Pick EPUB or PDF according to the configured preference."""
    available = {str(fmt).lower().lstrip(".") for fmt in formats if fmt}
    if preference == "epub":
        return "epub" if "epub" in available else None
    if preference == "pdf":
        return "pdf" if "pdf" in available else None
    if "epub" in available:
        return "epub"
    if "pdf" in available:
        return "pdf"
    return None


class SyncEngine:
    """Run one import+export pass over the configured OneDrive folder."""

    def __init__(
        self,
        graph,
        state: StateStore,
        library: Library,
        settings: Settings,
        *,
        add_book: Callable[[str], int | None],
        on_progress: Callable[[float, str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
        temp_root: str | None = None,
    ) -> None:
        self.graph = graph
        self.state = state
        self.library = library
        self.settings = settings
        self.add_book = add_book
        self.on_progress = on_progress
        self.should_abort = should_abort
        self.temp_root = temp_root
        self._export_folder: RemoteItem | None = None

    # -- public API ---------------------------------------------------------

    def run(self) -> SyncResult:
        """Run import and export; failures are collected in the result."""
        result = SyncResult()
        if self.settings.export_enabled:
            try:
                self._ensure_export_folder()
            except Exception as exc:  # noqa: BLE001 - reported, export can still try
                result.failed += 1
                result.errors.append(f"OneDrive export folder: {exc}")
                log.warning("Could not prepare the export folder", exc_info=True)

        if self.settings.import_enabled:
            self._import(result)
        if not self._aborted() and self.settings.export_enabled and self._export_folder is not None:
            self._export(result)

        self._report(1.0, result.summary())
        return result

    # -- import -------------------------------------------------------------

    def _import(self, result: SyncResult) -> None:
        drive_id = self.settings.drive_id
        folder_id = self.settings.folder_id
        delta_link = self.state.get_delta_link(drive_id, folder_id)
        items: list[RemoteItem] = []
        new_delta = ""
        try:
            items, new_delta = self.graph.delta(drive_id, folder_id, delta_link or None)
        except GraphError as exc:
            if delta_link and exc.status in {410}:
                log.info("Delta link expired; restarting the delta query")
                self.state.set_delta_link(drive_id, folder_id, "")
                try:
                    items, new_delta = self.graph.delta(drive_id, folder_id, None)
                except GraphError as retry_exc:
                    log.warning("Delta query failed, listing the folder instead", exc_info=True)
                    items = self._full_listing()
                    new_delta = ""
                    result.errors.append(f"OneDrive delta: {retry_exc}")
            else:
                log.warning("Delta query failed, listing the folder instead", exc_info=True)
                items = self._full_listing()
                new_delta = ""
                result.errors.append(f"OneDrive delta: {exc}")

        export_folder_id = self._export_folder.id if self._export_folder else ""
        total = max(1, len(items))
        failures_before = result.failed

        with tempfile.TemporaryDirectory(prefix="onedrive-sync-", dir=self.temp_root) as temp_dir:
            for index, item in enumerate(items):
                if self._aborted():
                    result.errors.append("Sync cancelled.")
                    break
                self._report(
                    0.5 * (index / total),
                    f"Checking OneDrive file {index + 1}/{total}: {item.name}",
                )
                if item.is_folder or item.deleted:
                    continue
                if item.extension not in IMPORT_EXTENSIONS:
                    result.skipped += 1
                    continue
                if export_folder_id and item.parent_id == export_folder_id:
                    result.skipped += 1
                    continue
                if self.state.is_imported(item.drive_id or drive_id, item.id):
                    result.skipped += 1
                    continue

                try:
                    path = self._download(item, temp_dir)
                    book_id = self.add_book(path)
                    if not book_id:
                        raise SyncError("calibre did not return a book id.")
                    self.state.mark_imported(
                        item.drive_id or drive_id,
                        item.id,
                        name=item.name,
                        e_tag=item.e_tag,
                        size=item.size,
                        book_id=book_id,
                    )
                    result.imported += 1
                except Exception as exc:  # noqa: BLE001 - isolate per file
                    result.failed += 1
                    result.errors.append(f"Import {item.name}: {exc}")
                    log.warning("Could not import %s", item.name, exc_info=True)

        # Only advance the delta link when every item was processed, so a failed
        # file is offered again next time (successful items are skipped by state).
        if new_delta and result.failed == failures_before:
            self.state.set_delta_link(drive_id, folder_id, new_delta)
        self._report(0.5, f"Imported {result.imported} file(s).")

    def _full_listing(self) -> list[RemoteItem]:
        skip = self._export_folder.id if self._export_folder else None
        return self.graph.list_recursive(
            self.settings.drive_id,
            self.settings.folder_id,
            skip_folder_id=skip,
        )

    def _download(self, item: RemoteItem, temp_dir: str) -> str:
        name = sanitize_filename(os.path.basename(item.name))
        if not name:
            name = f"book-{item.id}.{item.extension or 'bin'}"
        dest = os.path.join(temp_dir, name)
        self.graph.download(item.drive_id or self.settings.drive_id, item.id, dest)
        if not os.path.isfile(dest):
            raise SyncError(f"Download produced no file for {item.name}.")
        return dest

    # -- export -------------------------------------------------------------

    def _ensure_export_folder(self) -> RemoteItem:
        if self._export_folder is not None:
            return self._export_folder
        name = normalize_export_subfolder(self.settings.export_subfolder)
        folder = self.graph.ensure_folder(
            self.settings.drive_id,
            self.settings.folder_id,
            name,
        )
        self._export_folder = folder
        self.state.set_meta(
            self._export_folder_key(),
            folder.id,
        )
        return folder

    def _export_folder_key(self) -> str:
        return f"export-folder::{self.settings.drive_id}::{self.settings.folder_id}"

    def _export(self, result: SyncResult) -> None:
        folder = self._ensure_export_folder()
        drive_id = self.settings.drive_id
        remote_children = {
            child.name: child
            for child in self.graph.list_children(drive_id, folder.id)
            if not child.is_folder
        }
        books = list(self.library.all_books())
        total = max(1, len(books))
        for index, book_id in enumerate(books):
            if self._aborted():
                result.errors.append("Sync cancelled.")
                break
            self._report(
                0.5 + 0.5 * (index / total),
                f"Checking calibre book {index + 1}/{total}",
            )
            fmt = select_export_format(self.library.formats(book_id), self.settings.export_format)
            if not fmt:
                result.skipped += 1
                continue
            file_info = self.library.format_file(book_id, fmt)
            if file_info is None:
                result.skipped += 1
                continue
            path, mtime, size = file_info
            if self.state.is_exported(book_id, fmt, mtime, size):
                result.skipped += 1
                continue

            title = self.library.title(book_id)
            authors = self.library.authors(book_id)
            name = build_export_name(title, authors, book_id, fmt)
            existing = self.state.get_export(book_id, fmt)
            remote = remote_children.get(name)
            item_id = ""
            if existing is not None and existing.name == name:
                item_id = existing.item_id
            elif remote is not None:
                item_id = remote.id

            if remote is not None and remote.size == size and item_id:
                self.state.mark_exported(
                    ExportRecord(
                        book_id=book_id,
                        fmt=fmt,
                        drive_id=drive_id,
                        item_id=item_id,
                        name=name,
                        e_tag=remote.e_tag,
                        size=size,
                        local_mtime=mtime,
                    )
                )
                result.skipped += 1
                continue

            try:
                uploaded = self._upload_with_fallback(drive_id, folder.id, name, path, item_id)
                self.state.mark_exported(
                    ExportRecord(
                        book_id=book_id,
                        fmt=fmt,
                        drive_id=drive_id,
                        item_id=uploaded.id,
                        name=uploaded.name,
                        e_tag=uploaded.e_tag,
                        size=size,
                        local_mtime=mtime,
                    )
                )
                remote_children[uploaded.name] = uploaded
                result.exported += 1
            except Exception as exc:  # noqa: BLE001 - isolate per book
                result.failed += 1
                result.errors.append(f"Export {name}: {exc}")
                log.warning("Could not export book %s", book_id, exc_info=True)
        self._report(1.0, f"Exported {result.exported} book(s).")

    def _upload_with_fallback(
        self,
        drive_id: str,
        parent_id: str,
        name: str,
        path: str,
        item_id: str,
    ) -> RemoteItem:
        if item_id:
            try:
                return self.graph.upload_file(
                    drive_id, parent_id, name, path, item_id=item_id, conflict="rename"
                )
            except GraphError as exc:
                if exc.status != 404:
                    raise
                log.info("Previous OneDrive item is gone; uploading %s again", name)
        return self.graph.upload_file(drive_id, parent_id, name, path, conflict="rename")

    # -- helpers ------------------------------------------------------------

    def _aborted(self) -> bool:
        try:
            return bool(self.should_abort and self.should_abort())
        except Exception:  # noqa: BLE001 - abort hooks must not break a sync
            return False

    def _report(self, fraction: float, message: str) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(max(0.0, min(1.0, float(fraction))), message)
        except Exception:  # noqa: BLE001 - progress is best-effort
            log.debug("Progress callback failed", exc_info=True)
