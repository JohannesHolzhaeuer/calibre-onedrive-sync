"""Small data objects shared by the OAuth/Graph/sync layers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DriveInfo:
    """The signed-in user's OneDrive returned by ``/me/drive``."""

    id: str
    name: str
    drive_type: str = ""
    owner: str = ""


@dataclass(frozen=True)
class RemoteItem:
    """A file or folder in a drive."""

    id: str
    name: str
    drive_id: str
    parent_id: str = ""
    path: str = ""
    size: int = 0
    e_tag: str = ""
    c_tag: str = ""
    last_modified: str = ""
    is_folder: bool = False
    deleted: bool = False
    mime_type: str = ""
    web_url: str = ""

    @property
    def extension(self) -> str:
        """Lower-case extension without the dot (``''`` for folders)."""
        if self.is_folder:
            return ""
        return os.path.splitext(self.name)[1].lstrip(".").lower()


@dataclass(frozen=True)
class FolderRef:
    """A selected folder in the user's OneDrive."""

    drive_id: str
    item_id: str
    name: str = ""
    path: str = ""

    def display(self) -> str:
        """Human-readable location, falling back to the folder name/id."""
        return self.path or self.name or self.item_id


@dataclass
class SyncResult:
    """Outcome of one sync run."""

    imported: int = 0
    exported: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-line summary suitable for a dialog/status bar."""
        prefix = "Cancelled. " if self.cancelled else ""
        return (
            f"{prefix}Imported {self.imported}, exported {self.exported}, "
            f"deleted {self.deleted}, skipped {self.skipped}, failed {self.failed}."
        )


@dataclass(frozen=True)
class ExportRecord:
    """Where a calibre book was uploaded (used to update it in place)."""

    book_id: int
    fmt: str
    drive_id: str
    item_id: str
    name: str
    e_tag: str = ""
    size: int = 0
    local_mtime: float = 0.0


@dataclass(frozen=True)
class BookLink:
    """The OneDrive item that represents a calibre book."""

    book_id: int
    drive_id: str
    item_id: str
    name: str = ""
    web_url: str = ""
    size: int = 0
    e_tag: str = ""

    def matches(self, remote: RemoteItem) -> bool:
        """True when ``remote`` is the linked OneDrive item."""
        return bool(self.item_id) and self.item_id == remote.id

    @classmethod
    def from_remote(cls, book_id: int, remote: RemoteItem) -> BookLink:
        """Create a link from a Graph item."""
        return cls(
            book_id=int(book_id),
            drive_id=remote.drive_id,
            item_id=remote.id,
            name=remote.name,
            web_url=remote.web_url,
            size=int(remote.size),
            e_tag=remote.e_tag,
        )

    @property
    def extension(self) -> str:
        """Lower-case extension of the linked file (``''`` when unknown)."""
        return os.path.splitext(self.name)[1].lstrip(".").lower()


#: calibre identifier key used to persist the OneDrive link in book metadata.
ONEDRIVE_IDENTIFIER = "onedrive"
