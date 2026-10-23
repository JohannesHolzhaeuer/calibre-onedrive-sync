"""OneDrive Sync InterfaceAction: toolbar button with a small menu.

The action exposes *Sync now* and *Open configuration…*. The sync engine itself
is intentionally not wired yet — this milestone establishes the plugin, its
configuration and the release/CI infrastructure. ``sync_now`` validates the
configuration and reports that the engine is coming in a follow-up change.
"""

from __future__ import annotations

from calibre.gui2.actions import InterfaceAction
from qt.core import QMenu, QMessageBox

from ..common.config import is_configured, load_settings
from ..common.i18n import _
from ..common.log import get_logger
from ..common.paths import validate_settings
from .config_widget import open_config

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

    def genesis(self):
        """Set up the action icon and its menu.

        ``get_icons`` is injected into this module's namespace by calibre's
        plugin loader, so it is used directly instead of importing it.
        """
        self.qaction.setIcon(
            get_icons(  # noqa: F821 - injected by calibre's plugin loader
                "resources/images/onedrive.png", "onedrive_sync"
            )
        )
        self.menu = QMenu(self.gui)
        self.menu.addAction(_("Sync now"), self.sync_now)
        self.menu.addAction(_("Open configuration…"), self.open_config)
        self.qaction.setMenu(self.menu)

    def _library_path(self) -> str:
        """Return the currently open library's path, or ``''``."""
        try:
            return str(self.gui.current_db.library_path)
        except Exception:  # noqa: BLE001 - the GUI may not have a library yet
            return ""

    def open_config(self) -> bool:
        """Open the configuration dialog."""
        return open_config(self.gui, default_library_path=self._library_path())

    def sync_now(self):
        """Validate the configuration and report the scaffold status.

        The actual copy/mirror engine arrives in a follow-up change. Until then
        this entry point makes configuration errors visible and confirms that
        the stored settings are usable.
        """
        settings = load_settings()
        if not is_configured(settings) or validate_settings(settings):
            if not self.open_config():
                return
            settings = load_settings()
            if not is_configured(settings) or validate_settings(settings):
                return
        log.debug("Sync requested for %s", settings.onedrive_folder)
        QMessageBox.information(
            self.gui,
            _("OneDrive Sync"),
            _("Configuration saved. The sync engine will be added in a follow-up change."),
        )
