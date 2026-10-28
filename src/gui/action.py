"""OneDrive Sync InterfaceAction: the toolbar button drives everything.

The plugin never syncs on its own. A click opens the OneDrive folder picker,
checks whether that folder already carries a usable calibre index and then
either opens the matching window or offers to clear the folder and upload the
whole library with a fresh index. Only the direct children of the selected
folder are ever read.
"""

from __future__ import annotations

from calibre.gui2.actions import InterfaceAction
from qt.core import QDialog, QMessageBox, pyqtSignal

from ..common.auth import AuthManager
from ..common.config import (
    ConfigTokenStore,
    get_refresh_token,
    is_configured,
    load_settings,
    save_settings,
)
from ..common.errors import CancelledError
from ..common.graph import GraphClient
from ..common.i18n import _
from ..common.log import configure_logging, get_logger
from ..common.models import ONEDRIVE_IDENTIFIER, BookLink, FolderRef
from ..common.state import StateStore, default_state_path
from ..common.sync import SyncEngine
from .config_widget import open_config as open_config_dialog
from .folder_picker import open_folder_picker
from .library import CalibreLibrary, add_book_to_library, read_metadata
from .review_dialog import ReviewDialog
from .sync_job import (
    MainThreadBookAdder,
    MainThreadBookLinker,
    start_apply_job,
    start_index_job,
    start_scan_job,
    start_task_job,
)

log = get_logger("onedrive_sync.action")


