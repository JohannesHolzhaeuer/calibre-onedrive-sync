"""Tests for the SQLite sync state."""

from __future__ import annotations

from onedrive_sync.common.models import BookLink, ExportRecord
from onedrive_sync.common.state import StateStore, default_state_path


def test_meta_and_delta_links(tmp_path):
    with StateStore(str(tmp_path / "state.sqlite")) as state:
        assert state.get_meta("missing") == ""
        assert state.get_meta("missing", "fallback") == "fallback"
        state.set_meta("key", "value")
        assert state.get_meta("key") == "value"
        state.set_meta("key", "updated")
        assert state.get_meta("key") == "updated"

        assert state.get_delta_link("drive", "folder") == ""
        state.set_delta_link("drive", "folder", "link")
        assert state.get_delta_link("drive", "folder") == "link"
        assert state.get_delta_link("drive", None) == ""
        state.set_delta_link("drive", None, "root-link")
        assert state.get_delta_link("drive", None) == "root-link"


def test_imported_items(tmp_path):
    with StateStore(str(tmp_path / "state.sqlite")) as state:
        assert not state.is_imported("drive", "item")
        state.mark_imported("drive", "item", name="a.epub", e_tag="etag", size=10, book_id=1)
        assert state.is_imported("drive", "item")
        state.mark_imported("drive", "item", name="b.epub", size=20, book_id=2)
        assert state.is_imported("drive", "item")


def test_export_records(tmp_path):
    with StateStore(str(tmp_path / "state.sqlite")) as state:
        assert state.get_export(1, "epub") is None
        assert not state.is_exported(1, "epub", 1.0, 10)
        record = ExportRecord(
            book_id=1,
            fmt="epub",
            drive_id="drive",
            item_id="item",
            name="book.epub",
            e_tag="etag",
            size=10,
            local_mtime=1.0,
        )
        state.mark_exported(record)
        loaded = state.get_export(1, "epub")
        assert loaded == record
        assert state.is_exported(1, "epub", 1.0, 10)
        assert not state.is_exported(1, "epub", 2.0, 10)
        assert not state.is_exported(1, "epub", 1.0, 11)
        assert state.is_exported_item("drive", "item")
        assert not state.is_exported_item("drive", "other")

        record2 = ExportRecord(
            book_id=1,
            fmt="EPUB",
            drive_id="drive2",
            item_id="item2",
            name="book2.epub",
            size=11,
            local_mtime=2.0,
        )
        state.mark_exported(record2)
        assert state.get_export(1, "epub").item_id == "item2"


def test_default_state_path_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CALIBRE_CONFIG_DIRECTORY", str(tmp_path))
    expected = tmp_path / "plugins" / "onedrive_sync" / "state.sqlite"
    assert default_state_path() == str(expected)


def test_book_links_round_trip(tmp_path):
    with StateStore(str(tmp_path / "state.sqlite")) as state:
        assert state.get_link(1) is None
        link = BookLink(
            book_id=1,
            drive_id="drive",
            item_id="item",
            name="Book.epub",
            web_url="https://onedrive.test/item",
            size=10,
            e_tag="etag",
        )
        state.link_book(link)
        assert state.get_link(1) == link
        assert state.get_link_by_item("drive", "item") == link
        assert state.get_link_by_item("other", "item") is None
        assert state.all_links() == [link]

        updated = BookLink(book_id=1, drive_id="drive2", item_id="item2", name="Book.pdf")
        state.link_book(updated)
        assert state.get_link(1) == updated
        assert state.all_links() == [updated]

        state.unlink_book(1)
        assert state.get_link(1) is None
        assert state.all_links() == []


def test_prune_links_forgets_links_to_deleted_files(tmp_path):
    with StateStore(str(tmp_path / "state.sqlite")) as state:
        state.link_book(BookLink(book_id=1, drive_id="drive", item_id="live", name="A.epub"))
        state.link_book(BookLink(book_id=2, drive_id="drive", item_id="gone", name="B.epub"))
        state.link_book(BookLink(book_id=3, drive_id="other", item_id="elsewhere", name="C.epub"))

        stale = state.prune_links("drive", {"live"})

        assert [link.book_id for link in stale] == [2]
        assert state.get_link(2) is None
        assert state.get_link(1) is not None
        # Another drive is none of our business.
        assert state.get_link(3) is not None
        assert [link.book_id for link in state.all_links()] == [1, 3]


def test_imported_link_by_book_and_remote_metadata(tmp_path):
    with StateStore(str(tmp_path / "state.sqlite")) as state:
        assert state.get_import_by_book(1) is None
        state.mark_imported("drive", "item", name="Book.epub", e_tag="v1", size=4, book_id=1)
        state.mark_imported("drive", "item2", name="Book.pdf", e_tag="v2", size=8, book_id=1)
        link = state.get_import_by_book(1)
        assert link is not None
        assert link.item_id == "item2"
        assert link.name == "Book.pdf"
        assert link.size == 8

        assert state.get_remote_metadata("drive", "missing", "etag") is None
        state.set_remote_metadata("drive", "item", "Title", "Author", e_tag="v1")
        assert state.get_remote_metadata("drive", "item", "v1") == ("Title", "Author")
        assert state.get_remote_metadata("drive", "item") == ("Title", "Author")
        # A newer eTag invalidates the cached metadata.
        assert state.get_remote_metadata("drive", "item", "v2") is None
        state.set_remote_metadata("drive", "item", "New", "Writer", e_tag="v2")
        assert state.get_remote_metadata("drive", "item", "v2") == ("New", "Writer")


def test_forget_drive_drops_every_record_and_delta_link(tmp_path):
    with StateStore(str(tmp_path / "state.sqlite")) as state:
        state.mark_imported("drive", "item", name="a.epub", book_id=1)
        state.link_book(BookLink(book_id=1, drive_id="drive", item_id="item", name="a.epub"))
        state.mark_exported(
            ExportRecord(book_id=1, fmt="epub", drive_id="drive", item_id="item", name="a.epub")
        )
        state.set_remote_metadata("drive", "item", "Title", "Author")
        state.set_delta_link("drive", "folder", "delta")
        state.mark_imported("other", "keep", name="keep.epub", book_id=2)

        state.forget_drive("drive")

        assert not state.is_imported("drive", "item")
        assert state.get_link(1) is None
        assert state.get_export(1, "epub") is None
        assert state.get_remote_metadata("drive", "item") is None
        assert state.get_delta_link("drive", "folder") == ""
        # Records for another drive are untouched.
        assert state.is_imported("other", "keep")
