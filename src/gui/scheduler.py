"""Event-driven sync scheduling.

Remote changes are discovered with Graph delta queries; locally, calibre's DB
listener and a ``QFileSystemWatcher`` fallback notice library edits. Both feed a
debounce timer so a burst of events (e.g. adding a book creates metadata,
format, and book-created events) results in one sync. A periodic timer catches
changes made while calibre was closed.
"""

from __future__ import annotations

from qt.core import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from ..common.log import get_logger

log = get_logger("onedrive_sync.scheduler")

_WATCHED_EVENTS = frozenset(
    {
        "metadata_changed",
        "format_added",
        "formats_removed",
        "book_created",
        "books_removed",
        "book_edited",
    }
)


class SyncScheduler(QObject):
    """Emit :attr:`sync_requested` after local/remote changes or on a timer."""

    sync_requested = pyqtSignal(str)
    _db_event = pyqtSignal(str)

    def __init__(self, parent=None, *, debounce_seconds: float = 5.0) -> None:
        super().__init__(parent)
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._library = None
        self._listener = None
        self._active = False
        self._suspended = False
        self._pending = False
        self._watched: set[str] = set()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_periodic)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._on_debounce)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_path_changed)
        self._watcher.fileChanged.connect(self._on_path_changed)
        self._db_event.connect(self._on_db_event)

    # -- lifecycle ----------------------------------------------------------

    def start(self, library, *, interval_minutes: int) -> None:
        """Begin watching ``library`` and fire every ``interval_minutes``."""
        self.stop()
        self._library = library
        self._active = True
        self._attach_listener(library)
        api = getattr(library, "api", None)
        path = getattr(getattr(library, "gui", None), "current_db", None)
        path = getattr(path, "library_path", None)
        if path:
            self.watch_paths([str(path)])
        self.set_interval(interval_minutes)
        self._timer.start()
        if api is None:
            log.debug("Library has no API; only the timer drives syncs")

    def stop(self) -> None:
        """Stop timers and detach all listeners."""
        self._active = False
        self._pending = False
        self._timer.stop()
        self._debounce.stop()
        self._detach_listener()
        self.unwatch_paths()
        self._library = None

    def suspend(self) -> None:
        """Ignore events while a sync is running (prevents feedback loops)."""
        self._suspended = True

    def resume(self) -> None:
        self._suspended = False

    def set_interval(self, minutes: int) -> None:
        self._timer.setInterval(max(1, int(minutes)) * 60 * 1000)

    # -- events -------------------------------------------------------------

    def notify_change(self, reason: str = "change") -> None:
        """Request a sync after a short debounce window."""
        if not self._active:
            return
        self._pending = True
        self._debounce.start()
        log.debug("Sync requested: %s", reason)

    def watch_paths(self, paths) -> None:
        """Watch local paths (best effort; missing paths are ignored)."""
        for path in paths:
            path = str(path)
            if not path or path in self._watched:
                continue
            if self._watcher.addPath(path):
                self._watched.add(path)

    def unwatch_paths(self) -> None:
        if self._watched:
            self._watcher.removePaths(sorted(self._watched))
            self._watched.clear()

    def _attach_listener(self, library) -> None:
        add_listener = getattr(getattr(library, "api", None), "add_listener", None)
        if add_listener is None:
            return

        def listener(event_type, _library_id, _event_data) -> None:
            name = getattr(event_type, "name", str(event_type).rsplit(".", 1)[-1])
            if name in _WATCHED_EVENTS:
                self._db_event.emit(name)

        try:
            add_listener(listener)
            self._listener = listener
        except Exception:  # noqa: BLE001 - scheduling is best-effort
            log.debug("Could not attach the calibre DB listener", exc_info=True)

    def _detach_listener(self) -> None:
        remove_listener = getattr(getattr(self._library, "api", None), "remove_listener", None)
        if self._listener is not None and remove_listener is not None:
            try:
                remove_listener(self._listener)
            except Exception:  # noqa: BLE001 - library may already be closed
                log.debug("Could not detach the calibre DB listener", exc_info=True)
        self._listener = None

    # -- slots --------------------------------------------------------------

    def _on_db_event(self, name: str) -> None:
        self.notify_change(f"library {name}")

    def _on_path_changed(self, path) -> None:
        self.notify_change(f"path {path}")

    def _on_debounce(self) -> None:
        if not self._active or self._suspended or not self._pending:
            self._pending = False
            return
        self._pending = False
        self.sync_requested.emit("change")

    def _on_periodic(self) -> None:
        if not self._active or self._suspended:
            return
        self.sync_requested.emit("scheduled")
