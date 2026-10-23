"""onedrive-sync calibre plugin.

This module is the entry point calibre loads from the plugin zip. Calibre
registers **exactly one** :class:`Plugin` subclass per zip, so this plugin ships
a single **interface action**:

* ``OneDriveSyncAction`` — an :class:`InterfaceActionBase` that adds a
  "OneDrive Sync" toolbar button with a menu (*Sync now*, *Open configuration*).
  Its ``do_user_config`` opens the configuration dialog used by
  *Preferences → Plugins → Customize plugin*.

Keep top-level imports minimal (only ``calibre.customize``). Do NOT import PyQt
or any other heavy module at module scope.
"""

from calibre.customize import InterfaceActionBase

from .common.i18n import _
from .common.version import VERSION_TUPLE as VERSION_TUPLE
from .common.version import __version__ as __version__


class OneDriveSyncAction(InterfaceActionBase):
    """OneDrive interface action: keep the calibre library in sync."""

    name = "OneDrive Sync"
    description = "Sync the calibre library with a folder in your OneDrive."
    author = "JohannesHolzhaeuer"
    version = VERSION_TUPLE
    minimum_calibre_version = (9, 0, 0)
    actual_plugin = "calibre_plugins.onedrive_sync.gui.action:OneDriveSyncInterfaceAction"

    def initialize(self):
        """Load translations + wire the shared logger."""
        from .common.i18n import activate

        activate(globals())

    def do_user_config(self, gui=None):
        """Open the configuration dialog (picked up by Preferences)."""
        from .gui.config_widget import open_config

        default_library_path = ""
        try:
            default_library_path = str(gui.current_db.library_path)
        except Exception:  # noqa: BLE001 - no library open / headless caller
            pass
        open_config(parent=gui, default_library_path=default_library_path)

    def customization_help(self, gui=False):
        """Return help text so the plugin is reported as customizable.

        The default ``Plugin.customization_help`` raises ``NotImplementedError``,
        which makes ``is_customizable()`` False and disables the *Customize
        plugin* button in Preferences. Overriding it opts this plugin into the
        customization flow that ends at :meth:`do_user_config`.
        """
        return _("Choose your calibre library folder and the OneDrive folder to sync with.")
