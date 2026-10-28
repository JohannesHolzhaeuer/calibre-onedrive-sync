"""Tests for the OneDrive folder picker."""

from __future__ import annotations

from qt.core import QDialog

from onedrive_sync.common.models import DriveInfo, FolderRef, RemoteItem
from onedrive_sync.gui import folder_picker
from onedrive_sync.gui.folder_picker import (
    FolderPickerDialog,
    _LoadDrives,
    _LoadFolders,
    open_folder_picker,
)


class FakeGraph:
    def __init__(self) -> None:
        self.drives = [DriveInfo("drive", "OneDrive", owner="Alice")]
        self.fail_drives = False
        self.fail_folders = False
        self.children = {
            ("root", "drive"): [
                RemoteItem("folder", "Books", "drive", is_folder=True),
                RemoteItem("file", "file.epub", "drive"),
            ],
            ("folder", "drive"): [
                RemoteItem("nested", "Nested", "drive", is_folder=True),
            ],
        }

    def list_drives(self):
        if self.fail_drives:
            raise RuntimeError("no drives")
        return list(self.drives)

    def get_root_item(self, drive_id):
        if self.fail_folders:
            raise RuntimeError("no root")
        return RemoteItem("root", "OneDrive", drive_id, is_folder=True)

    def list_children(self, drive_id, folder_id=None):
        if self.fail_folders:
            raise RuntimeError("no folders")
        return list(self.children.get((folder_id, drive_id), []))


def test_load_drives_worker():
    worker = _LoadDrives(FakeGraph())
    loaded = []
    worker.loaded.connect(loaded.append)
    worker.run()
    assert loaded[0][0].id == "drive"

    graph = FakeGraph()
    graph.fail_drives = True
    failed = []
    worker = _LoadDrives(graph)
    worker.failed.connect(failed.append)
    worker.run()
    assert failed == ["no drives"]


def test_load_folders_worker():
    worker = _LoadFolders(FakeGraph(), "drive", None)
    loaded = []
    worker.loaded.connect(loaded.append)
    worker.run()
    assert loaded[0]["root"].id == "root"
    assert loaded[0]["children"][0].name == "Books"

    graph = FakeGraph()
    graph.fail_folders = True
    failed = []
    worker = _LoadFolders(graph, "drive", None)
    worker.failed.connect(failed.append)
    worker.run()
    assert failed == ["no root"]


def test_tree_loads_root_and_expands_folders():
    dialog = FolderPickerDialog(FakeGraph())
    dialog.start()
    dialog._thread.run()
    dialog._thread.run()

    assert dialog.tree.topLevelItemCount() == 1
    root = dialog.tree.topLevelItem(0)
    assert root.text(0) == "OneDrive"
    assert root.childCount() == 1
    assert root.child(0).text(0) == "Books"

    books = root.child(0)
    dialog.tree.expandItem(books)
    dialog._thread.run()
    assert books.childCount() == 1
    assert books.child(0).text(0) == "Nested"


def test_select_existing_folder():
    dialog = FolderPickerDialog(FakeGraph())
    dialog.start()
    dialog._thread.run()
    dialog._thread.run()

    root = dialog.tree.topLevelItem(0)
    books = root.child(0)
    dialog.tree.setCurrentItem(books)
    dialog._select_current()
    assert dialog.selected == FolderRef("drive", "folder", "Books", "/Books")
    assert dialog._result == QDialog.DialogCode.Accepted


def test_select_button_and_initial_folder():
    initial = FolderRef("drive", "folder", "Books", "/Books")
    dialog = FolderPickerDialog(FakeGraph(), initial=initial)
    dialog.start()
    dialog._thread.run()
    dialog._thread.run()
    assert dialog._drive.id == "drive"

    # The configured folder is offered as a one-click entry and preselected.
    current = dialog.tree.topLevelItem(0)
    assert "Current folder: /Books" in current.text(0)
    assert dialog._current_folder() == initial
    assert dialog.select_button.isEnabled() is True

    root = dialog.tree.topLevelItem(1)
    dialog.tree.setCurrentItem(root.child(0))
    assert dialog._current_folder() == FolderRef("drive", "folder", "Books", "/Books")
    assert dialog.select_button.isEnabled() is True


def test_dialog_without_drive():
    graph = FakeGraph()
    graph.drives = []
    dialog = FolderPickerDialog(graph)
    dialog.start()
    dialog._thread.run()
    assert "not available" in dialog.status.text()


def test_dialog_load_failure():
    graph = FakeGraph()
    graph.fail_folders = True
    dialog = FolderPickerDialog(graph)
    dialog.start()
    dialog._thread.run()
    dialog._thread.run()
    assert "no root" in dialog.status.text()


def test_dialog_reject():
    dialog = FolderPickerDialog(FakeGraph())
    dialog.reject()
    assert dialog._result == QDialog.DialogCode.Rejected


class FakeDialog:
    def __init__(self, graph, parent, *, initial=None) -> None:
        self.selected = initial or FolderRef("drive", "folder", "Books", "/Books")
        self.result = QDialog.DialogCode.Accepted

    def start(self):
        pass

    def exec(self):
        return self.result


def test_open_folder_picker_success(monkeypatch):
    monkeypatch.setattr(folder_picker, "FolderPickerDialog", FakeDialog)
    selected = open_folder_picker(None, FakeGraph())
    assert selected.item_id == "folder"


def test_open_folder_picker_cancelled(monkeypatch):
    class Cancelled(FakeDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.result = QDialog.DialogCode.Rejected

    monkeypatch.setattr(folder_picker, "FolderPickerDialog", Cancelled)
    assert open_folder_picker(None, FakeGraph()) is None
