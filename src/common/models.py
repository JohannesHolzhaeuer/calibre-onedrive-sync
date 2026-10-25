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
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-line summary suitable for a dialog/status bar."""
        return (
            f"Imported {self.imported}, exported {self.exported}, "
            f"skipped {self.skipped}, failed {self.failed}."
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
