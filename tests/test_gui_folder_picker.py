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
        root_children = [
            RemoteItem("folder", "Books", "drive", is_folder=True),
            RemoteItem("file", "file.epub", "drive"),
        ]
        self.children = {
            (None, "drive"): root_children,
            ("root", "drive"): root_children,
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


def test_dialog_navigation(monkeypatch):
    dialog = FolderPickerDialog(FakeGraph())
    dialog.start()
    dialog._thread.run()
    dialog._thread.run()

    assert dialog.drive_combo.count() == 1
    assert dialog.path_label.text() == "/"
    assert dialog.folder_combo.count() == 1
    assert dialog._current_item == "root"

    dialog.folder_combo.setCurrentIndex(0)
    dialog._open_selected()
    assert dialog._current_path == "/Books"
    assert dialog._current_item == "folder"
    assert dialog._stack[0][0] == "drive"

    dialog._thread.run()
    assert dialog.status.text() == ""

    dialog._up()
    dialog._thread.run()
    assert dialog._current_item == "root"
    assert dialog._stack == []

    dialog.folder_combo.setCurrentIndex(0)
    dialog._open_selected()
    dialog._thread.run()
    dialog._select_current()
    assert dialog.selected == FolderRef("drive", "folder", "Books", "/Books")
    assert dialog._result == QDialog.DialogCode.Accepted


def test_dialog_with_initial_folder_and_errors():
    initial = FolderRef("drive", "folder", "Books", "/Books")
    dialog = FolderPickerDialog(FakeGraph(), initial=initial)
    dialog.start()
    dialog._thread.run()
    assert dialog._current_drive == "drive"

    dialog._on_error("boom")
    assert "boom" in dialog.status.text()

    dialog._stack = []
    dialog._up()
    dialog._open_selected()


def test_dialog_without_drives():
    graph = FakeGraph()
    graph.drives = []
    dialog = FolderPickerDialog(graph)
    dialog.start()
    dialog._thread.run()
    assert "No OneDrive drives" in dialog.status.text()


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
