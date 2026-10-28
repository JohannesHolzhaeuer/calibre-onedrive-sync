"""OneDrive folder picker for the signed-in user's own drive.

Folders are loaded lazily into a tree so large drives stay responsive. Loading
happens on short-lived ``QThread`` workers; the configuration dialog never
blocks on the network.
"""

from __future__ import annotations

from qt.core import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QThread,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    pyqtSignal,
)

from ..common.i18n import _
from ..common.models import DriveInfo, FolderRef, RemoteItem

_ACTIVE_WORKERS: set[QThread] = set()


class _LoadDrives(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, graph) -> None:
        super().__init__()
        self.graph = graph

    def run(self) -> None:
        try:
            self.loaded.emit(self.graph.list_drives())
        except Exception as exc:  # noqa: BLE001 - surfaced in the dialog
            self.failed.emit(str(exc))


class _LoadFolders(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, graph, drive_id: str, folder_id: str | None) -> None:
        super().__init__()
        self.graph = graph
        self.drive_id = drive_id
        self.folder_id = folder_id

    def run(self) -> None:
        try:
            root = None
            if self.folder_id is None:
                root = self.graph.get_root_item(self.drive_id)
                folder_id = root.id
            else:
                folder_id = self.folder_id
            children = self.graph.list_children(self.drive_id, folder_id)
            self.loaded.emit({"root": root, "folder_id": folder_id, "children": children})
        except Exception as exc:  # noqa: BLE001 - surfaced in the dialog
            self.failed.emit(str(exc))


class FolderPickerDialog(QDialog):
    """Browse the user's OneDrive folders and pick a destination."""

    def __init__(self, graph, parent=None, *, initial: FolderRef | None = None) -> None:
        super().__init__(parent)
        self.graph = graph
        self.initial = initial
        self.selected: FolderRef | None = None
        self._drive: DriveInfo | None = None
        self._loaded: set[str] = set()
        self._loading: set[str] = set()
        self._thread = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.setWindowTitle(_("Choose OneDrive folder"))

        self.summary = QLabel(_("Loading OneDrive…"))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Folder")])
        self.tree.itemExpanded.connect(self._on_expanded)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.tree)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.select_button = QPushButton(_("Select this folder"))
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._select_current)
        self.cancel_button = QPushButton(_("Cancel"))
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.select_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def start(self) -> None:
        """Load the user's OneDrive; call before ``exec()``."""
        self.status.setText(_("Loading OneDrive."))
        worker = _LoadDrives(self.graph)
        worker.loaded.connect(self._on_drives_loaded)
        worker.failed.connect(self._on_error)
        self._start_worker(worker)

    def _start_worker(self, worker: QThread) -> None:
        self._thread = worker
        _ACTIVE_WORKERS.add(worker)
        worker.finished.connect(lambda w=worker: _ACTIVE_WORKERS.discard(w))
        worker.start()

    def _on_drives_loaded(self, drives) -> None:
        available = list(drives or [])
        if not available:
            self._on_error(_("The OneDrive drive is not available for this account."))
            return
        self._drive = available[0]
        name = self._drive.name or _("OneDrive")
        if self._drive.owner:
            name = f"{name} - {self._drive.owner}"
        self.summary.setText(name)
        self.status.setText("")

        current = self._initial_node()
        if current is not None:
            self.tree.addTopLevelItem(current)
            self.tree.setCurrentItem(current)

        root = QTreeWidgetItem([name])
        root.setData(0, Qt.ItemDataRole.UserRole, FolderRef(self._drive.id, "", name, "/"))
        root.addChild(QTreeWidgetItem([_("Loading…")]))
        self.tree.addTopLevelItem(root)
        if current is None:
            self.tree.setCurrentItem(root)
        self._load_children(root, None)
        self.tree.expandItem(root)

    def _initial_node(self) -> QTreeWidgetItem | None:
        """A one-click entry for the folder that is currently configured."""
        initial = self.initial
        if initial is None or not initial.item_id or self._drive is None:
            return None
        folder = FolderRef(
            drive_id=initial.drive_id or self._drive.id,
            item_id=initial.item_id,
            name=initial.name,
            path=initial.path,
        )
        node = QTreeWidgetItem([_("Current folder: {path}").format(path=folder.display())])
        node.setData(0, Qt.ItemDataRole.UserRole, folder)
        return node

    def _load_children(self, item: QTreeWidgetItem, folder_id: str | None) -> None:
        if self._drive is None:
            return
        key = folder_id or "root"
        if key in self._loaded or key in self._loading:
            return
        self._loading.add(key)
        self.status.setText(_("Loading folders."))
        worker = _LoadFolders(self.graph, self._drive.id, folder_id)
        worker.loaded.connect(lambda payload, item=item: self._on_children_loaded(item, payload))
        worker.failed.connect(lambda message, key=key: self._on_load_failed(key, message))
        self._start_worker(worker)

    def _on_children_loaded(self, item: QTreeWidgetItem, payload) -> None:
        if not isinstance(payload, dict):
            return
        parent = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(parent, FolderRef):
            return

        load_key = "root" if isinstance(payload.get("root"), RemoteItem) else parent.item_id
        root = payload.get("root")
        if isinstance(root, RemoteItem):
            parent = FolderRef(
                drive_id=parent.drive_id,
                item_id=root.id,
                name=root.name or parent.name,
                path="/",
            )
            item.setData(0, Qt.ItemDataRole.UserRole, parent)
            item.setText(0, parent.name)

        self._loading.discard(load_key)
        self._loaded.add(parent.item_id)
        self._clear_children(item)
        for child in payload.get("children") or []:
            if not isinstance(child, RemoteItem) or not child.is_folder or child.deleted:
                continue
            if child.drive_id and child.drive_id != self._drive.id:
                continue
            path = f"{parent.path.rstrip('/')}/{child.name}"
            folder = FolderRef(
                drive_id=self._drive.id,
                item_id=child.id,
                name=child.name,
                path=path,
            )
            node = QTreeWidgetItem([child.name])
            node.setData(0, Qt.ItemDataRole.UserRole, folder)
            node.addChild(QTreeWidgetItem([_("Loading…")]))
            item.addChild(node)
        self.status.setText("")
        self._refresh_selection()

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(folder, FolderRef) and folder.item_id:
            self._load_children(item, folder.item_id)

    def _on_current_changed(self, current, _previous=None) -> None:
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        folder = self._current_folder()
        self.select_button.setEnabled(folder is not None and bool(folder.item_id))

    def _current_folder(self) -> FolderRef | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        return folder if isinstance(folder, FolderRef) else None

    @staticmethod
    def _clear_children(item: QTreeWidgetItem) -> None:
        while item.childCount():
            item.takeChild(0)

    def _select_current(self) -> None:
        folder = self._current_folder()
        if folder is None or not folder.item_id:
            return
        self.selected = folder
        self.accept()

    def _on_load_failed(self, key: str, message: str) -> None:
        self._loading.discard(key)
        self._on_error(message)

    def _on_error(self, message: str) -> None:
        self.status.setText(_("Could not load OneDrive: {message}").format(message=message))

    def reject(self) -> None:
        super().reject()


def open_folder_picker(parent, graph, *, initial: FolderRef | None = None) -> FolderRef | None:
    """Show the folder picker and return the selected folder (or ``None``)."""
    dialog = FolderPickerDialog(graph, parent, initial=initial)
    dialog.start()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected
