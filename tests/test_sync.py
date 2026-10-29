"""Tests for the GUI-free sync engine."""

from __future__ import annotations

import json
import os
import tempfile
import threading

import pytest

from onedrive_sync.common.config import Settings
from onedrive_sync.common.errors import CancelledError, GraphError
from onedrive_sync.common.index import INDEX_FILENAME, BookIndex, IndexEntry
from onedrive_sync.common.models import BookLink, ExportRecord, RemoteItem
from onedrive_sync.common.reconcile import (
    LocalBook,
    ReconcileEntry,
    ReconcilePlan,
    build_reconcile_plan,
)
from onedrive_sync.common.state import StateStore
from onedrive_sync.common.sync import (
    SyncEngine,
    build_export_name,
    sanitize_filename,
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
    web_url="",
):
    return RemoteItem(
        id=item_id,
        name=name,
        drive_id=drive,
        parent_id=parent,
        size=size,
        is_folder=folder,
        deleted=deleted,
        web_url=web_url,
    )


class FakeGraph:
    def __init__(self) -> None:
        self.recursive = []
        self.list_calls = []
        self.download_error = None
        self.upload_error = None
        self.upload_errors = []
        self.uploaded = []
        self.uploaded_bytes = {}
        #: Every upload as ``(name, payload)``, in order, so a test can watch a
        #: file that is rewritten several times during one pass.
        self.upload_history = []
        self.files = {}
        self.downloaded = []
        self.deleted = []
        self.delete_error = None
        self.get_item_error = None
        self.get_item_calls = []

    def list_children(self, drive_id, folder_id, *, on_page=None):
        self.list_calls.append((drive_id, folder_id))
        children = list(self.recursive)
        if on_page is not None:
            on_page(children)
        return children

    def get_item(self, drive_id, item_id):
        self.get_item_calls.append((drive_id, item_id))
        if self.get_item_error is not None:
            raise self.get_item_error
        return remote(item_id, "Selected", folder=True)

    def download_path(self, drive_id, path, dest):
        """Serve a file addressed by path, like Graph's ``root:`` syntax does."""
        name = str(path).rsplit("/", 1)[-1]
        item = next((entry for entry in self.recursive if entry.name == name), None)
        if item is None:
            raise GraphError(404, "itemNot-found")
        self.download(drive_id, item.id, dest)

    def download(self, drive_id, item_id, dest):
        if self.download_error is not None:
            raise self.download_error
        self.downloaded.append((drive_id, item_id, dest))
        with open(dest, "wb") as handle:
            handle.write(self.files.get(item_id, b"book"))

    def upload_file(
        self, drive_id, parent_id, name, path, *, item_id="", conflict="rename", on_progress=None
    ):
        self.uploaded.append((drive_id, parent_id, name, path, item_id, conflict))
        with open(path, "rb") as handle:
            self.uploaded_bytes[name] = handle.read()
        self.upload_history.append((name, self.uploaded_bytes[name]))
        if self.upload_errors:
            raise self.upload_errors.pop(0)
        if self.upload_error is not None:
            raise self.upload_error
        size = os.path.getsize(path)
        if on_progress is not None:
            on_progress(size)
        return remote("uploaded", name, parent=parent_id, size=size)

    def delete_item(self, drive_id, item_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append((drive_id, item_id))


class FailingIndexGraph(FakeGraph):
    """A folder whose books upload fine but whose index file cannot be written."""

    def upload_file(
        self, drive_id, parent_id, name, path, *, item_id="", conflict="rename", on_progress=None
    ):
        if name == INDEX_FILENAME:
            raise OSError("disk full")
        return super().upload_file(
            drive_id, parent_id, name, path, item_id=item_id, conflict=conflict
        )


class GatingGraph(FakeGraph):
    """Uploads that block until told, so a test can cancel a running pass.

    ``started`` signals that a file is on the wire, ``release`` ends the wait;
    the engine under test is expected to set ``release`` when it aborts.
    """

    def __init__(self, started, release) -> None:
        super().__init__()
        self._started = started
        self._release = release

    def upload_file(self, *args, **kwargs):
        self._started.set()
        self._release.wait(5)
        return super().upload_file(*args, **kwargs)


class CountingFailingIndexGraph(FailingIndexGraph):
    """The same broken index endpoint, with a counter for the attempts."""

    def __init__(self) -> None:
        super().__init__()
        self.index_attempts = 0

    def upload_file(
        self, drive_id, parent_id, name, path, *, item_id="", conflict="rename", on_progress=None
    ):
        if name == INDEX_FILENAME:
            self.index_attempts += 1
        return super().upload_file(
            drive_id, parent_id, name, path, item_id=item_id, conflict=conflict
        )


class ClearingGraph(FakeGraph):
    """A folder that really loses the children it is told to delete."""

    def delete_item(self, drive_id, item_id):
        super().delete_item(drive_id, item_id)
        self.recursive = [item for item in self.recursive if item.id != item_id]


class ConcurrencyGraph(FakeGraph):
    """Uploads that only finish once ``party`` of them run at the same time.

    A barrier is the only way to *prove* parallelism without sleeping: if the
    engine ever uploads fewer files at once, ``wait`` times out and the upload
    raises, so the test sees failures instead of passing for the wrong reason.
    """

    def __init__(self, party=3) -> None:
        super().__init__()
        self.barrier = threading.Barrier(party, timeout=5)
        self.in_flight = 0
        self.max_in_flight = 0
        self.threads: list[int] = []
        self._counted = threading.Lock()

    def upload_file(self, *args, **kwargs):
        name = args[2] if len(args) > 2 else kwargs.get("name", "")
        if name == INDEX_FILENAME:
            return super().upload_file(*args, **kwargs)  # written serially, at the end
        self.threads.append(threading.get_ident())
        with self._counted:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.barrier.wait()
        with self._counted:
            self.in_flight -= 1
        return super().upload_file(*args, **kwargs)


class FakeLibrary:
    def __init__(self) -> None:
        self.books = []
        self.titles_map = {}
        self.authors_map = {}
        self.formats_map = {}
        self.files = {}
        self.identifiers_map = {}

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

    def identifiers(self, book_id):
        return dict(self.identifiers_map.get(book_id, {}))


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


def _local_book(library, book_id, title, authors, fmt="epub"):
    """Register a local book with a real format file on disk."""
    library.books.append(book_id)
    library.titles_map[book_id] = title
    library.authors_map[book_id] = authors
    library.formats_map[book_id] = [fmt]
    directory = tempfile.mkdtemp(prefix="onedrive-test-")
    path = os.path.join(directory, f"book-{book_id}.{fmt}")
    with open(path, "wb") as handle:
        handle.write(b"book")
    library.files[(book_id, fmt)] = (path, 10.0, 4)


def write_index(graph, *entries):
    """Make the fake folder serve an index mapping ``(book_id, item_id, name)``."""
    index = BookIndex(
        books=[
            IndexEntry(book_id=book_id, name=name, item_id=item_id, drive_id="drive")
            for book_id, item_id, name in entries
        ]
    )
    graph.files["idx"] = index.to_json().encode()
    if not any(entry.name == INDEX_FILENAME for entry in graph.recursive):
        graph.recursive.append(remote("idx", INDEX_FILENAME))


def test_filename_helpers():
    assert sanitize_filename(' a/b:c*? "d" ') == "a_b_c__ _d_"
    assert sanitize_filename("") == ""
    name = build_export_name("", "", 7, "EPUB")
    assert name == "Unknown author - Book 7 (7).epub"
    assert build_export_name("A", "B", 1, "pdf") == "B - A (1).pdf"


def test_scan_matches_and_reads_remote_metadata(tmp_path):
    graph = FakeGraph()
    graph.recursive = [
        remote("scan", "scan001.epub", size=4),
        remote("named", "Jane Doe - The Book.epub", size=4),
    ]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    seen = []

    def reader(path, fmt):
        seen.append((path, fmt))
        return ("The Book", "Jane Doe")

    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        metadata_reader=reader,
    )
    plan = engine.scan()
    statuses = [entry.status for entry in plan.entries]
    assert statuses.count("matched") == 1
    assert statuses.count("duplicate") == 1
    assert seen and seen[0][1] == "epub"
    assert state.get_remote_metadata("drive", "scan") == ("The Book", "Jane Doe")
    state.close()