class OneDriveSyncInterfaceAction(InterfaceAction):
    """Toolbar action that opens the review window or starts a sync."""

    name = "OneDrive Sync"
    action_type = "current"
    action_spec = (
        _("OneDrive Sync"),
        None,
        _("Sync your calibre library with a OneDrive folder"),
        None,
    )

    _scan_done = pyqtSignal(object)
    _apply_done = pyqtSignal(object)
    _task_done = pyqtSignal(object)
    _index_checked = pyqtSignal(object)
    _scan_progress = pyqtSignal(object, str)
    _scan_status = pyqtSignal(float, str)

    def genesis(self):
        """Set up the action icon and job callbacks."""
        self._job = None
        self._state: StateStore | None = None
        self._engine: SyncEngine | None = None
        self._library: CalibreLibrary | None = None
        self._adder = None
        self._linker = None
        self._review_dialog = None
        self._scan_plan = None
        self._scan_finished = False
        self._task = ""
        self._next_task = ""
        self.qaction.setIcon(
            get_icons(  # noqa: F821 - injected by calibre's plugin loader
                "resources/images/onedrive.png", "onedrive_sync"
            )
        )
        self.qaction.triggered.connect(self.sync_now)

        self._scan_done.connect(self._on_scan_done)
        self._apply_done.connect(self._on_apply_done)
        self._task_done.connect(self._on_task_done)
        self._index_checked.connect(self._on_index_checked)
        self._scan_progress.connect(self._on_scan_progress)
        self._scan_status.connect(self._on_scan_status)

    def shutting_down(self):
        """Close the sync state database on shutdown."""
        if self._job is None:
            self._close_state()

    # -- actions ------------------------------------------------------------

    def open_config(self) -> bool:
        """Open the configuration dialog; returns True when it was accepted."""
        return open_config_dialog(self.gui)

    def sync_now(self, *args):
        """Toolbar entry point: always the manual review flow."""
        self._clear_finished_job()
        if self._job is not None:
            log.debug("Ignoring sync request: a sync is already running")
            return
        self.review_now()

    def review_now(self) -> None:
        """Pick a folder, require an index, then open the matching window."""
        self._clear_finished_job()
        if self._job is not None:
            return
        settings = self._settings_for()
        if settings is None:
            return
        if not self._prepare_engine(settings):
            return

        folder = self._pick_folder(settings)
        if folder is None:
            self._close_state()
            return
        self._remember_folder(settings, folder)

        # Listing the folder and downloading the index are network calls, so
        # they run in a job instead of blocking the GUI thread.
        self._job = start_index_job(
            self.gui.job_manager,
            self._engine,
            self._index_checked.emit,
            description=_("Checking the OneDrive folder"),
        )
        self._show_status(_("Checking the OneDrive folder for a calibre index…"))

    def _on_index_checked(self, job) -> None:
        """Continue the click flow once the index check has finished."""
        if self._is_stale_job(job):
            return
        self._job = None
        if job is None:
            self._close_state()
            return
        if self._job_cancelled(job):
            self._close_state()
            self._show_status(_("OneDrive sync cancelled."))
            return
        if getattr(job, "failed", False):
            message = str(getattr(job, "exception", "") or _("Unknown error"))
            self._close_state()
            log.warning("Could not check the OneDrive folder: %s", message)
            QMessageBox.warning(
                self.gui,
                _("OneDrive Sync"),
                _("Could not check the OneDrive folder: {message}").format(message=message),
            )
            return

        available = getattr(job, "result", None)
        if not isinstance(available, bool):
            self._close_state()
            log.warning("The OneDrive index check returned no result")
            QMessageBox.warning(
                self.gui,
                _("OneDrive Sync"),
                _("Could not check the OneDrive folder for a calibre index."),
            )
            return

        if not available:
            settings = self._engine.settings
            if not self._offer_fresh_upload(settings):
                self._close_state()
                self._show_status(_("OneDrive sync cancelled."))
                return
            self._next_task = "upload_all"
            self._start_task("clear_backup", _("Clearing the OneDrive folder…"))
            return

        self._open_review()

    def _pick_folder(self, settings):
        """Let the user choose the OneDrive folder, prefilled with the current one."""
        initial = None
        if settings.folder_id:
            initial = FolderRef(
                drive_id=settings.drive_id,
                item_id=settings.folder_id,
                name=settings.folder_name,
                path=settings.folder_path,
            )
        try:
            return open_folder_picker(self.gui, self._engine.graph, initial=initial)
        except Exception as exc:  # noqa: BLE001 - network/Graph failure
            log.warning("Could not open the OneDrive folder picker", exc_info=True)
            QMessageBox.warning(
                self.gui,
                _("OneDrive Sync"),
                _("Could not open the OneDrive folder picker: {message}").format(message=exc),
            )
            return None

    def _remember_folder(self, settings, folder) -> None:
        """Persist the folder the user just confirmed."""
        settings.drive_id = folder.drive_id
        settings.folder_id = folder.item_id
        settings.folder_path = folder.path
        settings.folder_name = folder.name
        try:
            save_settings(settings)
        except Exception:  # noqa: BLE001 - the session still works
            log.warning("Could not save the selected OneDrive folder", exc_info=True)

    def _offer_fresh_upload(self, settings) -> bool:
        """Ask whether to empty the folder and upload the whole library."""
        folder = settings.folder_path or settings.folder_name or settings.folder_id
        answer = QMessageBox.question(
            self.gui,
            _("OneDrive Sync"),
            _(
                "No calibre index was found in {folder}. Clear that folder and "
                "upload your whole library with a new index? Everything currently "
                "in the folder is moved to the OneDrive recycle bin."
            ).format(folder=folder),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _open_review(self) -> None:
        """Open the matching window and scan while it is visible."""
        dialog = ReviewDialog(parent=self.gui)
        dialog.start_scanning()
        self._review_dialog = dialog
        self._scan_plan = None
        self._scan_finished = False
        self._task = ""
        self._next_task = ""
        self._job = start_scan_job(
            self.gui.job_manager,
            self._engine,
            self._scan_done.emit,
            description=_("Matching OneDrive books"),
            on_plan=self._scan_progress.emit,
            on_progress=self._scan_status.emit,
        )
        self._show_status(_("Comparing your library with OneDrive…"))
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._review_dialog = None
        self._finish_review(dialog, accepted)

    # -- job setup ----------------------------------------------------------

    def _settings_for(self):
        settings = load_settings()
        if is_configured(settings) and get_refresh_token():
            return settings
        if not self.open_config():
            return None
        settings = load_settings()
        if not is_configured(settings) or not get_refresh_token():
            return None
        return settings

    def _prepare_engine(self, settings) -> bool:
        configure_logging(settings.debug)
        state = None
        try:
            library = CalibreLibrary(self.gui)
            auth = AuthManager(settings.client_id, ConfigTokenStore())
            graph = GraphClient(auth)
            state = StateStore(default_state_path())
            adder = MainThreadBookAdder(
                lambda path: add_book_to_library(self.gui, path), parent=self.gui
            )
            linker = MainThreadBookLinker(self._store_link, parent=self.gui)
            engine = SyncEngine(
                graph,
                state,
                library,
                settings,
                add_book=adder,
                link_book=linker,
                metadata_reader=read_metadata,
            )
        except Exception as exc:  # noqa: BLE001 - configuration/runtime failure
            if state is not None:
                try:
                    state.close()
                except Exception:  # noqa: BLE001 - best effort
                    pass
            self._close_state()
            log.warning("Could not initialize the OneDrive sync", exc_info=True)
            QMessageBox.warning(
                self.gui,
                _("OneDrive Sync"),
                _("Could not start the sync: {message}").format(message=exc),
            )
            return False
        self._state = state
        self._library = library
        self._engine = engine
        self._adder = adder
        self._linker = linker
        return True

    # -- review callbacks ---------------------------------------------------

    def _on_scan_progress(self, plan, phase) -> None:
        """Show a live scan snapshot in the open review window."""
        dialog = self._review_dialog
        if dialog is None:
            return
        try:
            dialog.set_plan(plan, phase)
        except Exception:  # noqa: BLE001 - a redraw must not kill the scan
            log.debug("Could not update the review window", exc_info=True)

    def _on_scan_status(self, fraction, message) -> None:
        """Advance the progress bar/status line of the open review window."""
        dialog = self._review_dialog
        if dialog is None:
            return
        try:
            dialog.set_progress(fraction, message)
        except Exception:  # noqa: BLE001 - a status update must not kill the scan
            log.debug("Could not update the scan progress", exc_info=True)

    def _on_scan_done(self, job) -> None:
        if self._is_stale_job(job):
            return
        self._job = None
        self._scan_finished = True
        dialog = self._review_dialog

        if job is None:
            self._scan_failed(dialog, _("OneDrive matching stopped without a result."))
            return
        if self._job_cancelled(job) or isinstance(getattr(job, "exception", None), CancelledError):
            log.info("OneDrive matching cancelled")
            message = _("OneDrive matching cancelled.")
            if dialog is not None:
                dialog.set_cancelled(message)
                self._show_status(message)
            else:
                self._release_review()
                self._show_status(message)
            return
        if getattr(job, "failed", False):
            exception = getattr(job, "exception", None)
            log.warning("OneDrive matching failed: %s", exception, exc_info=exception is not None)
            self._scan_failed(
                dialog,
                _("Could not compare the library with OneDrive: {message}").format(
                    message=str(exception or _("Unknown error"))
                ),
            )
            return

        plan = getattr(job, "result", None)
        if plan is None:
            self._scan_failed(dialog, _("OneDrive matching stopped without a result."))
            return
        self._scan_plan = plan
        self._persist_plan_links(plan)
        self._show_status(plan.summary())
        if dialog is not None:
            dialog.finish_scan(plan)
        else:
            self._release_review()

    def _scan_failed(self, dialog, message: str) -> None:
        """Report a scan problem in the window, or as a dialog when it is gone."""
        if dialog is not None:
            dialog.set_failed(message)
            self._show_status(message)
            return
        self._release_review()
        QMessageBox.warning(self.gui, _("OneDrive Sync"), message)

    def _release_review(self) -> None:
        """Close the sync state after a review."""
        self._close_state()

    def _abort_job(self, job) -> None:
        """Ask a worker to stop, exactly like calibre's own Kill action."""
        abort = getattr(job, "abort", None)
        try:
            if abort is not None:
                abort.set()
        except Exception:  # noqa: BLE001 - already finished job
            log.debug("Could not signal the OneDrive job to stop", exc_info=True)

    def _finish_review(self, dialog, accepted: bool) -> None:
        """React to the review window closing."""
        if not self._scan_finished:
            # Closed while the scan was still running: stop the worker and let
            # the completion callback do the clean-up.
            self._abort_job(self._job)
            self._show_status(_("OneDrive matching cancelled."))
            return

        plan = self._scan_plan
        action = dialog.action() if accepted else ""
        if action in {"upload_all", "download_all", "clear_backup"}:
            self._next_task = ""
            self._start_task(action)
            return

        selected = dialog.selected_entries() if action == "apply" and plan is not None else []
        if action == "apply" and plan is not None and selected:
            self._job = start_apply_job(
                self.gui.job_manager,
                self._engine,
                plan,
                selected,
                self._adder,
                self._linker,
                self._apply_done.emit,
                description=_("Applying OneDrive changes"),
            )
            self._show_status(_("Applying the selected OneDrive changes…"))
            return

        self._release_review()
        if action == "apply" and plan is not None:
            QMessageBox.information(self.gui, _("OneDrive Sync"), plan.summary())

    # -- bulk actions -------------------------------------------------------

    def _task_description(self, task: str) -> str:
        if task == "upload_all":
            return _("Uploading every book to OneDrive")
        if task == "download_all":
            return _("Downloading every OneDrive book")
        if task == "clear_backup":
            return _("Clearing the OneDrive backup")
        return _("OneDrive Sync")

    def _start_task(self, task: str, status: str | None = None) -> None:
        """Run a bulk review action (upload all / download all / clear) as a job."""
        self._task = task
        self._job = start_task_job(
            self.gui.job_manager,
            self._engine,
            task,
            self._task_done.emit,
            description=self._task_description(task),
        )
        self._show_status(status or self._task_status(task))

    def _task_status(self, task: str) -> str:
        if task == "upload_all":
            return _("Uploading every book to OneDrive…")
        if task == "download_all":
            return _("Downloading every OneDrive book…")
        if task == "clear_backup":
            return _("Deleting the OneDrive backup…")
        return _("Working on OneDrive…")

    def _on_task_done(self, job) -> None:
        """Report a finished bulk action and release the sync state."""
        task = self._task
        self._task = ""
        if self._is_stale_job(job):
            return
        self._job = None
        # ``_close_state`` drops the library adapter, but clearing the calibre
        # identifiers still needs it, so keep a reference for the result below.
        library = self._library
        if job is None:
            self._next_task = ""
            self._close_state()
            return
        if self._job_cancelled(job):
            self._next_task = ""
            self._close_state()
            self._show_status(_("OneDrive action cancelled."))
            return
        if getattr(job, "failed", False):
            self._next_task = ""
            self._close_state()
            message = str(getattr(job, "exception", "") or _("Unknown error"))
            log.warning("OneDrive action %s failed: %s", task, message)
            QMessageBox.warning(
                self.gui,
                _("OneDrive Sync"),
                _("OneDrive action failed: {message}").format(message=message),
            )
            return

        result = getattr(job, "result", None)
        if result is None:
            self._next_task = ""
            self._close_state()
            return
        if task == "clear_backup" and not result.cancelled and library is not None:
            self._clear_identifiers(library)

        # "Clear backup" is chained with "Upload all" when a folder had no
        # index: keep the engine and state alive for the follow-up job.
        next_task = self._next_task
        self._next_task = ""
        if next_task and not result.cancelled and not result.errors:
            self._start_task(next_task)
            return

        self._close_state()
        summary = result.summary()
        self._show_status(summary)
        if result.errors:
            details = "\n".join(result.errors[:10])
            if len(result.errors) > 10:
                details += "\n…"
            for message in result.errors:
                log.warning("OneDrive action error: %s", message)
            QMessageBox.warning(self.gui, _("OneDrive Sync"), f"{summary}\n\n{details}")
        else:
            QMessageBox.information(self.gui, _("OneDrive Sync"), summary)

    def _clear_identifiers(self, library) -> None:
        """Drop the OneDrive link from every book after the backup was cleared."""
        try:
            cleared = library.clear_identifier(ONEDRIVE_IDENTIFIER)
        except Exception:  # noqa: BLE001 - the remote files are already gone
            log.warning("Could not clear the OneDrive identifiers", exc_info=True)
            return
        log.info("Cleared %s OneDrive identifier(s)", cleared)

    def _on_apply_done(self, job) -> None:
        self._on_job_done(job)

    def _persist_plan_links(self, plan) -> None:
        """Store matches in state and calibre metadata (GUI thread)."""
        for entry in plan.entries:
            if entry.status != "matched" or entry.local is None or entry.remote is None:
                continue
            link = BookLink.from_remote(entry.local.book_id, entry.remote)
            try:
                if self._state is not None:
                    self._state.link_book(link)
                self._store_link(link)
            except Exception:  # noqa: BLE001 - linking one book must not break the review
                log.warning(
                    "Could not save the OneDrive link for book %s",
                    link.book_id,
                    exc_info=True,
                )

    def _store_link(self, link: BookLink) -> None:
        """Write the OneDrive URL/id into calibre's identifiers field."""
        library = self._library
        if library is None:
            try:
                library = CalibreLibrary(self.gui)
            except Exception:  # noqa: BLE001 - headless/closed library
                return
        value = link.web_url or link.item_id
        library.set_identifier(link.book_id, ONEDRIVE_IDENTIFIER, value)

    # -- job callback -------------------------------------------------------

    def _on_job_done(self, job) -> None:
        if self._is_stale_job(job):
            return
        self._job = None
        self._close_state()
        if job is None:
            return

        result = getattr(job, "result", None)
        # calibre marks a killed job as failed without an exception, so check
        # for cancellation before treating it as an error.
        if self._job_cancelled(job) or (result is not None and result.cancelled):
            summary = result.summary() if result is not None else _("Cancelled.")
            log.info("OneDrive sync cancelled")
            self._show_status(summary)
            QMessageBox.information(self.gui, _("OneDrive Sync"), summary)
            return

        if getattr(job, "failed", False):
            message = str(getattr(job, "exception", "") or _("Unknown error"))
            log.warning("OneDrive sync failed: %s", message)
            QMessageBox.warning(
                self.gui,
                _("OneDrive Sync"),
                _("Sync failed: {message}").format(message=message),
            )
            return

        if result is None:
            return
        summary = result.summary()
        if result.errors:
            details = "\n".join(result.errors[:10])
            if len(result.errors) > 10:
                details += "\n…"
            for message in result.errors:
                log.warning("Sync error: %s", message)
            QMessageBox.warning(
                self.gui,
                _("OneDrive Sync"),
                f"{summary}\n\n{details}",
            )
        else:
            QMessageBox.information(self.gui, _("OneDrive Sync"), summary)
        self._show_status(summary)

    # -- helpers ------------------------------------------------------------

    def _close_state(self) -> None:
        if self._state is not None:
            try:
                self._state.close()
            except Exception:  # noqa: BLE001 - best effort
                log.debug("Could not close the sync state", exc_info=True)
        self._state = None
        self._engine = None
        self._library = None
        self._adder = None
        self._linker = None

    def _job_cancelled(self, job) -> bool:
        """True when the user stopped the job from calibre's jobs list."""
        if getattr(job, "killed", False):
            return True
        abort = getattr(job, "abort", None)
        try:
            return bool(abort is not None and abort.is_set())
        except Exception:  # noqa: BLE001 - unknown job object
            return False

    def _job_is_running(self, job) -> bool:
        """True while the job still does work (queued or executing)."""
        if getattr(job, "worker_active", False):
            return True
        if self._job_cancelled(job):
            return False
        # calibre keeps ``duration`` as None until the job has finished.
        return getattr(job, "duration", None) is None

    def _clear_finished_job(self) -> None:
        """Forget a job calibre finished without calling the callback.

        calibre never calls the completion callback for a job that was killed
        while it was still queued, so a dead reference would otherwise block
        every later sync.
        """
        job = self._job
        if job is None or self._job_is_running(job):
            return
        log.debug("Releasing a finished OneDrive job that reported no result")
        self._job = None
        self._close_state()

    def _is_stale_job(self, job) -> bool:
        """True when ``job`` is a superseded job whose callback arrived late."""
        if job is None or self._job is None or job is self._job:
            return False
        log.debug("Ignoring the late callback of a superseded OneDrive job")
        return True

    def _show_status(self, message: str) -> None:
        try:
            self.gui.status_bar.show_message(message, 5000)
        except Exception:  # noqa: BLE001 - status bar is cosmetic
            pass
