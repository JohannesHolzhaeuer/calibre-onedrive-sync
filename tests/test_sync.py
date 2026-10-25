"""Tests for the GUI-free sync engine."""

from __future__ import annotations

import os

from onedrive_sync.common.config import Settings
from onedrive_sync.common.errors import GraphError
from onedrive_sync.common.models import ExportRecord, RemoteItem, SyncResult
from onedrive_sync.common.state import StateStore
from onedrive_sync.common.sync import (
    SyncEngine,
    build_export_name,
    normalize_export_subfolder,
    sanitize_filename,
    select_export_format,
)


def remote(
    item_id,
    name,
    *,
    folder=False,
    parent="",
    size=0,
    deleted=False,
    drive="drive",
):
    return RemoteItem(
        id=item_id,
        name=name,
        drive_id=drive,
        parent_id=parent,
        size=size,
        is_folder=folder,
        deleted=deleted,
    )


class FakeGraph:
    def __init__(self) -> None:
        self.delta_items = []
        self.delta_link = ""
        self.delta_errors = []
        self.delta_calls = []
        self.recursive = []
        self.children = []
        self.export_folder = remote("export", "Export", folder=True)
        self.ensure_error = None
        self.download_error = None
        self.upload_error = None
        self.upload_errors = []
        self.uploaded = []
        self.downloaded = []

    def delta(self, drive_id, folder_id, link):
        self.delta_calls.append((drive_id, folder_id, link))
        if self.delta_errors:
            raise self.delta_errors.pop(0)
        return list(self.delta_items), self.delta_link

    def list_recursive(self, drive_id, folder_id, *, skip_folder_id=None):
        self.delta_calls.append(("recursive", drive_id, folder_id, skip_folder_id))
        return list(self.recursive)

    def list_children(self, drive_id, folder_id):
        return list(self.children)

    def ensure_folder(self, drive_id, folder_id, name):
        if self.ensure_error is not None:
            raise self.ensure_error
        return self.export_folder

    def download(self, drive_id, item_id, dest):
        if self.download_error is not None:
            raise self.download_error
        self.downloaded.append((drive_id, item_id, dest))
        with open(dest, "wb") as handle:
            handle.write(b"book")

    def upload_file(self, drive_id, parent_id, name, path, *, item_id="", conflict="rename"):
        self.uploaded.append((drive_id, parent_id, name, path, item_id, conflict))
        if self.upload_errors:
            raise self.upload_errors.pop(0)
        if self.upload_error is not None:
            raise self.upload_error
        return remote("uploaded", name, parent=parent_id, size=os.path.getsize(path))


class FakeLibrary:
    def __init__(self) -> None:
        self.books = []
        self.titles_map = {}
        self.authors_map = {}
        self.formats_map = {}
        self.files = {}

    def all_books(self):
        return list(self.books)

    def title(self, book_id):
        return self.titles_map.get(book_id, "")

    def authors(self, book_id):
        return self.authors_map.get(book_id, "")

    def formats(self, book_id):
        return self.formats_map.get(book_id, [])

    def format_file(self, book_id, fmt):
        return self.files.get((book_id, fmt))


def make_engine(tmp_path, graph, library, settings, add_book=None, **kwargs):
    state = StateStore(str(tmp_path / "state.sqlite"))
    engine = SyncEngine(
        graph,
        state,
        library,
        settings,
        add_book=add_book or (lambda path: 1),
        **kwargs,
    )
    return engine, state


def test_filename_helpers():
    assert sanitize_filename(' a/b:c*? "d" ') == "a_b_c__ _d_"
    assert sanitize_filename("") == ""
    name = build_export_name("", "", 7, "EPUB")
    assert name == "Unknown author - Book 7 (7).epub"
    assert build_export_name("A", "B", 1, "pdf") == "B - A (1).pdf"
    assert normalize_export_subfolder("") == "Calibre Export"
    assert normalize_export_subfolder("..") == "Calibre Export"
    assert normalize_export_subfolder("My Export") == "My Export"


def test_select_export_format():
    assert select_export_format(["EPUB", "pdf"], "epub_or_pdf") == "epub"
    assert select_export_format(["PDF"], "epub_or_pdf") == "pdf"
    assert select_export_format(["MOBI"], "epub_or_pdf") is None
    assert select_export_format(["EPUB"], "pdf") is None
    assert select_export_format(["PDF"], "pdf") == "pdf"
    assert select_export_format([], "epub") is None


def test_import_delta_success(tmp_path):
    graph = FakeGraph()
    graph.delta_link = "new-link"
    graph.delta_items = [
        remote("f", "Folder", folder=True),
        remote("a", "a.epub", size=4),
        remote("t", "notes.xyz"),
        remote("e", "exported.epub", parent="export"),
        remote("x", "deleted.epub", deleted=True),
    ]
    graph.export_folder = remote("export", "Export", folder=True)
    library = FakeLibrary()
    added = []

    def add_book(path):
        added.append(path)
        return 11

    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=True, export_enabled=False
    )
    engine, state = make_engine(tmp_path, graph, library, settings, add_book)
    engine._export_folder = graph.export_folder
    result = engine.run()
    assert result.imported == 1
    assert result.failed == 0
    assert result.skipped == 2
    assert added and added[0].endswith("a.epub")
    assert state.is_imported("drive", "a")
    assert state.get_delta_link("drive", "folder") == "new-link"
    state.close()


def test_import_skips_already_imported_and_handles_add_failure(tmp_path):
    graph = FakeGraph()
    graph.delta_items = [remote("a", "a.epub"), remote("b", "b.epub")]
    library = FakeLibrary()
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=True, export_enabled=False
    )
    engine, state = make_engine(tmp_path, graph, library, settings, add_book=lambda path: None)
    state.mark_imported("drive", "a")
    result = engine.run()
    assert result.skipped == 1
    assert result.failed == 1
    assert "calibre did not return" in result.errors[0]
    state.close()


def test_import_delta_410_retries(tmp_path):
    graph = FakeGraph()
    graph.delta_errors = [GraphError(410, "expired")]
    graph.delta_items = [remote("a", "a.epub")]
    graph.delta_link = "fresh"
    library = FakeLibrary()
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=True, export_enabled=False
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    state.set_delta_link("drive", "folder", "old")
    result = engine.run()
    assert result.imported == 1
    assert graph.delta_calls[0] == ("drive", "folder", "old")
    assert graph.delta_calls[1] == ("drive", "folder", None)
    assert state.get_delta_link("drive", "folder") == "fresh"
    state.close()


def test_import_delta_failure_falls_back_to_recursive(tmp_path):
    graph = FakeGraph()
    graph.delta_errors = [GraphError(500, "boom")]
    graph.recursive = [remote("a", "a.epub")]
    library = FakeLibrary()
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=True, export_enabled=False
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    result = engine.run()
    assert result.imported == 1
    assert any("OneDrive delta" in error for error in result.errors)
    assert graph.delta_calls[-1][0] == "recursive"
    state.close()


def test_import_abort_and_download_failure(tmp_path):
    graph = FakeGraph()
    graph.delta_items = [remote("a", "a.epub"), remote("b", "b.epub")]
    graph.download_error = OSError("network")
    library = FakeLibrary()
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=True, export_enabled=False
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    result = engine.run()
    assert result.failed == 2
    assert result.imported == 0
    state.close()

    graph.download_error = None
    engine2, state2 = make_engine(tmp_path, graph, library, settings, should_abort=lambda: True)
    result2 = engine2.run()
    assert "Sync cancelled." in result2.errors
    state2.close()


def test_progress_and_abort_hooks_are_best_effort(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=False, export_enabled=False
    )

    def broken_progress(_fraction, _message):
        raise RuntimeError("ui gone")

    def broken_abort():
        raise RuntimeError("bad hook")

    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        on_progress=broken_progress,
        should_abort=broken_abort,
    )
    result = engine.run()
    assert isinstance(result, SyncResult)
    state.close()


