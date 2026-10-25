"""OneDrive folder picker (own drives, shared drives and folder shortcuts).

Loading drives and children happens on short-lived ``QThread`` workers so the
configuration dialog never blocks on the network.
"""

from __future__ import annotations

from qt.core import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QThread,
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
            children = self.graph.list_children(self.drive_id, self.folder_id)
            self.loaded.emit({"root": root, "children": children})
        except Exception as exc:  # noqa: BLE001 - surfaced in the dialog
            self.failed.emit(str(exc))


class FolderPickerDialog(QDialog):
    """Browse OneDrive folders and pick a destination."""

    def __init__(self, graph, parent=None, *, initial: FolderRef | None = None) -> None:
        super().__init__(parent)
        self.graph = graph
        self.initial = initial
        self.selected: FolderRef | None = None
        self._drives: list[DriveInfo] = []
        self._current_drive = ""
        self._current_item: str | None = None
        self._current_path = "/"
        self._current_name = ""
        self._stack: list[tuple[str, str | None, str, str]] = []
        self._thread = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.setWindowTitle(_("Choose OneDrive folder"))

        layout.addWidget(QLabel(_("Drive")))
        self.drive_combo = QComboBox()
        self.drive_combo.currentIndexChanged.connect(self._on_drive_changed)
        layout.addWidget(self.drive_combo)

        layout.addWidget(QLabel(_("Current folder")))
        self.path_label = QLabel("/")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        layout.addWidget(QLabel(_("Subfolders")))
        self.folder_combo = QComboBox()
        layout.addWidget(self.folder_combo)

        buttons = QHBoxLayout()
        self.open_button = QPushButton(_("Open"))
        self.open_button.clicked.connect(self._open_selected)
        self.up_button = QPushButton(_("Up"))
        self.up_button.clicked.connect(self._up)
        self.select_button = QPushButton(_("Select this folder"))
        self.select_button.clicked.connect(self._select_current)
        self.cancel_button = QPushButton(_("Cancel"))
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.up_button)
        buttons.addWidget(self.select_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._set_busy(True)

    def start(self) -> None:
        """Load the drive list; call before ``exec()``."""
        self._set_busy(True)
        self.status.setText(_("Loading drives."))
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
        self._drives = list(drives or [])
        self.drive_combo.clear()
        for drive in self._drives:
            label = drive.name or drive.id
            if drive.owner:
                label = f"{label} — {drive.owner}"
            self.drive_combo.addItem(label, drive)
        if not self._drives:
            self._on_error(_("No OneDrive drives are available for this account."))
            return
        index = 0
        if self.initial is not None:
            for candidate, drive in enumerate(self._drives):
                if drive.id == self.initial.drive_id:
                    index = candidate
                    break
        current = self.drive_combo.currentIndex()
        self.drive_combo.setCurrentIndex(index)
        if current == index:
            self._on_drive_changed()

    def _on_drive_changed(self, *_args) -> None:
        drive = self.drive_combo.currentData()
        if not isinstance(drive, DriveInfo):
            return
        self._stack.clear()
        self._navigate(drive.id, None, "/", "OneDrive")

    def _navigate(
        self,
        drive_id: str,
        item_id: str | None,
        path: str,
        name: str,
        *,
        push: bool = False,
    ) -> None:
        if push and self._current_drive:
            self._stack.append(
                (self._current_drive, self._current_item, self._current_path, self._current_name)
            )
        self._current_drive = drive_id
        self._current_item = item_id
        self._current_path = path
        self._current_name = name
        self.path_label.setText(path)
        self.folder_combo.clear()
        self._set_busy(True)
        self.status.setText(_("Loading folders."))
        worker = _LoadFolders(self.graph, drive_id, item_id)
        worker.loaded.connect(self._on_folders_loaded)
        worker.failed.connect(self._on_error)
        self._start_worker(worker)

    def _on_folders_loaded(self, payload) -> None:
        root = payload.get("root") if isinstance(payload, dict) else None
        if self._current_item is None and isinstance(root, RemoteItem):
            self._current_item = root.id
            self._current_name = root.name or self._current_name
        children = payload.get("children") if isinstance(payload, dict) else payload
        self.folder_combo.clear()
        for child in children or []:
            if isinstance(child, RemoteItem) and child.is_folder and not child.deleted:
                path = f"{self._current_path.rstrip('/')}/{child.name}"
                self.folder_combo.addItem(
                    child.name,
                    FolderRef(
                        drive_id=child.drive_id or self._current_drive,
                        item_id=child.id,
                        name=child.name,
                        path=path,
                    ),
                )
        self._set_busy(False)
        self.status.setText("")

    def _open_selected(self) -> None:
        folder = self.folder_combo.currentData()
        if not isinstance(folder, FolderRef):
            return
        self._navigate(
            folder.drive_id,
            folder.item_id,
            folder.path,
            folder.name,
            push=True,
        )

    def _up(self) -> None:
        if not self._stack:
            return
        drive_id, item_id, path, name = self._stack.pop()
        self._navigate(drive_id, item_id, path, name)

    def _select_current(self) -> None:
        self.selected = FolderRef(
            drive_id=self._current_drive,
            item_id=self._current_item or "",
            name=self._current_name,
            path=self._current_path,
        )
        self.accept()

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self.status.setText(_("Could not load OneDrive: {message}").format(message=message))

    def _set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self.up_button.setEnabled(not busy and bool(self._stack))
        self.select_button.setEnabled(not busy and bool(self._current_item))
        self.drive_combo.setEnabled(not busy)

    def reject(self) -> None:
        super().reject()


def open_folder_picker(parent, graph, *, initial: FolderRef | None = None) -> FolderRef | None:
    """Show the folder picker and return the selected folder (or ``None``)."""
    dialog = FolderPickerDialog(graph, parent, initial=initial)
    dialog.start()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected
