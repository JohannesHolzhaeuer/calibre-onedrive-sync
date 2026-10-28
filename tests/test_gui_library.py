"""Tests for the calibre database adapter."""

from __future__ import annotations

import sys
import types

from onedrive_sync.gui.library import CalibreLibrary, add_book_to_library, read_metadata


class FakeApi:
    def __init__(self) -> None:
        self.books = [1, 2]
        self.fields = {}
        self.paths = {}
        self.raise_path = False
        self.added = []
        self.add_result = [7]
        self.identifiers_calls = []
        self.field_calls = []

    def all_book_ids(self):
        return self.books

    def field_for(self, name, book_id, default_value=None):
        value = self.fields.get((name, book_id), default_value)
        return value

    def format_abspath(self, book_id, fmt):
        if self.raise_path:
            raise RuntimeError("bad")
        return self.paths.get((book_id, fmt))

    def add_books(self, records, add_duplicates=True):
        self.added.append((records, add_duplicates))
        return self.add_result, []

    def set_identifiers(self, book_id, identifiers):
        self.identifiers_calls.append((book_id, dict(identifiers)))
        self.fields[("identifiers", book_id)] = dict(identifiers)

    def set_field(self, name, values):
        self.field_calls.append((name, dict(values)))
        for book_id, value in values.items():
            self.fields[(name, book_id)] = value


class FakeData:
    def __init__(self) -> None:
        self.added = []
        self.raise_added = False

    def books_added(self, ids):
        if self.raise_added:
            raise RuntimeError("model gone")
        self.added.append(ids)


class FakeModel:
    def __init__(self) -> None:
        self.db = types.SimpleNamespace(data=FakeData())
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def books_added(self, _count):
        pass


class FakeDb:
    def __init__(self) -> None:
        self.new_api = FakeApi()
        self.format_calls = []
        self.format_path = None
        self.library_path = "C:/Books"

    def format(self, book_id, fmt, as_file=False):
        self.format_calls.append((book_id, fmt, as_file))
        return self.format_path


class FakeGui:
    def __init__(self) -> None:
        self.current_db = FakeDb()
        self._model = FakeModel()
        self.selected = []
        self.tags_recounted = 0
        self.covers_refreshed = 0
        self.library_view = types.SimpleNamespace(
            model=lambda: self._model, select_rows=self.selected.extend
        )

    def refresh_cover_browser(self):
        self.covers_refreshed += 1

    @property
    def tags_view(self):
        return types.SimpleNamespace(recount=lambda: setattr(self, "tags_recounted", 1))


def test_library_metadata():
    gui = FakeGui()
    api = gui.current_db.new_api
    api.fields = {
        ("title", 1): "Book",
        ("authors", 1): ["A", "B"],
        ("authors", 2): "Solo",
        ("formats", 1): {"EPUB": "x", "PDF": "y"},
        ("formats", 2): "MOBI",
    }
    library = CalibreLibrary(gui)
    assert library.all_books() == [1, 2]
    assert library.title(1) == "Book"
    assert library.authors(1) == "A & B"
    assert library.authors(2) == "Solo"
    assert sorted(library.formats(1)) == ["epub", "pdf"]
    assert library.formats(2) == ["mobi"]


def test_library_formats_handles_fallback():
    gui = FakeGui()
    gui.current_db.new_api.fields = {("formats", 1): None}
    library = CalibreLibrary(gui)
    assert library.formats(1) == []


def test_library_format_file(monkeypatch, tmp_path):
    gui = FakeGui()
    path = tmp_path / "book.epub"
    path.write_bytes(b"book")
    gui.current_db.new_api.paths = {(1, "epub"): str(path)}
    library = CalibreLibrary(gui)
    info = library.format_file(1, "epub")
    assert info is not None
    assert info[0] == str(path)
    assert info[2] == 4

    gui.current_db.new_api.paths = {}
    assert library.format_file(1, "epub") is None


def test_library_format_file_uses_legacy_db(monkeypatch, tmp_path):
    gui = FakeGui()
    path = tmp_path / "book.epub"
    path.write_bytes(b"book")
    gui.current_db.new_api.format_abspath = None
    gui.current_db.format_path = str(path)
    library = CalibreLibrary(gui)
    info = library.format_file(1, "epub")
    assert info is not None
    assert gui.current_db.format_calls == [(1, "EPUB", True)]


def test_library_format_file_ignores_errors(tmp_path):
    gui = FakeGui()
    gui.current_db.new_api.raise_path = True
    gui.current_db.format_path = None
    library = CalibreLibrary(gui)
    assert library.format_file(1, "epub") is None


def test_library_stat_failure(monkeypatch, tmp_path):
    gui = FakeGui()
    gui.current_db.new_api.paths = {(1, "epub"): str(tmp_path / "missing.epub")}
    library = CalibreLibrary(gui)
    assert library.format_file(1, "epub") is None


def install_metadata_shim(monkeypatch):
    module = types.ModuleType("calibre.ebooks.metadata.meta")
    module.get_metadata = lambda stream, fmt: {
        "title": "Book",
        "authors": ["Author", "Two"],
        "fmt": fmt,
    }
    package = types.ModuleType("calibre.ebooks")
    metadata = types.ModuleType("calibre.ebooks.metadata")
    metadata.meta = module
    monkeypatch.setitem(sys.modules, "calibre.ebooks", package)
    monkeypatch.setitem(sys.modules, "calibre.ebooks.metadata", metadata)
    monkeypatch.setitem(sys.modules, "calibre.ebooks.metadata.meta", module)


