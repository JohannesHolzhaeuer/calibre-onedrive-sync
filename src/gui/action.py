"""OneDrive Sync InterfaceAction: the toolbar button drives everything.

Nothing is scanned, downloaded or cleaned up on its own. A click uses the
configured OneDrive folder, checks whether that folder already carries a usable
calibre index and then either opens the matching window or offers to clear the
folder and upload the whole library with a fresh index. The folder picker only
appears when no folder is configured, that is in the setup flow and when the
settings dialog is opened from the toolbar. Only the direct children of the
selected folder are ever read.

The single automatic path is the add-books hook: ``genesis`` registers a
:class:`~onedrive_sync.gui.library_watch.LibraryWatcher` on calibre's database,
and a book calibre just created is uploaded on its own (see
``_upload_new_books``). Everything that hook does is visible in calibre's job
panel and in the status bar, and the *Upload newly added books* setting turns it
off again.
"""

from __future__ import annotations

from calibre.gui2.actions import InterfaceAction
from qt.core import QDialog, QMessageBox, Qt, QTimer, pyqtSignal

from ..common.auth import AuthManager
from ..common.auto_upload import AUTO_UPLOAD_DELAY_MS, BookUploadQueue
from ..common.config import (
    ConfigTokenStore,
    get_refresh_token,
    is_configured,
    load_settings,
    save_settings,
)
from ..common.errors import CancelledError, GraphError
from ..common.graph import GraphClient
from ..common.i18n import _
from ..common.log import configure_logging, get_logger
from ..common.models import ONEDRIVE_IDENTIFIER, BookLink, FolderRef
from ..common.state import StateStore, default_state_path
from ..common.sync import SyncEngine
from .config_widget import open_config as open_config_dialog
from .folder_picker import open_folder_picker
from .library import CalibreLibrary, add_book_to_library, read_metadata
from .library_watch import LibraryWatcher
from .review_dialog import ReviewDialog
from .sync_job import (
    MainThreadBookAdder,
    MainThreadBookLinker,
    start_apply_job,
    start_index_job,
    start_scan_job,
    start_task_job,
    start_upload_books_job,
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
    _auto_upload_done = pyqtSignal(object)

    def genesis(self):
        """Set up the action icon, job callbacks and the add-books hook."""
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
        #: Automatic upload of books calibre just added (the only non-manual path).
        self._auto_watcher: LibraryWatcher | None = None
        self._auto_queue = BookUploadQueue()
        self._auto_job = None
        self._auto_state: StateStore | None = None
        self._auto_engine: SyncEngine | None = None
        self._auto_library: CalibreLibrary | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(int(AUTO_UPLOAD_DELAY_MS))
        self._auto_timer.timeout.connect(self._upload_new_books)
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
        self._auto_upload_done.connect(self._on_auto_upload_done)
        self._install_auto_upload()

    def shutting_down(self):
        """Stop watching for new books and close the sync state on shutdown."""
        self._stop_auto_upload()
        if self._job is None:
            self._close_state()

    # -- actions ------------------------------------------------------------

    def open_config(self) -> bool:
        """Open the configuration dialog; returns True when it was accepted."""
        accepted = open_config_dialog(self.gui)
        if accepted:
            # The folder or the auto-upload toggle may have changed.
            self._install_auto_upload()
        return accepted

    def sync_now(self, *args):
        """Toolbar entry point: always the manual review flow."""
        self._clear_finished_job()
        if self._job is not None:
            log.debug("Ignoring sync request: a sync is already running")
            return
        self.review_now()

    def review_now(self) -> None:
        """Open the window at once, then check the index and scan into it."""
        self._clear_finished_job()
        if self._job is not None:
            return
        # A manual pass and the automatic upload must not write the state at once.
        self._abort_auto_upload()
        settings = self._settings_for()
        if settings is None:
            return
        self._install_auto_upload()
        if not self._prepare_engine(settings):
            return
        if not self._ensure_folder(settings):
            self._close_state()
            return
        self._open_window()

    def _open_window(self) -> None:
        """Show the review window first so the user sees the work that follows."""
        dialog = ReviewDialog(parent=self.gui)
        dialog.start_scanning(_("Checking the OneDrive folder for a calibre index…"))
        dialog.bulk_requested.connect(self._on_bulk_request)
        self._review_dialog = dialog
        self._scan_plan = None
        self._scan_finished = False
        self._task = ""
        self._next_task = ""
        self._check_index()
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._review_dialog = None
        self._finish_review(dialog, accepted)

    def _configured_folder(self, settings) -> FolderRef | None:
        """The folder from the settings, or ``None`` when none is stored."""
        if not (settings.drive_id and settings.folder_id):
            return None
        return FolderRef(
            drive_id=settings.drive_id,
            item_id=settings.folder_id,
            name=settings.folder_name,
            path=settings.folder_path,
        )

    def _ensure_folder(self, settings) -> bool:
        """True when a folder is available, asking for one when none is stored.

        The folder normally comes from the settings dialog, so a toolbar click
        does not ask for it. The picker only appears during the initial setup and
        after a Graph 404 shows that the stored folder is gone.
        """
        if self._configured_folder(settings) is not None:
            return True
        folder = self._pick_folder(settings)
        if folder is None:
            return False
        self._remember_folder(settings, folder)
        return True

    def _check_index(self) -> None:
        """Look for a calibre index in the selected folder, off the GUI thread."""
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
            self._stop_window(_("OneDrive matching stopped without a result."))
            return
        if self._job_cancelled(job):
            self._stop_window(_("OneDrive sync cancelled."))
            return
        if getattr(job, "failed", False):
            exception = getattr(job, "exception", None)
            message = str(exception or _("Unknown error"))
            log.warning("Could not check the OneDrive folder: %s", message)
            missing = isinstance(exception, GraphError) and exception.status == 404
            if missing:
                self._stop_window(_("The configured OneDrive folder no longer exists."))
                self._reselect_folder()
            else:
                self._stop_window(
                    _("Could not check the OneDrive folder: {message}").format(message=message)
                )
            return

        available = getattr(job, "result", None)
        if not isinstance(available, bool):
            log.warning("The OneDrive index check returned no result")
            self._stop_window(_("Could not check the OneDrive folder for a calibre index."))
            return

        if not available:
            settings = self._engine.settings
            if not self._offer_fresh_upload(settings):
                self._stop_window(_("OneDrive sync cancelled."))
                return
            # Confirmed: clear the folder and upload everything, chained right
            # here so the progress runs in the window the user is looking at.
            self._next_task = "upload_all"
            self._start_task("clear_backup")
            return

        self._start_scan()

    def _start_scan(self) -> None:
        """Scan the folder into the review window that is already open."""
        dialog = self._review_dialog
        if dialog is None:
            self._close_state()
            return
        message = _("Comparing your library with OneDrive…")
        dialog.start_scanning(message)
        self._scan_plan = None
        self._scan_finished = False
        self._job = start_scan_job(
            self.gui.job_manager,
            self._engine,
            self._scan_done.emit,
            description=_("Matching OneDrive books"),
            on_plan=self._scan_progress.emit,
            on_progress=self._scan_status.emit,
        )
        self._show_status(message)

    def _close_window(self) -> None:
        """Close the review window; the flow continues in ``_finish_review``."""
        dialog = self._review_dialog
        if dialog is not None:
            dialog.reject()
            return
        self._close_state()

    def _stop_window(self, message: str) -> None:
        """Report a dead end in the window and release the sync state."""
        dialog = self._review_dialog
        if dialog is not None:
            dialog.set_failed(message)
            dialog.reject()
        self._close_state()
        self._show_status(message)

    def _reselect_folder(self) -> None:
        """Ask for a folder after the stored one turned out to be unusable."""
        settings = self._settings_for()
        if settings is None:
            self._close_state()
            return
        if not self._prepare_engine(settings):
            return
        folder = self._pick_folder(settings)
        if folder is None:
            self._close_state()
            return
        self._remember_folder(settings, folder)
        self._open_window()

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
        parent = self._review_dialog if self._review_dialog is not None else self.gui
        answer = QMessageBox.question(
            parent,
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
        try:
            adder = MainThreadBookAdder(
                lambda path: add_book_to_library(self.gui, path), parent=self.gui
            )
            state, library, linker, engine = self._build_engine(settings, add_book=adder)
        except Exception as exc:  # noqa: BLE001 - configuration/runtime failure
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

    def _build_engine(self, settings, *, add_book=None):
        """Build the graph/state/library stack; the caller owns the state.

        The manual flow and the automatic upload both run through this, so both
        write links, export records and the index the same way. On failure the
        half-built state is closed here, which keeps the callers' error paths
        free of connection bookkeeping.
        """
        library = CalibreLibrary(self.gui)
        auth = AuthManager(settings.client_id, ConfigTokenStore())
        graph = GraphClient(auth)
        state = StateStore(default_state_path())
        try:
            linker = MainThreadBookLinker(self._store_link, parent=self.gui)
            engine = SyncEngine(
                graph,
                state,
                library,
                settings,
                add_book=add_book,
                link_book=linker,
                metadata_reader=read_metadata,
            )
        except Exception:  # noqa: BLE001 - re-raised for the caller's own message
            try:
                state.close()
            except Exception:  # noqa: BLE001 - a dead connection must not hide the cause
                log.debug("Could not close the half-built sync state", exc_info=True)
            raise
        return state, library, linker, engine

    def _prepare_auto_engine(self, settings) -> bool:
        """Build a private engine for the automatic upload (never a dialog)."""
        configure_logging(settings.debug)
        try:
            state, library, _linker, engine = self._build_engine(settings)
        except Exception:  # noqa: BLE001 - one failed add must not stop calibre
            log.warning("Could not start the automatic OneDrive upload", exc_info=True)
            return False
        self._auto_state = state
        self._auto_library = library
        self._auto_engine = engine
        return True

    # -- automatic upload of newly added books ------------------------------

    def _install_auto_upload(self) -> None:
        """Watch the calibre database for new books, once, when the plugin is set up.

        The watcher is installed even while the automatic upload is switched off:
        the toggle is read again when a batch is drained, so changing it in the
        settings takes effect without restarting calibre.
        """
        if self._auto_watcher is not None:
            return
        settings = load_settings()
        if not (is_configured(settings) and get_refresh_token()):
            return
        try:
            api = self.gui.current_db.new_api
        except Exception:  # noqa: BLE001 - no library window yet
            log.debug("No calibre database to watch yet", exc_info=True)
            return
        watcher = LibraryWatcher(api, parent=self)
        if not watcher.start():
            return
        # calibre reports new books from its own DBListener thread; the queue and
        # the timer belong to the GUI thread, so the delivery is queued explicitly.
        watcher.books_created.connect(self._on_books_created, Qt.ConnectionType.QueuedConnection)
        self._auto_watcher = watcher

    def _stop_auto_upload(self) -> None:
        """Stop watching for new books and forget what is still queued."""
        watcher, self._auto_watcher = self._auto_watcher, None
        if watcher is not None:
            watcher.stop()
        self._auto_timer.stop()
        self._auto_queue.clear()

    def _auto_upload_enabled(self) -> bool:
        """True when a drained batch should really be uploaded."""
        settings = load_settings()
        return bool(
            settings.auto_upload_new_books and is_configured(settings) and get_refresh_token()
        )

    def _on_books_created(self, book_ids) -> None:
        """Queue a book calibre has just created and arm the batch timer."""
        if not self._auto_queue.add_many(book_ids):
            return
        self._start_auto_timer()

    def _start_auto_timer(self) -> None:
        """Wait :data:`AUTO_UPLOAD_DELAY_MS`; a whole import then uploads in one pass."""
        if not self._auto_timer.isActive():
            self._auto_timer.start()

    def _upload_new_books(self) -> None:
        """Upload the queued books, or wait while a manual pass owns the state."""
        self._clear_finished_auto_job()
        if not self._auto_queue:
            return
        if self._auto_job is not None or self._job is not None:
            # A manual pass is running. The books are retried later, and anything
            # that pass imported is linked by then, so it is not uploaded again.
            self._start_auto_timer()
            return
        if not self._auto_upload_enabled():
            self._auto_queue.clear()
            return
        settings = load_settings()
        if not self._prepare_auto_engine(settings):
            self._auto_queue.clear()
            return
        book_ids = self._auto_queue.take()
        self._auto_job = start_upload_books_job(
            self.gui.job_manager,
            self._auto_engine,
            book_ids,
            self._auto_upload_done.emit,
            description=_("Uploading new books to OneDrive"),
        )
        self._show_status(
            _("Uploading {count} new book(s) to OneDrive…").format(count=len(book_ids))
        )

    def _abort_auto_upload(self) -> None:
        """Stop a running automatic upload before a manual pass uses the state."""
        if self._auto_job is not None:
            self._abort_job(self._auto_job)

    def _on_auto_upload_done(self, job) -> None:
        """Report an automatic upload without a window or a dialog.

        The automatic pass runs while the user is working on their library, so a
        failure belongs in the log and the status bar, not in a modal box.
        """
        if job is not self._auto_job:
            log.debug("Ignoring the late callback of a superseded automatic upload")
            return
        self._auto_job = None
        self._close_auto_state()
        if job is None:
            return
        result = getattr(job, "result", None)
        if self._job_cancelled(job):
            log.info("Automatic OneDrive upload cancelled")
            self._show_status(_("Automatic OneDrive upload cancelled."))
            return
        if getattr(job, "failed", False):
            message = str(getattr(job, "exception", "") or _("Unknown error"))
            log.warning("Automatic OneDrive upload failed: %s", message)
            self._show_status(
                _("Automatic OneDrive upload failed: {message}").format(message=message)
            )
            return
        if result is None:
            return
        for message in result.errors:
            log.warning("Automatic OneDrive upload error: %s", message)
        self._show_status(result.summary())
        if self._review_dialog is not None and self._job is None and self._engine is not None:
            # The open window still shows the book as pending; refresh the rows.
            self._start_scan()

    def _clear_finished_auto_job(self) -> None:
        """Forget an automatic upload calibre finished without a callback."""
        job = self._auto_job
        if job is None or self._job_is_running(job):
            return
        self._auto_job = None
        self._close_auto_state()

    def _close_auto_state(self) -> None:
        if self._auto_state is not None:
            try:
                self._auto_state.close()
            except Exception:  # noqa: BLE001 - best effort
                log.debug("Could not close the automatic sync state", exc_info=True)
        self._auto_state = None
        self._auto_engine = None
        self._auto_library = None

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
        self._forget_stale_links()
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
        """React to the review window closing: the work itself ran inside it."""
        if not self._scan_finished:
            # Closed while the scan was still running: stop the worker and let
            # the completion callback do the clean-up.
            self._abort_job(self._job)
            self._show_status(_("OneDrive matching cancelled."))
            return
        if self._task:
            # Closed during a bulk action: a transfer the user hid is a transfer
            # the user does not want, and the job is killable by design.
            self._abort_job(self._job)
            self._next_task = ""
            self._show_status(_("OneDrive action cancelled."))
            return
        self._release_review()
        plan = self._scan_plan
        if accepted and plan is not None:
            self._show_status(plan.summary())

    # -- bulk actions -------------------------------------------------------

    def _on_bulk_request(self, action: str) -> None:
        """Start a bulk action requested from the open review window.

        The window stays open and receives the job's progress, which is the only
        place a two-hour upload can show its speed and remaining time.
        """
        if action == "apply":
            self._apply_selected()
            return
        if action in {"upload_all", "download_all", "clear_backup"}:
            self._next_task = ""
            self._start_task(action)

    def _apply_selected(self) -> None:
        """Run the rows ticked in the review window, reporting into that window."""
        dialog = self._review_dialog
        plan = self._scan_plan
        if dialog is None or plan is None:
            return
        self._abort_auto_upload()
        selected = dialog.selected_entries()
        if not selected:
            QMessageBox.information(self.gui, _("OneDrive Sync"), plan.summary())
            return
        self._job = start_apply_job(
            self.gui.job_manager,
            self._engine,
            plan,
            selected,
            self._adder,
            self._linker,
            self._apply_done.emit,
            description=_("Applying OneDrive changes"),
            on_plan=self._scan_progress.emit,
            on_progress=self._scan_status.emit,
        )
        # Marks the window busy, so closing it kills the job like a cancelled
        # transfer; the completion callback does the clean-up.
        self._task = "apply"
        dialog.start_task("apply")
        self._show_status(_("Applying the selected OneDrive changes…"))

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
        self._abort_auto_upload()
        self._task = task
        self._job = start_task_job(
            self.gui.job_manager,
            self._engine,
            task,
            self._task_done.emit,
            description=self._task_description(task),
            on_plan=self._scan_progress.emit,
            on_progress=self._scan_status.emit,
        )
        # Every path that starts a job owns the window's busy state, including
        # the clear-and-upload chain the index gate offers.
        dialog = self._review_dialog
        if dialog is not None:
            dialog.start_task(task)
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
        """Report a finished bulk action in the window, then refresh the rows."""
        task = self._task
        self._task = ""
        if self._is_stale_job(job):
            return
        self._job = None
        # ``_close_state`` drops the library adapter, but clearing the calibre
        # identifiers still needs it, so keep a reference for the result below.
        library = self._library
        dialog = self._review_dialog
        if job is None:
            self._next_task = ""
            self._end_task(dialog, _("OneDrive action stopped without a result."))
            return
        if self._job_cancelled(job):
            self._next_task = ""
            self._end_task(dialog, _("OneDrive action cancelled."))
            return
        if getattr(job, "failed", False):
            self._next_task = ""
            message = str(getattr(job, "exception", "") or _("Unknown error"))
            log.warning("OneDrive action %s failed: %s", task, message)
            self._end_task(
                dialog,
                _("OneDrive action failed: {message}").format(message=message),
                warning=True,
            )
            return

        result = getattr(job, "result", None)
        if result is None:
            self._next_task = ""
            self._end_task(dialog, _("OneDrive action stopped without a result."))
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

        summary = result.summary()
        for message in result.errors:
            log.warning("OneDrive action error: %s", message)
        self._end_task(dialog, summary, errors=result.errors)

    def _end_task(self, dialog, message: str, *, errors=(), warning: bool = False) -> None:
        """Finish a bulk action: report it, then let the window show the result.

        With the window still open the rows are scanned again, because an upload
        or import changes exactly what the list is about; a closed window only
        gets the summary, as before.
        """
        self._task = ""
        if dialog is None:
            self._close_state()
            self._show_status(message)
            if warning:
                QMessageBox.warning(self.gui, _("OneDrive Sync"), message)
                return
            self._report_errors(message, errors)
            return
        dialog.finish_task(message)
        self._show_status(message)
        if warning:
            QMessageBox.warning(dialog, _("OneDrive Sync"), message)
        else:
            self._report_errors(message, errors, parent=dialog)
        self._start_scan()

    def _clear_identifiers(self, library) -> None:
        """Drop the OneDrive link from every book after the backup was cleared."""
        try:
            cleared = library.clear_identifier(ONEDRIVE_IDENTIFIER)
        except Exception:  # noqa: BLE001 - the remote files are already gone
            log.warning("Could not clear the OneDrive identifiers", exc_info=True)
            return
        log.info("Cleared %s OneDrive identifier(s)", cleared)

    def _forget_stale_links(self) -> None:
        """Drop the calibre identifier of links the scan found to be broken.

        The scan has already removed the state rows; the identifier is the copy
        shown in the calibre book list, so it has to follow, and the book is
        offered for upload again.
        """
        stale = getattr(self._engine, "stale_links", ())
        if not stale:
            return
        self._engine.stale_links = []
        library = self._library
        if library is None:
            return
        cleared = 0
        for link in stale:
            try:
                library.set_identifier(link.book_id, ONEDRIVE_IDENTIFIER, "")
            except Exception:  # noqa: BLE001 - one book must not break the sweep
                log.warning(
                    "Could not clear the identifier of book %s", link.book_id, exc_info=True
                )
                continue
            cleared += 1
        if cleared:
            self._show_status(_("Removed {count} broken OneDrive link(s).").format(count=cleared))
            log.info("Cleared %s stale OneDrive identifier(s)", cleared)

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
            self._end_task(self._review_dialog, summary)
            return

        if getattr(job, "failed", False):
            message = str(getattr(job, "exception", "") or _("Unknown error"))
            log.warning("OneDrive sync failed: %s", message)
            self._end_task(
                self._review_dialog,
                _("Sync failed: {message}").format(message=message),
                warning=True,
            )
            return

        if result is None:
            return
        for message in result.errors:
            log.warning("Sync error: %s", message)
        self._end_task(self._review_dialog, result.summary(), errors=result.errors)

    def _report_errors(self, summary: str, errors, parent=None) -> None:
        """Show the result box; the errors are listed, ten at most."""
        parent = parent if parent is not None else self.gui
        if not errors:
            QMessageBox.information(parent, _("OneDrive Sync"), summary)
            return
        details = "\n".join(errors[:10])
        if len(errors) > 10:
            details += "\n…"
        QMessageBox.warning(parent, _("OneDrive Sync"), f"{summary}\n\n{details}")

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
