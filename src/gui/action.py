"""OneDrive Sync InterfaceAction: toolbar button with a small menu.

The action exposes *Sync now* and *Open configuration…*. Syncs run in calibre
threaded jobs; the scheduler reacts to calibre DB changes and a timer, so the
plugin also works without a local OneDrive client.
"""

from __future__ import annotations

from calibre.gui2.actions import InterfaceAction
from qt.core import QMenu, QMessageBox, pyqtSignal

from ..common.auth import AuthManager
from ..common.config import ConfigTokenStore, get_refresh_token, is_configured, load_settings
from ..common.graph import GraphClient
from ..common.i18n import _
from ..common.log import configure_logging, get_logger
from ..common.state import StateStore, default_state_path
from ..common.sync import SyncEngine
from .config_widget import open_config as open_config_dialog
from .library import CalibreLibrary, add_book_to_library
from .scheduler import SyncScheduler
from .sync_job import MainThreadBookAdder, start_sync_job

log = get_logger("onedrive_sync.action")


class OneDriveSyncInterfaceAction(InterfaceAction):
    """Toolbar action that triggers a sync or opens the configuration."""

    name = "OneDrive Sync"
    action_type = "current"
    action_spec = (
        _("OneDrive Sync"),
        None,
        _("Sync your calibre library with a OneDrive folder"),
        None,
    )

    _job_done = pyqtSignal(object)

    def genesis(self):
        """Set up the action icon, menu, scheduler and job callback."""
        self._job = None
        self._state: StateStore | None = None
        self.qaction.setIcon(
            get_icons(  # noqa: F821 - injected by calibre's plugin loader
                "resources/images/onedrive.png", "onedrive_sync"
            )
        )
        self.menu = QMenu(self.gui)
        self.menu.addAction(_("Sync now"), self.sync_now)
        self.menu.addAction(_("Open configuration…"), self.open_config)
        self.qaction.setMenu(self.menu)

        self._scheduler = SyncScheduler(self.gui)
        self._scheduler.sync_requested.connect(self.sync_now)
        self._job_done.connect(self._on_job_done)

    def initialization_complete(self):
        """Start automatic syncing once the GUI is fully initialized."""
        self._configure_scheduler()

    def library_changed(self, _db):
        """Restart the scheduler for the newly opened library."""
        self._configure_scheduler()

    def shutting_down(self):
        """Stop timers/listeners and close the sync state database."""
        try:
            self._scheduler.stop()
        except Exception:  # noqa: BLE001 - shutdown must not throw
            log.debug("Could not stop the scheduler", exc_info=True)
        if self._job is None:
            self._close_state()

    # -- menu actions -------------------------------------------------------

    def open_config(self) -> bool:
        """Open the configuration dialog and re-arm the scheduler on accept."""
        changed = open_config_dialog(self.gui)
        if changed:
            self._configure_scheduler()
        return changed

    def sync_now(self, *args):
        """Validate configuration and start a background sync."""
        reason = args[0] if args and isinstance(args[0], str) else "manual"
        if self._job is not None:
            log.debug("Ignoring sync request (%s): a sync is already running", reason)
            return

        settings = load_settings()
        if not is_configured(settings) or not get_refresh_token():
            if reason != "manual":
                log.debug("Automatic sync skipped: plugin is not configured/signed in")
                return
            if not self.open_config():
                return
            settings = load_settings()
            if not is_configured(settings) or not get_refresh_token():
                return

        configure_logging(settings.debug)
        try:
            library = CalibreLibrary(self.gui)
            auth = AuthManager(settings.client_id, ConfigTokenStore())
            graph = GraphClient(auth)
            state = StateStore(default_state_path())
            adder = MainThreadBookAdder(
                lambda path: add_book_to_library(self.gui, path), parent=self.gui
            )
            engine = SyncEngine(graph, state, library, settings, add_book=adder)
            self._state = state
            self._job = start_sync_job(
                self.gui.job_manager,
                engine,
                adder,
                self._job_done.emit,
                description=_("Syncing with OneDrive"),
            )
        except Exception as exc:  # noqa: BLE001 - configuration/runtime failure
            self._close_state()
            log.warning("Could not start the OneDrive sync", exc_info=True)
            QMessageBox.warning(
                self.gui,
                _("OneDrive Sync"),
                _("Could not start the sync: {message}").format(message=exc),
            )
            return

        self._scheduler.suspend()
        self._show_status(_("OneDrive sync started…"))

    # -- job callback -------------------------------------------------------

    def _on_job_done(self, job) -> None:
        self._scheduler.resume()
        self._job = None
        self._close_state()
        if job is None:
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

        result = getattr(job, "result", None)
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

    def _configure_scheduler(self) -> None:
        try:
            scheduler = self._scheduler
        except AttributeError:  # genesis not called yet
            return
        scheduler.stop()
        settings = load_settings()
        configure_logging(settings.debug)
        if not settings.auto_sync or not is_configured(settings) or not get_refresh_token():
            return
        try:
            library = CalibreLibrary(self.gui)
            scheduler.start(library, interval_minutes=settings.sync_interval_minutes)
        except Exception:  # noqa: BLE001 - no library / DB listener issues
            log.debug("Could not start the sync scheduler", exc_info=True)

    def _close_state(self) -> None:
        if self._state is not None:
            try:
                self._state.close()
            except Exception:  # noqa: BLE001 - best effort
                log.debug("Could not close the sync state", exc_info=True)
            self._state = None

    def _show_status(self, message: str) -> None:
        try:
            self.gui.status_bar.show_message(message, 5000)
        except Exception:  # noqa: BLE001 - status bar is cosmetic
            pass
