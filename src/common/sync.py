"""The OneDrive sync engine (GUI-free and unit-testable).

Everything is manual and flat: the plugin only ever looks at the direct
children of the selected folder, never at subfolders.

* :meth:`SyncEngine.scan` compares the calibre library with the folder for the
  review window (link, calibre index, then filename heuristics).
* :meth:`SyncEngine.apply_plan` executes the rows the user selected (upload,
  download, delete duplicate).
* :meth:`SyncEngine.upload_all`, :meth:`download_all` and
  :meth:`SyncEngine.clear_backup` back the review window's bulk actions.
* :meth:`SyncEngine.index_available` tells the action whether the folder already
  carries a usable ``calibre-onedrive-index.json``.

Files are always uploaded into the selected folder itself (no export
subfolder), so the index and every stored book live in the same flat folder.

Network and calibre specifics are injected, which keeps this module pure and
easy to test.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from collections.abc import Callable
from typing import Protocol

from .config import Settings
from .errors import CancelledError, GraphError, SyncError
from .i18n import _
from .index import INDEX_FILENAME, BookIndex, build_index
from .log import get_logger
from .models import BookLink, ExportRecord, RemoteItem, SyncResult
from .reconcile import (
    IMPORT_EXTENSIONS,
    LocalBook,
    ReconcileEntry,
    ReconcilePlan,
    build_pending_plan,
    build_reconcile_plan,
    pick_format,
)
from .state import StateStore

log = get_logger("onedrive_sync.sync")

_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

#: Minimum seconds between two live plan updates while reading remote metadata.
#: Rebuilding the plan is quadratic, so updates are throttled on large libraries.
PLAN_UPDATE_INTERVAL = 0.5

#: Progress fractions used by :meth:`SyncEngine.scan` for the review window.
SCAN_LISTING_START = 0.05
SCAN_LISTING_END = 0.45
SCAN_METADATA_END = 0.90


class Library(Protocol):
    """The calibre library operations the engine needs."""

    def all_books(self) -> list[int]: ...

    def title(self, book_id: int) -> str: ...

    def authors(self, book_id: int) -> str: ...

    def formats(self, book_id: int) -> list[str]: ...

    def format_file(self, book_id: int, fmt: str) -> tuple[str, float, int] | None: ...

    def identifiers(self, book_id: int) -> dict[str, str]: ...


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
        link_book: Callable[[BookLink], None] | None = None,
        metadata_reader: Callable[[str, str], tuple[str, str] | None] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        on_plan: Callable[[ReconcilePlan, str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
        temp_root: str | None = None,
    ) -> None:
        self.graph = graph
        self.state = state
        self.library = library
        self.settings = settings
        self.add_book = add_book
        self.link_book = link_book
        self.metadata_reader = metadata_reader
        self.on_progress = on_progress
        self.on_plan = on_plan
        self.should_abort = should_abort
        self.temp_root = temp_root
        self._export_folder: RemoteItem | None = None

    # -- public API ---------------------------------------------------------

    def index_available(self) -> bool:
        """True when the selected folder holds a usable calibre index."""
        if self._aborted():
            return False
        return self._load_index_by_path() is not None

    def scan(self) -> ReconcilePlan:
        """Compare the calibre library with the selected OneDrive folder.

        The result is reported through :attr:`on_plan` as soon as it is usable:
        once with the local books (before touching the network), then repeatedly
        while the folders are listed, once after the listing, and again while
        remote ebook metadata is read. Progress messages go through
        :attr:`on_progress`. That lets the review window open immediately and
        fill in as the scan advances instead of sitting on one line.
        """
        if self._aborted():
            raise CancelledError("Cancelled.")

        self._report(0.02, _("Reading your calibre library…"))
        local_books = self._local_books()
        if self._aborted():
            raise CancelledError("Cancelled.")
        links = {(link.drive_id, link.item_id): link.book_id for link in self.state.all_links()}
        preference = self.settings.export_format
        self._notify_plan(
            build_pending_plan(local_books, links=links, preference=preference),
            "library",
        )

        self._report(SCAN_LISTING_START, _("Listing files in OneDrive…"))
        listed: list[RemoteItem] = []
        pages = 0

        def on_page(page: list[RemoteItem]) -> None:
            """Report every Graph page so the window fills in while listing runs."""
            nonlocal pages
            pages += 1
            listed.extend(page)
            ramp = 1 - 1 / (1 + pages / 4)
            self._report(
                SCAN_LISTING_START + (SCAN_LISTING_END - SCAN_LISTING_START) * ramp,
                _("Found {count} file(s) in OneDrive…").format(count=len(listed)),
            )
            self._notify_plan(
                build_pending_plan(local_books, listed, links=links, preference=preference),
                "listing",
            )
            if self._aborted():
                raise CancelledError("Cancelled.")

        remote_items = self._list_folder(on_page)
        if self._aborted():
            raise CancelledError("Cancelled.")
        self._report(
            SCAN_LISTING_END,
            _("Found {count} file(s) in OneDrive.").format(count=len(remote_items)),
        )
        _index_item, index = self._load_index(remote_items)
        plan = build_reconcile_plan(
            local_books,
            remote_items,
            links=links,
            preference=preference,
            index=index,
        )
        self._notify_plan(plan, "onedrive")

        plan = self._probe_remote_metadata(plan, remote_items)
        if self._aborted():
            raise CancelledError("Cancelled.")
        self._report(1.0, plan.summary())
        self._notify_plan(plan, "final")
        return plan

    def upload_all(self) -> SyncResult:
        """Upload every book that is not in OneDrive yet and write the index.

        Existing files are linked instead of uploaded again, so this is safe to
        run on a folder that already contains the books (for example after a
        manual ``Save to disk`` export).
        """
        result = SyncResult()
        if self._aborted():
            result.cancelled = True
            result.errors.append("Sync cancelled.")
            return result

        self._report(0.02, _("Listing files in OneDrive…"))
        remote_items = self._list_folder()
        if self._aborted():
            result.cancelled = True
            result.errors.append("Sync cancelled.")
            return result
        index_item, index = self._load_index(remote_items)
        links = {(link.drive_id, link.item_id): link.book_id for link in self.state.all_links()}
        plan = build_reconcile_plan(
            self._local_books(),
            remote_items,
            links=links,
            preference=self.settings.export_format,
            index=index,
        )

        # Matches are remembered right away so the index covers them too.
        for entry in plan.entries:
            if entry.status == "matched" and entry.local is not None and entry.remote is not None:
                self._persist_link(entry.local.book_id, entry.remote)

        uploads = [entry for entry in plan.entries if entry.status == "upload"]
        total = max(1, len(uploads))
        for position, entry in enumerate(uploads):
            if self._aborted():
                result.cancelled = True
                result.errors.append("Sync cancelled.")
                break
            self._report(
                0.1 + 0.8 * (position / total),
                _("Uploading {index}/{total}: {name}").format(
                    index=position + 1, total=total, name=entry.display()
                ),
            )
            try:
                self._apply_upload(entry, result)
            except Exception as exc:  # noqa: BLE001 - isolate per book
                result.failed += 1
                result.errors.append(f"{entry.display()}: {exc}")
                log.warning("Upload all failed for %s", entry.key, exc_info=True)

        if not result.cancelled:
            self._report(0.95, _("Writing the OneDrive index…"))
            try:
                self._write_index_from_state(index_item)
            except Exception as exc:  # noqa: BLE001 - books are already uploaded
                result.failed += 1
                result.errors.append(f"OneDrive index: {exc}")
                log.warning("Could not write the OneDrive index", exc_info=True)
        self._report(1.0, result.summary())
        return result

    def download_all(self) -> SyncResult:
        """Import every ebook file in the folder, matching nothing.

        Deliberately does not de-duplicate: the books are added through
        calibre's normal add-books API with duplicates allowed, so a messy
        folder can be imported completely and cleaned up afterwards with
        calibre's Find Duplicates plugin.
        """
        result = SyncResult()
        drive_id = self.settings.drive_id
        if self._aborted():
            result.cancelled = True
            result.errors.append("Sync cancelled.")
            return result

        self._report(0.02, _("Listing files in OneDrive…"))
        candidates = [item for item in self._list_folder() if item.extension in IMPORT_EXTENSIONS]
        total = max(1, len(candidates))
        with tempfile.TemporaryDirectory(prefix="onedrive-sync-", dir=self.temp_root) as temp_dir:
            for position, item in enumerate(candidates):
                if self._aborted():
                    result.cancelled = True
                    result.errors.append("Sync cancelled.")
                    break
                self._report(
                    0.05 + 0.9 * (position / total),
                    _("Downloading {index}/{total}: {name}").format(
                        index=position + 1, total=total, name=item.name
                    ),
                )
                item_drive = item.drive_id or drive_id
                if self.state.is_imported(item_drive, item.id):
                    result.skipped += 1
                    continue
                entry = ReconcileEntry(
                    key=f"remote:{item_drive}:{item.id}",
                    status="download",
                    remote=item,
                )
                try:
                    self._apply_download(entry, temp_dir, position, result)
                except Exception as exc:  # noqa: BLE001 - isolate per file
                    result.failed += 1
                    result.errors.append(f"{item.name}: {exc}")
                    log.warning("Could not import %s", item.name, exc_info=True)

        if not result.cancelled:
            self._report(0.98, _("Writing the OneDrive index…"))
            try:
                self._write_index_from_state()
            except Exception:  # noqa: BLE001 - imported books are already saved
                log.warning("Could not write the OneDrive index", exc_info=True)
        self._report(1.0, result.summary())
        return result

    def clear_backup(self) -> SyncResult:
        """Delete every item in the selected folder and forget the sync state."""
        result = SyncResult()
        drive_id = self.settings.drive_id
        if self._aborted():
            result.cancelled = True
            result.errors.append("Sync cancelled.")
            return result

        self._report(0.02, _("Listing the OneDrive folder…"))
        children = self._list_folder()
        total = max(1, len(children))
        for position, child in enumerate(children):
            if self._aborted():
                result.cancelled = True
                result.errors.append("Sync cancelled.")
                break
            self._report(
                0.05 + 0.9 * (position / total),
                _("Deleting {index}/{total}: {name}").format(
                    index=position + 1, total=total, name=child.name
                ),
            )
            try:
                self.graph.delete_item(child.drive_id or drive_id, child.id)
                result.deleted += 1
            except Exception as exc:  # noqa: BLE001 - isolate per item
                result.failed += 1
                result.errors.append(f"{child.name}: {exc}")
                log.warning("Could not delete %s", child.name, exc_info=True)

        if not result.cancelled and not result.failed:
            self.state.forget_drive(drive_id)
        self._report(1.0, result.summary())
        return result

    def apply_plan(
        self,
        plan: ReconcilePlan,
        entries: list[ReconcileEntry],
    ) -> SyncResult:
        """Execute the selected review rows and persist all matches."""
        result = SyncResult()
        for entry in plan.entries:
            if entry.status == "matched" and entry.local is not None and entry.remote is not None:
                self._persist_link(entry.local.book_id, entry.remote)

        selected = [entry for entry in entries if entry.selectable]
        if any(entry.status == "upload" for entry in selected):
            try:
                self._ensure_export_folder()
            except Exception as exc:  # noqa: BLE001 - uploads fail individually below
                result.failed += 1
                result.errors.append(f"OneDrive folder: {exc}")
                selected = [entry for entry in selected if entry.status != "upload"]

        total = max(1, len(selected))
        with tempfile.TemporaryDirectory(prefix="onedrive-sync-", dir=self.temp_root) as temp_dir:
            for index, entry in enumerate(selected):
                if self._aborted():
                    result.cancelled = True
                    result.errors.append("Sync cancelled.")
                    break
                self._report(
                    0.5 + 0.5 * (index / total),
                    f"Applying {index + 1}/{total}: {entry.display()}",
                )
                try:
                    if entry.status == "upload":
                        self._apply_upload(entry, result)
                    elif entry.status == "download":
                        self._apply_download(entry, temp_dir, index, result)
                    elif entry.status == "duplicate":
                        self._apply_delete(entry, result)
                except Exception as exc:  # noqa: BLE001 - isolate per row
                    result.failed += 1
                    result.errors.append(f"{entry.display()}: {exc}")
                    log.warning("Review action failed for %s", entry.key, exc_info=True)
        if not result.cancelled and (result.imported or result.exported or result.deleted):
            try:
                self._write_index_from_state()
            except Exception:  # noqa: BLE001 - the review actions already ran
                log.warning("Could not update the OneDrive index", exc_info=True)
        self._report(1.0, result.summary())
        return result

    def _download(self, item: RemoteItem, temp_dir: str, *, prefix: str = "") -> str:
        name = sanitize_filename(os.path.basename(item.name))
        if not name:
            name = f"book-{item.id}.{item.extension or 'bin'}"
        dest = os.path.join(temp_dir, f"{prefix}{name}")
        self.graph.download(item.drive_id or self.settings.drive_id, item.id, dest)
        if not os.path.isfile(dest):
            raise SyncError(f"Download produced no file for {item.name}.")
        return dest

    # -- review helpers -----------------------------------------------------

    def _local_books(self) -> list[LocalBook]:
        return [self._local_book(int(book_id)) for book_id in self.library.all_books()]

    def _local_book(self, book_id: int, *, extra_format: str = "") -> LocalBook:
        book_id = int(book_id)
        formats = tuple(str(fmt).lower() for fmt in (self.library.formats(book_id) or []) if fmt)
        if extra_format and extra_format.lower() not in formats:
            formats = (*formats, extra_format.lower())
        identifiers: dict[str, str] = {}
        getter = getattr(self.library, "identifiers", None)
        if callable(getter):
            try:
                identifiers = {
                    str(key): str(value) for key, value in (getter(book_id) or {}).items() if value
                }
            except Exception:  # noqa: BLE001 - metadata must never stop a scan
                identifiers = {}
        fmt = pick_format(formats, self.settings.export_format)
        if fmt is None:
            fmt = pick_format(formats, "epub_or_pdf")
        size = 0
        mtime = 0.0
        if fmt is not None:
            info = self.library.format_file(book_id, fmt)
            if info is not None:
                _path, mtime, size = info
        return LocalBook(
            book_id=book_id,
            title=self.library.title(book_id),
            authors=self.library.authors(book_id),
            formats=formats,
            identifiers=identifiers,
            size=size,
            mtime=mtime,
        )

    def _probe_remote_metadata(
        self,
        plan: ReconcilePlan,
        remote_items: list[RemoteItem],
    ) -> ReconcilePlan:
        """Download unmatched EPUB/PDF files just far enough to read metadata.

        The plan is rebuilt (and pushed to :attr:`on_plan`) while probing, so
        rows move from "download" to "matched" as metadata becomes available.
        """
        if self.metadata_reader is None or not plan.local_books:
            return plan
        candidates = [
            entry.remote
            for entry in plan.entries
            if entry.status == "download"
            and entry.remote is not None
            and entry.remote.extension in {"epub", "pdf"}
        ]
        if not candidates:
            return plan

        metadata: dict[str, tuple[str, str]] = {}
        total = max(1, len(candidates))
        last_update = time.monotonic()
        current = plan
        with tempfile.TemporaryDirectory(prefix="onedrive-meta-", dir=self.temp_root) as temp_dir:
            for index, remote in enumerate(candidates):
                if self._aborted():
                    break
                self._report(
                    SCAN_LISTING_END + (SCAN_METADATA_END - SCAN_LISTING_END) * (index / total),
                    _("Reading book metadata {index}/{total}: {name}").format(
                        index=index + 1, total=total, name=remote.name
                    ),
                )
                drive_id = remote.drive_id or self.settings.drive_id
                cached = self.state.get_remote_metadata(drive_id, remote.id, remote.e_tag)
                if cached is not None:
                    if cached[0] or cached[1]:
                        metadata[remote.id] = cached
                        current = self._maybe_update_plan(
                            current, remote_items, metadata, last_update
                        )
                        last_update = time.monotonic()
                    continue
                try:
                    path = self._download(remote, temp_dir, prefix=f"{index:04d}-")
                    info = self.metadata_reader(path, remote.extension)
                except Exception:  # noqa: BLE001 - metadata is best-effort
                    log.debug("Could not read metadata for %s", remote.name, exc_info=True)
                    continue
                if not info:
                    continue
                title = str(info[0] or "")
                authors = str(info[1] or "")
                self.state.set_remote_metadata(
                    drive_id,
                    remote.id,
                    title,
                    authors,
                    e_tag=remote.e_tag,
                )
                if title or authors:
                    metadata[remote.id] = (title, authors)
                    current = self._maybe_update_plan(current, remote_items, metadata, last_update)
                    last_update = time.monotonic()

        if not metadata:
            return current
        return self._rebuild_plan(current, remote_items, metadata)

    def _maybe_update_plan(
        self,
        plan: ReconcilePlan,
        remote_items: list[RemoteItem],
        metadata: dict[str, tuple[str, str]],
        last_update: float,
    ) -> ReconcilePlan:
        """Rebuild the plan at most every :data:`PLAN_UPDATE_INTERVAL` seconds."""
        if time.monotonic() - last_update < PLAN_UPDATE_INTERVAL:
            return plan
        updated = self._rebuild_plan(plan, remote_items, metadata)
        self._notify_plan(updated, "metadata")
        return updated

    def _rebuild_plan(
        self,
        plan: ReconcilePlan,
        remote_items: list[RemoteItem],
        metadata: dict[str, tuple[str, str]],
    ) -> ReconcilePlan:
        links = {(link.drive_id, link.item_id): link.book_id for link in self.state.all_links()}
        return build_reconcile_plan(
            plan.local_books,
            remote_items,
            links=links or plan.links,
            preference=self.settings.export_format,
            metadata=metadata,
            index=plan.index,
        )

    def _persist_link(self, book_id: int, remote: RemoteItem) -> None:
        link = BookLink.from_remote(book_id, remote)
        self.state.link_book(link)
        self._notify_link(link)

    def _notify_link(self, link: BookLink) -> None:
        if self.link_book is None:
            return
        try:
            self.link_book(link)
        except Exception:  # noqa: BLE001 - calibre metadata is best-effort
            log.warning("Could not save the OneDrive link for book %s", link.book_id, exc_info=True)

    def _list_folder(
        self, on_page: Callable[[list[RemoteItem]], None] | None = None
    ) -> list[RemoteItem]:
        """Direct children of the selected folder (subfolders are never entered).

        ``on_page`` is called with every Graph page, which lets a scan report the
        files it found while a large folder is still being listed.
        """
        seen: list[RemoteItem] = []

        def report(page: list[RemoteItem]) -> None:
            alive = [item for item in page if not item.deleted]
            seen.extend(alive)
            if on_page is not None:
                on_page(alive)

        self.graph.list_children(
            self.settings.drive_id,
            self.settings.folder_id,
            on_page=report,
        )
        return seen

    def _ensure_export_folder(self) -> RemoteItem:
        """The selected folder is the destination; there is no export subfolder."""
        if self._export_folder is None:
            self._export_folder = self.graph.get_item(
                self.settings.drive_id,
                self.settings.folder_id,
            )
        return self._export_folder

    def _apply_upload(self, entry: ReconcileEntry, result: SyncResult) -> RemoteItem | None:
        book = entry.local
        if book is None:
            raise SyncError("No local book for this row.")
        fmt = pick_format(book.formats, self.settings.export_format)
        if fmt is None:
            result.skipped += 1
            return None
        info = self.library.format_file(book.book_id, fmt)
        if info is None:
            result.skipped += 1
            return None
        path, mtime, size = info
        folder = self._ensure_export_folder()
        title = self.library.title(book.book_id)
        authors = self.library.authors(book.book_id)
        name = build_export_name(title, authors, book.book_id, fmt)

        existing = self.state.get_export(book.book_id, fmt)
        link = self.state.get_link(book.book_id)
        item_id = ""
        if existing is not None and existing.name == name:
            item_id = existing.item_id
        elif link is not None and link.extension == fmt:
            item_id = link.item_id

        uploaded = self._upload_with_fallback(
            self.settings.drive_id,
            folder.id,
            name,
            path,
            item_id,
        )
        self.state.mark_exported(
            ExportRecord(
                book_id=book.book_id,
                fmt=fmt,
                drive_id=self.settings.drive_id,
                item_id=uploaded.id,
                name=uploaded.name,
                e_tag=uploaded.e_tag,
                size=size,
                local_mtime=mtime,
            )
        )
        self._persist_link(book.book_id, uploaded)
        result.exported += 1
        return uploaded

    def _apply_download(
        self,
        entry: ReconcileEntry,
        temp_dir: str,
        index: int,
        result: SyncResult,
    ) -> None:
        remote = entry.remote
        if remote is None:
            raise SyncError("No OneDrive file for this row.")
        path = self._download(remote, temp_dir, prefix=f"{index:04d}-")
        book_id = self.add_book(path)
        if not book_id:
            raise SyncError("calibre did not return a book id.")
        drive_id = remote.drive_id or self.settings.drive_id
        self.state.mark_imported(
            drive_id,
            remote.id,
            name=remote.name,
            e_tag=remote.e_tag,
            size=remote.size,
            book_id=book_id,
        )
        self._persist_link(book_id, remote)
        result.imported += 1

    def _apply_delete(self, entry: ReconcileEntry, result: SyncResult) -> None:
        remote = entry.remote
        if remote is None:
            raise SyncError("No OneDrive file for this row.")
        drive_id = remote.drive_id or self.settings.drive_id
        self.graph.delete_item(drive_id, remote.id)
        link = self.state.get_link_by_item(drive_id, remote.id)
        if link is not None:
            self.state.unlink_book(link.book_id)
        result.deleted += 1

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

    # -- calibre index ------------------------------------------------------

    def _index_path(self) -> str:
        """Path of the index file inside the selected folder."""
        folder = str(self.settings.folder_path or "").rstrip("/")
        return f"{folder}/{INDEX_FILENAME}"

    def _load_index_by_path(self) -> BookIndex | None:
        """Read the index file directly by its path.

        Listing a whole folder to find one known file is slow on the big
        libraries people keep in OneDrive, so this addresses the file itself and
        needs a single request.
        """
        try:
            with tempfile.TemporaryDirectory(
                prefix="onedrive-index-", dir=self.temp_root
            ) as temp_dir:
                dest = os.path.join(temp_dir, INDEX_FILENAME)
                self.graph.download_path(self.settings.drive_id, self._index_path(), dest)
                with open(dest, encoding="utf-8") as handle:
                    index = BookIndex.from_json(handle.read())
        except GraphError as exc:
            if exc.status != 404:
                raise
            # A missing index file is the normal first run, a missing folder is a
            # setup problem: confirm which of the two it is, then report the folder.
            self.graph.get_item(self.settings.drive_id, self.settings.folder_id)
            return None
        except Exception:  # noqa: BLE001 - a broken index counts as missing
            log.warning("Could not read the OneDrive index", exc_info=True)
            return None
        return index if index.books else None

    def _load_index(
        self,
        remote_items: list[RemoteItem],
    ) -> tuple[RemoteItem | None, BookIndex | None]:
        """Download and parse the index file, if the folder has one."""
        item = next(
            (
                entry
                for entry in remote_items
                if not entry.is_folder and entry.name == INDEX_FILENAME
            ),
            None,
        )
        if item is None:
            return None, None
        try:
            with tempfile.TemporaryDirectory(
                prefix="onedrive-index-", dir=self.temp_root
            ) as temp_dir:
                path = self._download(item, temp_dir, prefix="index-")
                with open(path, encoding="utf-8") as handle:
                    index = BookIndex.from_json(handle.read())
        except Exception:  # noqa: BLE001 - a broken index must not stop a scan
            log.warning("Could not read the OneDrive index", exc_info=True)
            return item, None
        return item, (index if index.books else None)

    def _write_index_from_state(self, existing: RemoteItem | None = None) -> RemoteItem | None:
        """Write the index that maps every linked book to its OneDrive item."""
        book_remotes: dict[int, RemoteItem] = {}
        for link in self.state.all_links():
            if not link.item_id or not link.name:
                continue
            book_remotes[int(link.book_id)] = RemoteItem(
                id=link.item_id,
                name=link.name,
                drive_id=link.drive_id or self.settings.drive_id,
                size=int(link.size),
                e_tag=link.e_tag,
                web_url=link.web_url,
            )
        if not book_remotes:
            return None
        index = build_index(
            book_remotes,
            generated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        with tempfile.TemporaryDirectory(prefix="onedrive-index-", dir=self.temp_root) as temp_dir:
            path = os.path.join(temp_dir, INDEX_FILENAME)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(index.to_json())
            uploaded = self.graph.upload_file(
                self.settings.drive_id,
                self.settings.folder_id,
                INDEX_FILENAME,
                path,
                item_id=existing.id if existing is not None else None,
                conflict="replace",
            )
        log.info("Wrote the OneDrive index with %s book(s)", len(index.books))
        return uploaded

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

    def _notify_plan(self, plan: ReconcilePlan, phase: str) -> None:
        """Publish a live plan snapshot to the review window (best effort)."""
        if self.on_plan is None:
            return
        try:
            self.on_plan(plan, phase)
        except Exception:  # noqa: BLE001 - the UI updating must not break a scan
            log.debug("Plan callback failed", exc_info=True)
