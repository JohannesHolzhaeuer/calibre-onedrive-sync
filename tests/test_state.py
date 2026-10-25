"""Tests for the SQLite sync state."""

from __future__ import annotations

from onedrive_sync.common.models import ExportRecord
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