def test_scan_uses_identifiers_and_cached_metadata(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("linked", "totally-different.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    library.identifiers_map[1] = {"onedrive": "linked"}
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    plan = engine.scan()
    assert plan.entries[0].status == "matched"
    assert plan.entries[0].note == "Linked"

    graph.recursive = [remote("scan2", "scan002.epub")]
    library.identifiers_map = {}
    state.set_remote_metadata("drive", "scan2", "The Book", "Jane Doe", e_tag="")
    seen = []
    engine.metadata_reader = lambda path, fmt: seen.append(path) or ("wrong", "wrong")
    plan = engine.scan()
    assert not seen
    assert plan.entries[0].status == "matched"
    state.close()


def test_scan_forgets_links_to_files_deleted_in_onedrive(tmp_path):
    """An out-of-band clear of the folder must not leave the books linked."""
    graph = FakeGraph()
    graph.recursive = [remote("live", "keep001.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    state.link_book(BookLink(book_id=1, drive_id="drive", item_id="gone", name="The Book.epub"))
    state.link_book(BookLink(book_id=2, drive_id="drive", item_id="live", name="Keep.epub"))
    state.link_book(BookLink(book_id=3, drive_id="other", item_id="elsewhere", name="Other.epub"))

    plan = engine.scan()

    assert [link.book_id for link in engine.stale_links] == [1]
    assert state.get_link(1) is None
    assert state.get_link(2) is not None
    assert state.get_link(3) is not None
    # The book whose file vanished is offered for upload again.
    assert plan.entry("upload:1").status == "upload"


def test_scan_reports_stale_links_as_absent_when_the_folder_is_empty(tmp_path):
    graph = FakeGraph()
    graph.recursive = []
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    state.link_book(BookLink(book_id=1, drive_id="drive", item_id="gone", name="The Book.epub"))
    messages = []

    engine.on_progress = lambda fraction, message: messages.append(message)
    engine.scan()

    assert [link.book_id for link in engine.stale_links] == [1]
    assert any("no longer in OneDrive" in message for message in messages)


def test_scan_ignores_metadata_failures(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("scan", "scan001.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")

    def broken(_path, _fmt):
        raise RuntimeError("bad book")

    engine, state = make_engine(tmp_path, graph, library, settings, metadata_reader=broken)
    plan = engine.scan()
    assert plan.entries[0].status == "download"
    state.close()


def test_scan_aborts_with_cancelled_error(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("scan", "scan001.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        should_abort=lambda: True,
    )
    with pytest.raises(CancelledError):
        engine.scan()
    state.close()


def test_apply_plan_uploads_downloads_and_deletes(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "Upload Me", "Author")
    links = []
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        add_book=lambda _path: 5,
        link_book=links.append,
    )
    locals_ = [
        LocalBook(book_id=1, title="Upload Me", authors="Author", formats=("epub",)),
        LocalBook(book_id=2, title="Matched", authors="Author", formats=("epub",)),
    ]
    remotes = [
        remote("m", "Author - Matched.epub"),
        remote("extra", "Author - Matched copy.epub"),
        remote("d", "Unknown - Download.epub"),
    ]
    plan = build_reconcile_plan(locals_, remotes)
    selected = [
        entry for entry in plan.entries if entry.status in {"upload", "download", "duplicate"}
    ]
    result = engine.apply_plan(plan, selected)
    assert result.exported == 1
    assert result.imported == 1
    assert result.deleted == 1
    assert result.failed == 0
    assert graph.uploaded[0][2] == "Author - Upload Me (1).epub"
    assert graph.deleted == [("drive", "extra")]
    assert state.get_link(1).item_id == "uploaded"
    assert state.get_link(2).item_id == "m"
    assert state.get_link(5).item_id == "d"
    assert {link.book_id for link in links} >= {1, 2, 5}
    state.close()


def test_apply_plan_without_selection_only_persists_matches(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    links = []
    engine, state = make_engine(tmp_path, graph, library, settings, link_book=links.append)
    plan = build_reconcile_plan(
        [LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",))],
        [remote("r", "Jane Doe - The Book.epub", web_url="https://onedrive.test/r")],
    )
    result = engine.apply_plan(plan, [])
    assert result.imported == result.exported == result.deleted == 0
    assert state.get_link(1).item_id == "r"
    assert links and links[0].web_url == "https://onedrive.test/r"
    state.close()


def test_apply_plan_handles_row_failures_and_folder_failure(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "Matched", "Author")
    _local_book(library, 2, "Upload Me", "Author")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        add_book=lambda _path: None,
    )
    locals_ = [
        LocalBook(book_id=1, title="Matched", authors="Author", formats=("epub",)),
        LocalBook(book_id=2, title="Upload Me", authors="Author", formats=("epub",)),
    ]
    remotes = [
        remote("m", "Author - Matched.epub"),
        remote("extra", "Author - Matched copy.epub"),
        remote("d", "Unknown - Download.epub"),
    ]
    plan = build_reconcile_plan(locals_, remotes)
    selected = [entry for entry in plan.entries if entry.selectable]
    assert {entry.status for entry in selected} == {"duplicate", "download", "upload"}
    graph.download_error = OSError("network")
    graph.delete_error = OSError("gone")
    graph.upload_error = OSError("disk full")
    result = engine.apply_plan(plan, selected)
    assert result.failed == 3
    assert len(result.errors) == 3
    state.close()

    graph.download_error = None
    graph.delete_error = None
    graph.upload_error = None
    graph.get_item_error = RuntimeError("no folder")
    engine2, state2 = make_engine(tmp_path, graph, library, settings)
    result2 = engine2.apply_plan(plan, selected)
    assert result2.failed == 1
    assert any("OneDrive folder" in error for error in result2.errors)
    state2.close()


def test_apply_plan_abort_and_link_callback_failure(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    _local_book(library, 2, "Upload Me", "Author")
    settings = Settings(drive_id="drive", folder_id="folder")

    def broken_link(_link):
        raise RuntimeError("db closed")

    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        link_book=broken_link,
        should_abort=lambda: True,
    )
    plan = build_reconcile_plan(
        [
            LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",)),
            LocalBook(book_id=2, title="Upload Me", authors="Author", formats=("epub",)),
        ],
        [remote("r", "Jane Doe - The Book.epub")],
    )
    selected = plan.default_selection()
    assert any(entry.status == "upload" for entry in selected)
    result = engine.apply_plan(plan, selected)
    assert result.cancelled
    assert "Sync cancelled." in result.errors
    # Even an abort keeps the link that was already discovered.
    assert state.get_link(1).item_id == "r"
    state.close()


# -- cancellation ------------------------------------------------------------


def test_scan_cancels_during_the_metadata_probe(tmp_path):
    graph = FakeGraph()
    graph.recursive = [
        remote("scan1", "scan001.epub"),
        remote("scan2", "scan002.epub"),
    ]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        metadata_reader=lambda _path, _fmt: ("Other", "Writer"),
        should_abort=lambda: len(graph.downloaded) >= 1,
    )
    with pytest.raises(CancelledError):
        engine.scan()
    assert len(graph.downloaded) == 1
    state.close()


# -- progressive scanning ----------------------------------------------------


def test_scan_reports_local_books_before_touching_the_network(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("named", "Jane Doe - The Book.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    _local_book(library, 2, "Fresh", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    phases = []

    def on_plan(plan, phase):
        phases.append(
            (
                phase,
                [entry.status for entry in plan.entries],
                list(graph.list_calls),
            )
        )

    engine, state = make_engine(tmp_path, graph, library, settings, on_plan=on_plan)
    plan = engine.scan()

    assert [phase for phase, _statuses, _calls in phases] == [
        "library",
        "listing",
        "onedrive",
        "final",
    ]
    # The window opens with the library contents before OneDrive is touched.
    assert phases[0][2] == []
    assert phases[0][1] == ["checking", "checking"]
    # Every Graph page is reported while the listing is still running.
    assert phases[1][1] == ["checking", "checking", "checking"]
    # One flat listing, then the plan with the matched and uploadable rows.
    assert phases[2][2] == [("drive", "folder")]
    assert phases[2][1] == ["upload", "matched"]
    assert {entry.status for entry in plan.entries} == {"matched", "upload"}
    state.close()


def test_scan_reports_metadata_matches_while_probing(tmp_path, monkeypatch):
    from onedrive_sync.common import sync as sync_mod

    monkeypatch.setattr(sync_mod, "PLAN_UPDATE_INTERVAL", 0.0)
    graph = FakeGraph()
    graph.recursive = [
        remote("n1", "scan001.epub"),
        remote("n2", "scan002.epub"),
    ]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    phases = []
    remote_statuses = []

    def on_plan(plan, phase):
        phases.append(phase)
        remote_statuses.append([entry.status for entry in plan.entries if entry.remote is not None])

    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        metadata_reader=lambda _path, _fmt: ("The Book", "Jane Doe"),
        on_plan=on_plan,
    )
    plan = engine.scan()

    assert phases == [
        "library",
        "listing",
        "onedrive",
        "metadata",
        "metadata",
        "final",
    ]
    # The library snapshot has no remote rows; the listing snapshot shows the
    # files as soon as they are seen, the flat listing marks them as downloads
    # and the probes turn one into a match while the second becomes a duplicate
    # of the same book.
    assert remote_statuses[0] == []
    assert remote_statuses[1] == ["checking", "checking"]
    assert remote_statuses[2] == ["download", "download"]
    assert set(remote_statuses[3]) == {"download", "matched"}
    assert set(remote_statuses[4]) == {"duplicate", "matched"}
    assert {entry.status for entry in plan.entries} == {"matched", "duplicate"}
    state.close()


def test_scan_ignores_a_failing_plan_callback(tmp_path):
    graph = FakeGraph()
    graph.recursive = []
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")

    def broken(_plan, _phase):
        raise RuntimeError("window closed")

    engine, state = make_engine(tmp_path, graph, library, settings, on_plan=broken)
    plan = engine.scan()
    assert plan.local_books
    state.close()


def test_scan_ignores_a_failing_progress_callback(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")

    def broken(_fraction, _message):
        raise RuntimeError("widget gone")

    engine, state = make_engine(tmp_path, graph, library, settings, on_progress=broken)
    plan = engine.scan()
    assert plan.entries
    state.close()


def test_scan_cancels_after_reading_the_library(tmp_path):
    flag = []

    class AbortingLibrary(FakeLibrary):
        def all_books(self):
            flag.append(True)
            return []

    library = AbortingLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(
        tmp_path, FakeGraph(), library, settings, should_abort=lambda: bool(flag)
    )
    with pytest.raises(CancelledError):
        engine.scan()
    state.close()


def test_scan_matches_files_with_the_calibre_index(tmp_path):
    graph = FakeGraph()
    graph.recursive = [
        remote("idx", INDEX_FILENAME, size=10),
        remote("file", "zzz-totally-unknown.epub", size=4),
    ]
    write_index(graph, (1, "file", "zzz-totally-unknown.epub"))
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    plan = engine.scan()

    assert plan.index_present is True
    assert plan.index_books == 1
    matched = [entry for entry in plan.entries if entry.status == "matched"]
    assert [entry.local.book_id for entry in matched] == [1]
    assert matched[0].remote.id == "file"
    assert matched[0].note == "Matched from the calibre index"
    assert {entry.status for entry in plan.entries} == {"matched"}
    assert [(drive, item) for drive, item, _path in graph.downloaded] == [("drive", "idx")]
    state.close()


def test_scan_ignores_a_broken_index_and_falls_back_to_names(tmp_path):
    graph = FakeGraph()
    graph.recursive = [
        remote("idx", INDEX_FILENAME, size=4),
        remote("named", "Jane Doe - The Book.epub", size=4),
    ]
    graph.files["idx"] = b"{ this is not json"
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    plan = engine.scan()

    assert plan.index_present is False
    matched = [entry for entry in plan.entries if entry.status == "matched"]
    assert matched[0].note == "Matched automatically"
    state.close()


def test_upload_all_uploads_missing_books_and_writes_the_index(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("existing", "Jane Doe - One (1).epub", size=4)]
    library = FakeLibrary()
    _local_book(library, 1, "One", "Jane Doe")
    _local_book(library, 2, "Two", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", export_enabled=True)
    engine, state = make_engine(tmp_path, graph, library, settings)

    result = engine.upload_all()

    assert result.exported == 1
    assert state.get_link(1).item_id == "existing"
    assert state.get_link(2).name == "Jane Doe - Two (2).epub"
    index_calls = [call for call in graph.uploaded if call[2] == INDEX_FILENAME]
    assert len(index_calls) == 1
    assert index_calls[0][4] is None  # no index existed yet
    assert index_calls[0][5] == "replace"
    payload = json.loads(graph.uploaded_bytes[INDEX_FILENAME])
    assert {book["book_id"] for book in payload["books"]} == {1, 2}
    assert payload["version"] == 1
    state.close()


def test_upload_all_links_indexed_files_without_uploading_them(tmp_path):
    graph = FakeGraph()
    graph.recursive = [
        remote("idx", INDEX_FILENAME, size=10),
        remote("file", "some-weird-name.epub", size=4),
    ]
    write_index(graph, (1, "file", "some-weird-name.epub"))
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", export_enabled=True)
    engine, state = make_engine(tmp_path, graph, library, settings)

    result = engine.upload_all()

    assert result.exported == 0
    assert state.get_link(1).item_id == "file"
    index_calls = [call for call in graph.uploaded if call[2] == INDEX_FILENAME]
    assert index_calls[0][4] == "idx"  # updated in place
    payload = json.loads(graph.uploaded_bytes[INDEX_FILENAME])
    assert payload["books"] == [
        {
            "book_id": 1,
            "name": "some-weird-name.epub",
            "item_id": "file",
            "drive_id": "drive",
            "format": "epub",
            "size": 4,
            "e_tag": "",
        }
    ]
    state.close()


def test_download_all_imports_everything_without_matching(tmp_path):
    graph = FakeGraph()
    graph.recursive = [
        remote("a", "Jane Doe - The Book.epub", size=4),
        remote("b", "notes.txt", size=4),
        remote("c", "cover.jpg", size=4),
    ]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    ids = iter([10, 11])
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        add_book=lambda _path: next(ids),
    )

    result = engine.download_all()

    assert result.imported == 2  # the duplicate of book 1 is imported as well
    assert result.skipped == 0
    assert [item_id for _drive, item_id, _path in graph.downloaded] == ["a", "b"]
    assert state.is_imported("drive", "a")
    assert state.get_link(10).item_id == "a"
    assert INDEX_FILENAME in graph.uploaded_bytes
    state.close()


def test_download_all_skips_files_it_imported_before(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "Jane Doe - The Book.epub", size=4)]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, add_book=lambda _path: 10)

    first = engine.download_all()
    second = engine.download_all()

    assert (first.imported, first.skipped) == (1, 0)
    assert (second.imported, second.skipped) == (0, 1)
    state.close()


def test_clear_backup_deletes_the_folder_and_forgets_state(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub"), remote("sub", "Sub", folder=True)]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    state.link_book(BookLink(book_id=1, drive_id="drive", item_id="a", name="a.epub"))
    state.mark_exported(
        ExportRecord(book_id=1, fmt="epub", drive_id="drive", item_id="a", name="a.epub")
    )
    state.mark_imported("drive", "inner", name="inner.epub")
    state.set_remote_metadata("drive", "a", "Title", "Author")
    state.set_delta_link("drive", "folder", "delta")

    result = engine.clear_backup()

    assert result.deleted == 2
    assert graph.deleted == [("drive", "a"), ("drive", "sub")]
    assert state.get_link(1) is None
    assert state.get_export(1, "epub") is None
    # Everything for the emptied drive is forgotten, including older records.
    assert not state.is_imported("drive", "inner")
    assert state.get_remote_metadata("drive", "a") is None
    assert state.get_delta_link("drive", "folder") == ""
    state.close()


def test_clear_backup_keeps_state_when_cancelled(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub"), remote("b", "b.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        should_abort=lambda: len(graph.deleted) >= 1,
    )
    state.link_book(BookLink(book_id=1, drive_id="drive", item_id="a", name="a.epub"))

    result = engine.clear_backup()

    assert result.cancelled
    assert result.deleted == 1
    assert state.get_link(1) is not None
    state.close()


def test_index_available_only_when_usable(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    assert engine.index_available() is False

    graph.recursive = [remote("idx", INDEX_FILENAME)]
    graph.files["idx"] = b"not json"
    assert engine.index_available() is False

    write_index(graph, (1, "file", "a.epub"))
    assert engine.index_available() is True
    state.close()


def test_index_check_reads_the_file_without_listing_the_folder(tmp_path):
    graph = FakeGraph()
    write_index(graph, (1, "file", "a.epub"))
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder", folder_path="/data/ebook")
    engine, state = make_engine(tmp_path, graph, library, settings)

    assert engine.index_available() is True
    # A folder with thousands of files must not be listed just for one known file.
    assert graph.list_calls == []
    state.close()


def test_index_check_verifies_the_folder_when_the_file_is_missing(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    assert engine.index_available() is False
    assert graph.get_item_calls == [("drive", "folder")]
    state.close()


def test_index_check_reports_a_folder_that_is_gone(tmp_path):
    graph = FakeGraph()
    graph.get_item_error = GraphError(404, "item not found")
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    with pytest.raises(GraphError) as caught:
        engine.index_available()

    assert caught.value.status == 404
    state.close()


def test_index_check_is_skipped_when_cancelled(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, should_abort=lambda: True)

    assert engine.index_available() is False
    assert graph.list_calls == []
    state.close()


def test_scan_cancels_while_the_folder_is_being_listed(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("book", "Jane Doe - The Book.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    calls = {"n": 0}

    def abort_at_the_listing_page():
        calls["n"] += 1
        return calls["n"] >= 3

    engine, state = make_engine(
        tmp_path, graph, library, settings, should_abort=abort_at_the_listing_page
    )

    with pytest.raises(CancelledError):
        engine.scan()

    assert graph.list_calls == [("drive", "folder")]
    state.close()


def test_scan_cancels_right_after_the_listing(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("book", "Jane Doe - The Book.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    calls = {"n": 0}

    def abort_after_the_listing():
        calls["n"] += 1
        return calls["n"] >= 4

    engine, state = make_engine(
        tmp_path, graph, library, settings, should_abort=abort_after_the_listing
    )

    with pytest.raises(CancelledError):
        engine.scan()

    assert graph.list_calls == [("drive", "folder")]
    state.close()


def test_listing_never_enters_subfolders(tmp_path):
    graph = FakeGraph()
    graph.recursive = [
        remote("book", "Jane Doe - The Book.epub"),
        remote("sub", "Sub", folder=True),
        remote("gone", "gone.epub", deleted=True),
    ]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    plan = engine.scan()

    assert [entry.remote.id for entry in plan.entries if entry.remote is not None] == ["book"]
    assert graph.list_calls == [("drive", "folder")]
    state.close()


def test_upload_all_stops_before_listing_when_cancelled(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, should_abort=lambda: True)

    result = engine.upload_all()

    assert result.cancelled
    assert graph.list_calls == []
    state.close()


def test_upload_all_stops_after_listing_when_cancelled(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub")]
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    calls = {"count": 0}

    def abort():
        calls["count"] += 1
        return calls["count"] >= 2

    engine, state = make_engine(tmp_path, graph, library, settings, should_abort=abort)

    result = engine.upload_all()

    assert result.cancelled
    assert graph.list_calls == [("drive", "folder")]
    state.close()


def test_upload_all_reports_a_failing_index_write(tmp_path):
    graph = FakeGraph()
    graph.recursive = [
        remote("idx", INDEX_FILENAME, size=10),
        remote("file", "Jane Doe - The Book.epub"),
    ]
    write_index(graph, (1, "file", "Jane Doe - The Book.epub"))
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    graph.upload_error = OSError("disk full")

    result = engine.upload_all()

    assert result.exported == 0
    assert result.failed == 1
    assert any("OneDrive index" in error for error in result.errors)
    state.close()


def test_download_all_stops_when_cancelled(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub")]
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, should_abort=lambda: True)

    result = engine.download_all()

    assert result.cancelled
    assert graph.downloaded == []
    state.close()


def test_download_all_reports_a_failing_download(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub")]
    graph.download_error = OSError("network")
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    result = engine.download_all()

    assert result.imported == 0
    assert result.failed == 1
    assert any("a.epub" in error for error in result.errors)
    state.close()


def test_clear_backup_keeps_state_when_a_delete_fails(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub"), remote("b", "b.epub")]
    graph.delete_error = OSError("gone")
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    state.link_book(BookLink(book_id=1, drive_id="drive", item_id="a", name="a.epub"))

    result = engine.clear_backup()

    assert result.deleted == 0
    assert result.failed == 2
    assert state.get_link(1) is not None
    state.close()


def test_scan_aborts_before_reading_the_library(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, should_abort=lambda: True)

    with pytest.raises(CancelledError):
        engine.scan()
    assert graph.list_calls == []
    state.close()


# -- bulk actions: cancellation and per-row guards ---------------------------


def test_upload_all_stops_between_uploads_when_cancelled(tmp_path):
    """A sequential pass (``upload_workers = 1``) never starts the next file."""
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "One", "Jane Doe")
    _local_book(library, 2, "Two", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=1)
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        should_abort=lambda: len(graph.uploaded) >= 1,
    )

    result = engine.upload_all()

    assert result.cancelled
    assert result.exported == 1
    assert "Sync cancelled." in result.errors
    assert [call[2] for call in graph.uploaded if call[2] != INDEX_FILENAME] == [
        "Jane Doe - One (1).epub"
    ]
    # A cancelled pass is a pause, so the index is written anyway: the folder
    # already in OneDrive has to describe itself to the next scan.
    index = json.loads(graph.uploaded_bytes[INDEX_FILENAME])
    assert [book["name"] for book in index["books"]] == ["Jane Doe - One (1).epub"]
    state.close()


def test_upload_all_cancels_the_uploads_that_never_started(tmp_path):
    """Concurrent uploads: queueing stops, the file in flight is not cut off."""
    library = FakeLibrary()
    _local_book(library, 1, "One", "Jane Doe")
    _local_book(library, 2, "Two", "Jane Doe")
    started = threading.Event()
    release = threading.Event()
    graph = GatingGraph(started, release)
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=1)

    def should_abort():
        if not started.is_set():
            return False
        release.set()  # let the running upload finish, then stop the pass
        return True

    engine, state = make_engine(tmp_path, graph, library, settings, should_abort=should_abort)

    result = engine.upload_all()

    assert result.cancelled
    assert result.exported == 0
    assert [call[2] for call in graph.uploaded] == ["Jane Doe - One (1).epub"]
    state.close()


def test_uploads_run_in_parallel_up_to_the_configured_workers(tmp_path):
    """Three workers mean three files on the wire, not three files per second."""
    graph = ConcurrencyGraph(party=3)
    library = FakeLibrary()
    for book_id in range(1, 7):
        _local_book(library, book_id, f"Book {book_id}", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=3)
    engine, state = make_engine(tmp_path, graph, library, settings)

    result = engine.upload_all()

    assert result.exported == 6
    assert result.failed == 0
    assert graph.max_in_flight == 3
    # The pool, not the caller's thread, performs the network requests.
    assert threading.get_ident() not in graph.threads
    state.close()


def test_one_worker_uploads_exactly_one_file_at_a_time(tmp_path):
    graph = ConcurrencyGraph(party=1)
    library = FakeLibrary()
    for book_id in range(1, 4):
        _local_book(library, book_id, f"Book {book_id}", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=1)
    engine, state = make_engine(tmp_path, graph, library, settings)

    result = engine.upload_all()

    assert result.exported == 3
    assert graph.max_in_flight == 1
    state.close()


def test_the_sync_state_is_written_on_the_callers_thread(tmp_path):
    """Calibre and SQLite stay serial; only Graph runs in the pool."""
    graph = ConcurrencyGraph(party=2)
    library = FakeLibrary()
    for book_id in (1, 2):
        _local_book(library, book_id, f"Book {book_id}", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=2)
    engine, state = make_engine(tmp_path, graph, library, settings)
    written = []
    mark_exported = state.mark_exported

    def spy(record):
        written.append(threading.get_ident())
        return mark_exported(record)

    state.mark_exported = spy
    try:
        engine.upload_all()
    finally:
        state.close()

    assert len(written) == 2
    assert written == [threading.get_ident()] * 2
    assert threading.get_ident() not in graph.threads


def test_upload_all_reports_a_failing_upload(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "One", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    graph.upload_error = OSError("disk full")
    engine, state = make_engine(tmp_path, graph, library, settings)

    result = engine.upload_all()

    assert result.exported == 0
    assert result.failed == 1
    assert len(result.errors) == 1
    assert "disk full" in result.errors[0]
    # The failed row is named so the user knows which book broke.
    assert "One — Jane Doe (#1)" in result.errors[0]
    state.close()


def test_download_all_stops_between_downloads_when_cancelled(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub"), remote("b", "b.epub")]
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        add_book=lambda _path: 10,
        should_abort=lambda: len(graph.downloaded) >= 1,
    )

    result = engine.download_all()

    assert result.cancelled
    assert result.imported == 1
    assert "Sync cancelled." in result.errors
    # The imported file is in the index, so a paused import can be finished
    # later without importing it twice.
    index = json.loads(graph.uploaded_bytes[INDEX_FILENAME])
    assert [book["item_id"] for book in index["books"]] == ["a"]
    state.close()


def test_download_all_ignores_a_failing_index_write(tmp_path):
    graph = FailingIndexGraph()
    graph.recursive = [remote("a", "a.epub")]
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, add_book=lambda _path: 10)

    result = engine.download_all()

    # The imported books stay imported; only the index is lost.
    assert result.imported == 1
    assert result.failed == 0
    assert result.errors == []
    assert INDEX_FILENAME not in graph.uploaded_bytes
    state.close()


def test_clear_backup_stops_before_listing_when_cancelled(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub")]
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, should_abort=lambda: True)

    result = engine.clear_backup()

    assert result.cancelled
    assert "Sync cancelled." in result.errors
    assert graph.deleted == []
    assert graph.list_calls == []
    state.close()


def test_apply_plan_ignores_a_failing_index_write(tmp_path):
    graph = FailingIndexGraph()
    library = FakeLibrary()
    _local_book(library, 1, "Upload Me", "Author")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    plan = build_reconcile_plan(
        [LocalBook(book_id=1, title="Upload Me", authors="Author", formats=("epub",))],
        [],
    )
    selected = [entry for entry in plan.entries if entry.selectable]

    result = engine.apply_plan(plan, selected)

    assert result.exported == 1
    assert result.failed == 0
    assert result.errors == []
    state.close()


# -- local book helpers ------------------------------------------------------


def test_local_book_adds_an_extra_format_on_request(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe", fmt="epub")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    book = engine._local_book(1, extra_format="PDF")

    assert book.formats == ("epub", "pdf")
    # An already known format is not added twice.
    assert engine._local_book(1, extra_format="epub").formats == ("epub",)
    state.close()


def test_scan_survives_failing_identifier_lookups(tmp_path):
    class BrokenIdentifiers(FakeLibrary):
        def identifiers(self, book_id):
            raise RuntimeError("metadata locked")

    graph = FakeGraph()
    graph.recursive = [remote("named", "Jane Doe - The Book.epub")]
    library = BrokenIdentifiers()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    plan = engine.scan()

    # Broken book metadata must never stop a scan.
    assert plan.local_books[0].identifiers == {}
    assert plan.entries[0].status == "matched"
    state.close()


def test_local_book_falls_back_to_epub_when_the_preferred_format_is_absent(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe", fmt="epub")
    settings = Settings(drive_id="drive", folder_id="folder", export_format="pdf")
    engine, state = make_engine(tmp_path, graph, library, settings)

    book = engine._local_book(1)

    # The pdf preference has no file, so the epub one supplies size and mtime.
    assert book.formats == ("epub",)
    assert (book.size, book.mtime) == (4, 10.0)
    state.close()


# -- metadata probing --------------------------------------------------------


def test_scan_skips_the_metadata_probe_when_no_ebook_is_listed(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("notes", "notes.txt")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    seen = []
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        metadata_reader=lambda path, fmt: seen.append(path) or ("The Book", "Jane Doe"),
    )

    plan = engine.scan()

    assert plan.local_books
    assert seen == []
    assert graph.downloaded == []
    state.close()


def test_scan_keeps_files_whose_metadata_could_not_be_read(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("scan", "scan001.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(
        tmp_path,
        graph,
        library,
        settings,
        metadata_reader=lambda _path, _fmt: None,
    )

    plan = engine.scan()

    assert plan.entry("remote:drive:scan").status == "download"
    assert state.get_remote_metadata("drive", "scan") is None
    state.close()


# -- row-level guards --------------------------------------------------------


def test_apply_plan_reports_an_upload_row_without_a_local_book(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    entry = ReconcileEntry(key="upload:none", status="upload")

    result = engine.apply_plan(ReconcilePlan(entries=[entry]), [entry])

    assert result.failed == 1
    assert "No local book for this row." in result.errors[0]
    assert graph.uploaded == []
    state.close()


def test_apply_plan_skips_an_upload_row_without_a_syncable_format(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    entry = ReconcileEntry(
        key="upload:1",
        status="upload",
        local=LocalBook(book_id=1, title="Only MOBI", authors="Author", formats=("mobi",)),
    )

    result = engine.apply_plan(ReconcilePlan(entries=[entry]), [entry])

    assert result.skipped == 1
    assert result.exported == 0
    assert result.failed == 0
    assert graph.uploaded == []
    state.close()


def test_apply_plan_skips_an_upload_row_whose_file_is_missing(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    # The book claims an epub but calibre has no file for it.
    library.books.append(1)
    library.titles_map[1] = "The Book"
    library.authors_map[1] = "Jane Doe"
    library.formats_map[1] = ["epub"]
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    plan = build_reconcile_plan(
        [LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",))],
        [],
    )
    selected = [entry for entry in plan.entries if entry.selectable]

    result = engine.apply_plan(plan, selected)

    assert result.skipped == 1
    assert result.failed == 0
    assert graph.uploaded == []
    state.close()


def test_apply_plan_reuses_the_item_id_of_the_previous_export(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 2, "Two", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    state.mark_exported(
        ExportRecord(
            book_id=2,
            fmt="epub",
            drive_id="drive",
            item_id="exported",
            name="Jane Doe - Two (2).epub",
        )
    )
    plan = build_reconcile_plan(
        [LocalBook(book_id=2, title="Two", authors="Jane Doe", formats=("epub",))],
        [],
    )
    selected = [entry for entry in plan.entries if entry.selectable]

    result = engine.apply_plan(plan, selected)

    assert result.exported == 1
    # The unchanged name means the existing item is replaced in place.
    assert graph.uploaded[0][4] == "exported"
    state.close()


def test_apply_plan_reuses_the_item_id_of_the_linked_file(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 2, "Two", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    state.link_book(BookLink(book_id=2, drive_id="drive", item_id="linked", name="Old.epub"))
    plan = build_reconcile_plan(
        [LocalBook(book_id=2, title="Two", authors="Jane Doe", formats=("epub",))],
        [],
    )
    selected = [entry for entry in plan.entries if entry.selectable]

    result = engine.apply_plan(plan, selected)

    assert result.exported == 1
    # Without an export record the stored link still points at the same format.
    assert graph.uploaded[0][4] == "linked"
    state.close()


def test_apply_plan_reports_a_download_row_without_a_remote_file(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    entry = ReconcileEntry(key="remote:drive:none", status="download")

    result = engine.apply_plan(ReconcilePlan(entries=[entry]), [entry])

    assert result.failed == 1
    assert "No OneDrive file for this row." in result.errors[0]
    assert result.imported == 0
    state.close()


def test_apply_plan_reports_a_download_row_without_a_book_id(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("d", "Unknown - Download.epub")]
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, add_book=lambda _path: None)
    entry = ReconcileEntry(
        key="remote:drive:d",
        status="download",
        remote=remote("d", "Unknown - Download.epub"),
    )

    result = engine.apply_plan(ReconcilePlan(entries=[entry]), [entry])

    assert result.failed == 1
    assert "calibre did not return a book id." in result.errors[0]
    assert result.imported == 0
    state.close()


def test_apply_plan_reports_a_delete_row_without_a_remote_file(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    entry = ReconcileEntry(key="remote:drive:none", status="duplicate")

    result = engine.apply_plan(ReconcilePlan(entries=[entry]), [entry])

    assert result.failed == 1
    assert "No OneDrive file for this row." in result.errors[0]
    assert graph.deleted == []
    state.close()


def test_apply_plan_removes_the_link_of_a_deleted_duplicate(tmp_path):
    graph = FakeGraph()
    library = FakeLibrary()
    _local_book(library, 1, "The Book", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)
    # An older book of another library points at the copy that gets deleted.
    state.link_book(BookLink(book_id=9, drive_id="drive", item_id="extra", name="Copy.epub"))
    plan = build_reconcile_plan(
        [LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",))],
        [
            remote("m", "Jane Doe - The Book.epub"),
            remote("extra", "Jane Doe - The Book copy.epub"),
        ],
    )
    selected = [entry for entry in plan.entries if entry.status == "duplicate"]

    result = engine.apply_plan(plan, selected)

    assert result.deleted == 1
    assert graph.deleted == [("drive", "extra")]
    assert state.get_link(9) is None
    state.close()


# -- calibre index -----------------------------------------------------------


def test_index_check_raises_for_a_failing_index_download(tmp_path):
    graph = FakeGraph()
    write_index(graph, (1, "file", "a.epub"))
    graph.download_error = GraphError(500, "server error")
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings)

    with pytest.raises(GraphError) as caught:
        engine.index_available()

    # Only a missing index is normal; a broken drive must surface.
    assert caught.value.status == 500
    state.close()


def test_index_write_skips_links_without_an_item_id_or_name(tmp_path):
    graph = FakeGraph()
    graph.recursive = [remote("a", "a.epub")]
    library = FakeLibrary()
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, add_book=lambda _path: 10)
    state.link_book(BookLink(book_id=1, drive_id="drive", item_id="", name=""))

    result = engine.download_all()

    assert result.imported == 1
    payload = json.loads(graph.uploaded_bytes[INDEX_FILENAME])
    assert [book["book_id"] for book in payload["books"]] == [10]
    state.close()


def test_apply_plan_writes_the_index_when_it_is_cancelled(tmp_path):
    """A cancelled review pass is a pause, so the index is written first."""
    graph = FakeGraph()
    graph.recursive = [
        remote("idx", INDEX_FILENAME, size=10),
        remote("m", "Author - Matched.epub"),
        remote("d", "Unknown - Download.epub"),
    ]
    library = FakeLibrary()
    _local_book(library, 1, "Matched", "Author", fmt="epub")
    settings = Settings(drive_id="drive", folder_id="folder")
    engine, state = make_engine(tmp_path, graph, library, settings, should_abort=lambda: True)
    plan = build_reconcile_plan(
        [LocalBook(book_id=1, title="Matched", authors="Author", formats=("epub",))],
        [item for item in graph.recursive if item.name != INDEX_FILENAME],
        preference="epub",
    )
    selected = [entry for entry in plan.entries if entry.selectable]
    assert selected, "the plan needs a row the pass will not reach"

    result = engine.apply_plan(plan, selected)

    assert result.cancelled
    assert result.imported == 0
    # The matched book was recognised before the pass stopped, and that
    # knowledge is in the folder, not only in the local state.
    index = json.loads(graph.uploaded_bytes[INDEX_FILENAME])
    assert [book["item_id"] for book in index["books"]] == ["m"]
    state.close()


def test_the_index_is_rewritten_after_every_uploaded_book(tmp_path):
    """A pass that is paused halfway leaves an index that knows about book 7."""
    graph = FakeGraph()
    library = FakeLibrary()
    for book_id in (1, 2, 3):
        _local_book(library, book_id, f"Book {book_id}", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=1)
    engine, state = make_engine(tmp_path, graph, library, settings)

    result = engine.upload_all()

    writes = [payload for name, payload in graph.upload_history if name == INDEX_FILENAME]
    assert [len(json.loads(payload)["books"]) for payload in writes] == [1, 2, 3]
    assert result.exported == 3
    state.close()


def test_the_index_rewrite_replaces_the_same_file(tmp_path):
    """One index file per pass, not one ``(1)``/``(2)`` copy per flush."""
    graph = FakeGraph()
    library = FakeLibrary()
    for book_id in (1, 2, 3):
        _local_book(library, book_id, f"Book {book_id}", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=1)
    engine, state = make_engine(tmp_path, graph, library, settings)

    engine.upload_all()

    index_calls = [call for call in graph.uploaded if call[2] == INDEX_FILENAME]
    assert len(index_calls) == 3
    # The first write has nothing to replace; every later one reuses the item
    # Graph handed back, which is what keeps the folder free of duplicates.
    assert index_calls[0][4] in (None, "")
    assert [call[4] for call in index_calls[1:]] == ["uploaded", "uploaded"]
    assert {call[5] for call in index_calls} == {"replace"}
    state.close()


def test_a_failing_index_write_is_retried_with_growing_gaps(tmp_path):
    """Four books and a dead index endpoint cost two requests, not four."""
    graph = CountingFailingIndexGraph()
    library = FakeLibrary()
    for book_id in (1, 2, 3, 4):
        _local_book(library, book_id, f"Book {book_id}", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=1)
    engine, state = make_engine(tmp_path, graph, library, settings)

    result = engine.upload_all()

    assert result.exported == 4
    assert result.failed == 2
    assert graph.index_attempts == 2
    assert INDEX_FILENAME not in graph.uploaded_bytes
    state.close()


def test_clear_backup_forgets_the_index_file_it_replaced(tmp_path):
    """After an empty folder, the next pass must create the index again."""
    graph = ClearingGraph()
    graph.recursive = [remote("idx", INDEX_FILENAME, size=10), remote("a", "a.epub")]
    library = FakeLibrary()
    _local_book(library, 1, "Book 1", "Jane Doe")
    settings = Settings(drive_id="drive", folder_id="folder", upload_workers=1)
    engine, state = make_engine(tmp_path, graph, library, settings)

    engine.clear_backup()
    result = engine.upload_all()

    assert result.exported == 1
    index_calls = [call for call in graph.uploaded if call[2] == INDEX_FILENAME]
    assert index_calls[0][4] in (None, "")
    state.close()
