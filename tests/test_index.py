"""Tests for the calibre OneDrive index file."""

from __future__ import annotations

import json

import pytest

from onedrive_sync.common.index import (
    INDEX_FILENAME,
    BookIndex,
    IndexEntry,
    build_index,
)
from onedrive_sync.common.models import RemoteItem


def remote(item_id, name, *, drive="drive", size=0):
    return RemoteItem(id=item_id, name=name, drive_id=drive, size=size)


def test_index_filename_is_stable():
    assert INDEX_FILENAME == "calibre-onedrive-index.json"
    assert INDEX_FILENAME.endswith(".json")


def test_round_trip_sorts_books_and_keeps_metadata():
    index = BookIndex(
        generated="2026-10-27T10:00:00Z",
        library="Calibre",
        books=[
            IndexEntry(book_id=2, name="b.epub", item_id="i2", drive_id="d", fmt="epub", size=4),
            IndexEntry(book_id=1, name="a.pdf", item_id="i1", drive_id="d", fmt="pdf"),
        ],
    )
    text = index.to_json()
    assert text.endswith("\n")
    parsed = BookIndex.from_json(text)

    assert [entry.book_id for entry in parsed.books] == [1, 2]
    assert parsed.generated == "2026-10-27T10:00:00Z"
    assert parsed.library == "Calibre"
    assert parsed.entry_for(2).name == "b.epub"
    assert parsed.entry_for(2).size == 4
    assert parsed.entry_for(99) is None
    assert parsed.item_links() == {("d", "i1"): 1, ("d", "i2"): 2}


def test_from_json_skips_entries_that_make_no_sense():
    text = json.dumps(
        {
            "books": [
                {"book_id": "x", "name": "a.epub"},
                {"book_id": 1, "name": ""},
                "not an entry",
                {"book_id": 2, "name": "b.epub", "item_id": "i"},
                {"book_id": 2, "name": "duplicate.epub", "item_id": "j"},
                {"book_id": 3, "name": "c.epub", "format": "PDF", "size": "12"},
            ]
        }
    )
    index = BookIndex.from_json(text)

    assert [entry.book_id for entry in index.books] == [2, 3]
    assert index.entry_for(3).fmt == "pdf"
    assert index.entry_for(3).size == 12


def test_from_json_rejects_a_non_object_document():
    with pytest.raises(ValueError):
        BookIndex.from_json("[]")
    with pytest.raises(ValueError):
        BookIndex.from_json("not json")


def test_name_links_are_only_kept_for_unique_names():
    index = BookIndex(
        books=[
            IndexEntry(1, "Same.epub", item_id="a"),
            IndexEntry(2, "same.EPUB", item_id="b"),
            IndexEntry(3, "Unique.epub", item_id="c"),
        ]
    )
    assert index.name_links() == {"unique.epub": 3}


def test_build_index_from_remote_items():
    index = build_index(
        {
            2: remote("i2", "B.epub", size=5),
            1: remote("i1", "A.epub"),
        },
        generated="now",
    )

    assert [entry.book_id for entry in index.books] == [1, 2]
    assert index.books[1].fmt == "epub"
    assert index.books[1].size == 5
    assert index.books[1].item_id == "i2"
    assert index.generated == "now"


def test_build_index_skips_incomplete_remotes():
    index = build_index({1: remote("", "a.epub"), 2: remote("i2", "")})
    assert index.books == []
