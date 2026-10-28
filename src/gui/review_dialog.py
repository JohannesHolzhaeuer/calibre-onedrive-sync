"""Review dialog: show how local books and OneDrive files were matched.

The dialog opens as soon as the toolbar button is clicked and fills in while the
scan runs: local books appear first, then their OneDrive counterpart, and rows
move from "download" to "matched" as remote ebook metadata is read. Nothing is
uploaded, downloaded or deleted until Apply is pressed.
"""

from __future__ import annotations

from qt.core import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..common.i18n import _
from ..common.reconcile import ReconcileEntry, ReconcilePlan


def _status_label(status: str) -> str:
    if status == "matched":
        return _("Already linked")
    if status == "upload":
        return _("Upload to OneDrive")
    if status == "download":
        return _("Download to calibre")
    if status == "duplicate":
        return _("Delete OneDrive duplicate")
    if status == "local_only":
        return _("No EPUB/PDF")
    if status == "checking":
        return _("Checking…")
    return status


def _entry_details(entry: ReconcileEntry) -> str:
    if entry.status == "matched" and entry.remote is not None:
        return entry.remote.web_url or entry.note
    return entry.note


def _stable_key(entry: ReconcileEntry) -> str:
    """Key that survives a rebuild (entry keys change when a row is matched)."""
    if entry.status in {"download", "duplicate"} and entry.remote is not None:
        return f"remote:{entry.remote.drive_id}:{entry.remote.id}"
    if entry.local is not None:
        return f"book:{entry.local.book_id}"
    return entry.key


