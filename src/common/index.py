"""A JSON index that maps stored OneDrive files to calibre book ids.

The plugin writes ``calibre-onedrive-index.json`` into the selected OneDrive
folder whenever it uploads books. A later scan can then match files by item id
(robust against renames) or by their unique filename, instead of guessing from
title and author. That is what makes a first sync against a folder that was
filled by a previous export reliable.

The module is GUI-free and network-free so it can be unit-tested on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .models import RemoteItem

#: Name of the index file in the selected OneDrive folder.
INDEX_FILENAME = "calibre-onedrive-index.json"
INDEX_VERSION = 1


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class IndexEntry:
    """One calibre book and the OneDrive file that holds it."""

    book_id: int
    name: str
    item_id: str = ""
    drive_id: str = ""
    fmt: str = ""
    size: int = 0
    e_tag: str = ""


@dataclass
class BookIndex:
    """The parsed contents of :data:`INDEX_FILENAME`."""

    version: int = INDEX_VERSION
    generated: str = ""
    library: str = ""
    books: list[IndexEntry] = field(default_factory=list)

    @classmethod
    def from_json(cls, text: str) -> BookIndex:
        """Parse an index, skipping entries that make no sense.

        Raises :class:`ValueError` when the document itself is unusable; the
        caller treats that like a missing index.
        """
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("the OneDrive index is not a JSON object")
        entries: list[IndexEntry] = []
        seen: set[int] = set()
        for raw in data.get("books") or []:
            if not isinstance(raw, dict):
                continue
            book_id = _as_int(raw.get("book_id"))
            name = str(raw.get("name") or "")
            if book_id <= 0 or not name or book_id in seen:
                continue
            seen.add(book_id)
            entries.append(
                IndexEntry(
                    book_id=book_id,
                    name=name,
                    item_id=str(raw.get("item_id") or ""),
                    drive_id=str(raw.get("drive_id") or ""),
                    fmt=str(raw.get("format") or raw.get("fmt") or "").lower(),
                    size=_as_int(raw.get("size")),
                    e_tag=str(raw.get("e_tag") or ""),
                )
            )
        entries.sort(key=lambda entry: entry.book_id)
        return cls(
            version=_as_int(data.get("version"), INDEX_VERSION),
            generated=str(data.get("generated") or ""),
            library=str(data.get("library") or ""),
            books=entries,
        )

    def to_json(self) -> str:
        """Serialise the index as pretty-printed JSON (newline terminated)."""
        payload = {
            "version": self.version,
            "generated": self.generated,
            "library": self.library,
            "books": [
                {
                    "book_id": entry.book_id,
                    "name": entry.name,
                    "item_id": entry.item_id,
                    "drive_id": entry.drive_id,
                    "format": entry.fmt,
                    "size": entry.size,
                    "e_tag": entry.e_tag,
                }
                for entry in self.books
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    def item_links(self) -> dict[tuple[str, str], int]:
        """``(drive_id, item_id) -> book_id`` for every indexed file."""
        return {
            (entry.drive_id, entry.item_id): entry.book_id for entry in self.books if entry.item_id
        }

    def name_links(self) -> dict[str, int]:
        """Unique lower-case filenames, so a re-uploaded file still matches."""
        by_name: dict[str, set[int]] = {}
        for entry in self.books:
            by_name.setdefault(entry.name.casefold(), set()).add(entry.book_id)
        return {name: next(iter(ids)) for name, ids in by_name.items() if len(ids) == 1}

    def entry_for(self, book_id: int) -> IndexEntry | None:
        """Return the entry for ``book_id``, if it is indexed."""
        for entry in self.books:
            if entry.book_id == int(book_id):
                return entry
        return None


def build_index(
    book_remotes: dict[int, RemoteItem],
    *,
    generated: str = "",
    library: str = "",
) -> BookIndex:
    """Build an index from ``book_id -> remote item`` pairs."""
    entries = [
        IndexEntry(
            book_id=int(book_id),
            name=remote.name,
            item_id=remote.id,
            drive_id=remote.drive_id,
            fmt=remote.extension,
            size=int(remote.size),
            e_tag=remote.e_tag,
        )
        for book_id, remote in sorted(book_remotes.items())
        if remote.id and remote.name
    ]
    return BookIndex(generated=generated, library=library, books=entries)