def test_export_uploads_and_marks_state(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    library.books = [1]
    library.titles_map = {1: "Book"}
    library.authors_map = {1: "Author"}
    file_path = tmp_path / "book.epub"
    file_path.write_bytes(b"book")
    library.formats_map = {1: ["EPUB"]}
    library.files = {(1, "epub"): (str(file_path), 10.0, 4)}
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=False, export_enabled=True
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    result = engine.run()
    assert result.exported == 1
    assert graph.uploaded[0][2] == "Author - Book (1).epub"
    record = state.get_export(1, "epub")
    assert record is not None
    assert record.item_id == "uploaded"
    state.close()


def test_export_skips_missing_formats_and_unchanged_books(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    library.books = [1, 2, 3]
    library.titles_map = {1: "One", 2: "Two", 3: "Three"}
    library.formats_map = {1: [], 2: ["MOBI"], 3: ["PDF"]}
    file_path = tmp_path / "three.pdf"
    file_path.write_bytes(b"pdf")
    library.files = {(3, "pdf"): (str(file_path), 20.0, 3)}
    settings = Settings(
        drive_id="drive",
        folder_id="folder",
        import_enabled=False,
        export_enabled=True,
        export_format="epub",
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    result = engine.run()
    assert result.skipped == 3
    assert result.exported == 0
    state.close()


def test_export_adopts_same_size_remote_file(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    library.books = [1]
    library.titles_map = {1: "Book"}
    library.authors_map = {1: "Author"}
    file_path = tmp_path / "book.epub"
    file_path.write_bytes(b"book")
    library.formats_map = {1: ["EPUB"]}
    library.files = {(1, "epub"): (str(file_path), 10.0, 4)}
    graph.children = [remote("remote", "Author - Book (1).epub", size=4)]
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=False, export_enabled=True
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    result = engine.run()
    assert result.skipped == 1
    assert result.exported == 0
    assert state.get_export(1, "epub").item_id == "remote"
    state.close()


def test_export_updates_existing_item_and_falls_back_after_404(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    library.books = [1]
    library.titles_map = {1: "Book"}
    library.authors_map = {1: "Author"}
    file_path = tmp_path / "book.epub"
    file_path.write_bytes(b"book")
    library.formats_map = {1: ["EPUB"]}
    library.files = {(1, "epub"): (str(file_path), 30.0, 4)}
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=False, export_enabled=True
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    state.mark_exported(
        ExportRecord(1, "epub", "drive", "old", "Author - Book (1).epub", size=1, local_mtime=1.0)
    )
    graph.upload_errors = [GraphError(404, "gone")]
    result = engine.run()
    assert result.exported == 1
    assert graph.uploaded[0][4] == "old"
    assert graph.uploaded[1][4] == ""
    state.close()


def test_export_folder_failure_is_reported(tmp_path):
    graph = FakeGraph()
    graph.ensure_error = GraphError(500, "cannot create")
    library = FakeLibrary()
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=False, export_enabled=True
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    result = engine.run()
    assert result.failed == 1
    assert any("export folder" in error for error in result.errors)
    state.close()


def test_export_folder_is_cached_and_state_is_set(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=False, export_enabled=True
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    first = engine._ensure_export_folder()
    second = engine._ensure_export_folder()
    assert first is second
    assert state.get_meta(engine._export_folder_key()) == "export"
    state.close()


def test_export_handles_upload_failure_per_book(tmp_path):
    graph = FakeGraph()
    graph.upload_error = OSError("disk full")
    library = FakeLibrary()
    library.books = [1]
    library.titles_map = {1: "Book"}
    library.authors_map = {1: "Author"}
    file_path = tmp_path / "book.epub"
    file_path.write_bytes(b"book")
    library.formats_map = {1: ["EPUB"]}
    library.files = {(1, "epub"): (str(file_path), 10.0, 4)}
    settings = Settings(
        drive_id="drive", folder_id="folder", import_enabled=False, export_enabled=True
    )
    engine, state = make_engine(tmp_path, graph, library, settings)
    result = engine.run()
    assert result.failed == 1
    assert "Export" in result.errors[0]
    state.close()