class ReviewDialog(QDialog):
    """List the reconciliation plan live and collect the user's selection."""

    def __init__(self, plan: ReconcilePlan | None = None, parent=None) -> None:
        super().__init__(parent)
        self.plan = plan if plan is not None else ReconcilePlan(entries=[])
        self._items: dict[str, QTreeWidgetItem] = {}
        #: Checkbox state by stable key, so a live update never loses the
        #: user's ticks or unticks.
        self._checked: dict[str, bool] = {}
        self._scanning = False
        #: Latest progress line from the scan; kept across plan snapshots so the
        #: status does not fall back to a generic "checking" message.
        self._progress_message = ""
        #: Which bulk action the user asked for ("" / "apply" / "upload_all" / …).
        self._action = ""
        self._build_ui()
        self.set_plan(self.plan)

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(_("OneDrive Sync – Review matching"))
        self.resize(980, 560)
        layout = QVBoxLayout(self)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)  # 0-100%, fed by set_progress()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.index_status = QLabel("")
        self.index_status.setWordWrap(True)
        layout.addWidget(self.index_status)

        self.hint = QLabel(
            _(
                "Matched books are linked in their calibre metadata. Ticked uploads "
                "and downloads run when you click Apply. Deleting a duplicate moves "
                "the OneDrive file to the recycle bin."
            )
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            [
                "",
                _("Action"),
                _("calibre book"),
                _("OneDrive file"),
                _("Details"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        layout.addWidget(self.tree)

        buttons = QHBoxLayout()
        self.select_all_button = QPushButton(_("Select all"))
        self.select_all_button.clicked.connect(lambda: self._set_all(True))
        self.select_none_button = QPushButton(_("Select none"))
        self.select_none_button.clicked.connect(lambda: self._set_all(False))
        buttons.addWidget(self.select_all_button)
        buttons.addWidget(self.select_none_button)
        buttons.addSpacing(16)

        self.upload_all_button = QPushButton(_("Upload all"))
        self.upload_all_button.setToolTip(
            _("Upload every book and write the calibre index into the folder.")
        )
        self.upload_all_button.clicked.connect(lambda: self._request("upload_all"))
        buttons.addWidget(self.upload_all_button)

        self.download_all_button = QPushButton(_("Download all"))
        self.download_all_button.setToolTip(
            _(
                "Import every OneDrive book without matching. Duplicates are added "
                "as well; clean them up afterwards with calibre's Find Duplicates plugin."
            )
        )
        self.download_all_button.clicked.connect(lambda: self._request("download_all"))
        buttons.addWidget(self.download_all_button)

        self.clear_backup_button = QPushButton(_("Clear backup"))
        self.clear_backup_button.setToolTip(
            _("Delete every item directly in the selected OneDrive folder after a confirmation.")
        )
        self.clear_backup_button.clicked.connect(self._request_clear_backup)
        buttons.addWidget(self.clear_backup_button)

        buttons.addStretch(1)

        self.cancel_button = QPushButton(_("Cancel"))
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.apply_button = QPushButton(_("Apply"))
        self.apply_button.clicked.connect(lambda: self._request("apply"))
        self.apply_button.setEnabled(False)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    # -- live scan ----------------------------------------------------------

    def start_scanning(self) -> None:
        """Switch the window into its "scanning" state (Apply disabled)."""
        self._scanning = True
        self._progress_message = ""
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.select_all_button.setEnabled(False)
        self.select_none_button.setEnabled(False)
        self.status.setText(_("Scanning OneDrive… the list fills in as books are found."))
        self._refresh_buttons()

    def set_progress(self, fraction: float, message: str = "") -> None:
        """Show how far the scan is; the bar never moves backwards."""
        try:
            value = int(round(max(0.0, min(1.0, float(fraction))) * 100))
        except (TypeError, ValueError):
            value = 0
        self.progress.setValue(max(self.progress.value(), value))
        if message:
            self._progress_message = message
            if self._scanning:
                self.status.setText(message)

    def set_plan(self, plan: ReconcilePlan, phase: str = "") -> None:
        """Replace the rows with a newer scan snapshot, keeping the ticks."""
        self._remember_checks()
        self.plan = plan
        self._items.clear()
        self.tree.clear()
        for entry in plan.entries:
            self._add_entry(entry)
        if self._scanning:
            self.status.setText(self._progress_message or self._progress_text(phase))
        else:
            self.status.setText(plan.summary())
        self.index_status.setText(self._index_text(plan))
        self._refresh_buttons()

    def finish_scan(self, plan: ReconcilePlan) -> None:
        """Show the final plan and enable Apply."""
        self._scanning = False
        self._progress_message = ""
        self.progress.setValue(100)
        self.progress.setVisible(False)
        self.set_plan(plan, phase="final")
        self.select_all_button.setEnabled(True)
        self.select_none_button.setEnabled(True)
        self.status.setText(plan.summary())

    def set_cancelled(self, message: str) -> None:
        """Report a cancelled scan without offering the actions."""
        self._stop_scanning(message)

    def set_failed(self, message: str) -> None:
        """Report a failed scan without offering the actions."""
        self._stop_scanning(message)

    def _stop_scanning(self, message: str) -> None:
        self._scanning = False
        self._progress_message = ""
        self.progress.setVisible(False)
        self.select_all_button.setEnabled(True)
        self.select_none_button.setEnabled(True)
        self.status.setText(message)
        self._refresh_buttons()

    def _progress_text(self, phase: str) -> str:
        counts = self.plan.counts()
        if phase == "listing":
            return _("Listing OneDrive… {files} file(s) found so far.").format(
                files=len(self.plan.remote_items)
            )
        if phase == "library" or not self.plan.remote_items:
            return _("Found {count} book(s) in your library. Checking OneDrive…").format(
                count=len(self.plan.local_books)
            )
        if phase == "metadata":
            return _("Reading book metadata… {matched} matched so far.").format(
                matched=counts.get("matched", 0)
            )
        return f"{self.plan.summary()} " + _("Reading book metadata…")

    def _index_text(self, plan: ReconcilePlan) -> str:
        if plan.index_present:
            return _(
                "The calibre index in this folder maps {count} book(s), so files "
                "are matched by item id instead of by name."
            ).format(count=plan.index_books)
        if plan.remote_items:
            return _(
                "No calibre index found in this folder, so files are matched by "
                "name only. Use Upload all to upload every book and create the "
                "index, or Download all to import everything and clean up "
                "duplicates afterwards."
            )
        return ""

    # -- actions ------------------------------------------------------------

    def action(self) -> str:
        """The bulk action the user picked before closing the window."""
        return self._action

    def accept(self) -> None:
        """Pressing Enter applies the ticked rows; the buttons set their own action."""
        if not self._action:
            self._action = "apply"
        super().accept()

    def _request(self, action: str) -> None:
        """Close the window and let the action start the matching job."""
        self._action = action
        self.accept()

    def _request_clear_backup(self) -> None:
        answer = QMessageBox.question(
            self,
            _("Clear OneDrive backup"),
            _(
                "Delete every item directly in the selected OneDrive folder? "
                "Files go to the OneDrive recycle bin, folders are removed with "
                "their contents, and the plugin forgets them. Afterwards you can "
                "upload every book again."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._request("clear_backup")

    # -- rows ---------------------------------------------------------------

    def _add_entry(self, entry: ReconcileEntry) -> None:
        local = entry.local.display() if entry.local is not None else ""
        remote = entry.remote.name if entry.remote is not None else ""
        item = QTreeWidgetItem(
            [
                "",
                _status_label(entry.status),
                local,
                remote,
                _entry_details(entry),
            ]
        )
        if entry.selectable:
            flags = item.flags()
            flags |= Qt.ItemFlag.ItemIsEnabled
            flags |= Qt.ItemFlag.ItemIsSelectable
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
            checked = self._checked.get(_stable_key(entry), entry.default_selected)
            item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        else:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self._items[entry.key] = item
        self.tree.addTopLevelItem(item)

    def _remember_checks(self) -> None:
        for entry in self.plan.entries:
            item = self._items.get(entry.key)
            if item is not None and entry.selectable:
                self._checked[_stable_key(entry)] = item.checkState(0) == Qt.CheckState.Checked

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for entry in self.plan.entries:
            if entry.selectable:
                self._items[entry.key].setCheckState(0, state)
                self._checked[_stable_key(entry)] = checked
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        selectable = any(entry.selectable for entry in self.plan.entries)
        idle = not self._scanning
        self.apply_button.setEnabled(bool(idle and selectable))
        self.upload_all_button.setEnabled(idle)
        self.download_all_button.setEnabled(idle)
        self.clear_backup_button.setEnabled(idle)

    # -- result -------------------------------------------------------------

    def selected_entries(self) -> list[ReconcileEntry]:
        """Return the rows whose checkbox is ticked."""
        selected: list[ReconcileEntry] = []
        for entry in self.plan.entries:
            item = self._items.get(entry.key)
            if item is None or not entry.selectable:
                continue
            if item.checkState(0) == Qt.CheckState.Checked:
                selected.append(entry)
        return selected