def test_library_identifiers_and_set_identifier():
    gui = FakeGui()
    api = gui.current_db.new_api
    api.fields[("identifiers", 1)] = {"isbn": "123", "onedrive": "item"}
    library = CalibreLibrary(gui)
    assert library.identifiers(1) == {"isbn": "123", "onedrive": "item"}
    assert library.identifiers(2) == {}

    library.set_identifier(1, "onedrive", "https://onedrive.test/book")
    assert api.identifiers_calls[-1] == (
        1,
        {"isbn": "123", "onedrive": "https://onedrive.test/book"},
    )
    library.set_identifier(1, "onedrive", "")
    assert api.identifiers_calls[-1] == (1, {"isbn": "123"})

    api.set_identifiers = None
    library.set_identifier(2, "onedrive", "item-2")
    assert api.field_calls[-1] == ("identifiers", {2: {"onedrive": "item-2"}})

    api.set_field = None
    legacy_calls = []
    gui.current_db.set_identifiers = lambda book_id, identifiers: legacy_calls.append(
        (book_id, identifiers)
    )
    library.set_identifier(2, "onedrive", "item-3")
    assert legacy_calls == [(2, {"onedrive": "item-3"})]


def test_library_identifiers_accepts_pair_list():
    gui = FakeGui()
    gui.current_db.new_api.fields[("identifiers", 1)] = [("isbn", "1"), ("bad",)]
    library = CalibreLibrary(gui)
    assert library.identifiers(1) == {"isbn": "1"}


def test_read_metadata(monkeypatch, tmp_path):
    install_metadata_shim(monkeypatch)
    path = tmp_path / "book.epub"
    path.write_bytes(b"book")
    assert read_metadata(str(path), "epub") == ("Book", "Author & Two")
    assert read_metadata(str(path), "") == ("Book", "Author & Two")
    assert read_metadata(str(tmp_path / "missing.epub"), "epub") is None


def test_read_metadata_without_reader(monkeypatch, tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"book")
    monkeypatch.delitem(sys.modules, "calibre.ebooks.metadata.meta", raising=False)

    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("calibre.ebooks.metadata"):
            raise ImportError("no calibre")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert read_metadata(str(path), "epub") is None


def test_metadata_title_authors_handles_objects():
    from onedrive_sync.gui.library import _metadata_title_authors

    class Meta:
        title = "T"
        authors = ("A", "")

    assert _metadata_title_authors(Meta()) == ("T", "A")
    assert _metadata_title_authors(None) == ("", "")
    assert _metadata_title_authors({"title": "T", "authors": "A"}) == ("T", "A")


def test_add_book_to_library(monkeypatch, tmp_path):
    install_metadata_shim(monkeypatch)
    gui = FakeGui()
    path = tmp_path / "book.epub"
    path.write_bytes(b"book")
    assert add_book_to_library(gui, str(path)) == 7
    records = gui.current_db.new_api.added[0][0]
    assert records[0][1]["EPUB"] == str(path)
    assert gui._model.db.data.added == [[7]]
    assert gui.tags_recounted == 1
    assert gui.covers_refreshed == 1
    assert gui.selected == [7]


def test_add_book_without_extension_returns_none(tmp_path):
    gui = FakeGui()
    path = tmp_path / "book"
    path.write_bytes(b"book")
    assert add_book_to_library(gui, str(path)) is None


def test_add_book_without_ids_returns_none(monkeypatch, tmp_path):
    install_metadata_shim(monkeypatch)
    gui = FakeGui()
    gui.current_db.new_api.add_result = []
    path = tmp_path / "book.epub"
    path.write_bytes(b"book")
    assert add_book_to_library(gui, str(path)) is None


def test_refresh_falls_back_when_incremental_fails(monkeypatch, tmp_path):
    install_metadata_shim(monkeypatch)
    gui = FakeGui()
    gui._model.db.data.raise_added = True
    path = tmp_path / "book.epub"
    path.write_bytes(b"book")
    add_book_to_library(gui, str(path))
    assert gui._model.refreshed == 1


def test_clear_identifier_removes_the_key_from_every_book():
    gui = FakeGui()
    api = gui.current_db.new_api
    api.books = [1, 2, 3]
    api.fields[("identifiers", 1)] = {"isbn": "1", "onedrive": "item-1"}
    api.fields[("identifiers", 2)] = {"isbn": "2"}
    api.fields[("identifiers", 3)] = {"onedrive": "item-3"}
    library = CalibreLibrary(gui)

    assert library.clear_identifier("onedrive") == 2

    assert api.identifiers_calls == [
        (1, {"isbn": "1"}),
        (3, {}),
    ]
    assert library.identifiers(1) == {"isbn": "1"}
    assert library.identifiers(2) == {"isbn": "2"}
    assert library.identifiers(3) == {}


def test_clear_identifier_is_a_no_op_without_matches():
    gui = FakeGui()
    api = gui.current_db.new_api
    api.fields[("identifiers", 1)] = {"isbn": "1"}
    library = CalibreLibrary(gui)

    assert library.clear_identifier("onedrive") == 0
    assert api.identifiers_calls == []
