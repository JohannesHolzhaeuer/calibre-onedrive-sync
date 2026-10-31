"""Watch calibre's database for newly created books.

calibre dispatches database events from a background thread
(:mod:`calibre.db.listeners`): ``Cache.add_listener(callback)`` registers a
callable that receives ``(EventType, library_id, event_specific_data)``.
``EventType.book_created`` fires once for every book record calibre creates,
which is exactly "the user added a book" — that event is the only one this
plugin reacts to. Conversions, metadata edits and deletions stay work for the
manual review window.

The callback runs on calibre's ``DBListener`` thread, so the watcher never
touches the sync engine, the sync state or calibre metadata itself: it only
re-emits the ids through :attr:`LibraryWatcher.books_created`, and Qt delivers
that signal to the GUI thread that owns the action.
"""

from __future__ import annotations

from qt.core import QObject, pyqtSignal

from ..common.log import get_logger

log = get_logger("onedrive_sync.library_watch")

#: ``EventType.book_created.name`` — compared by name so the watcher still works
#: when the enum class cannot be imported (older calibre, headless tests).
BOOK_CREATED = "book_created"


def event_name(event_type) -> str:
    """The name of a calibre event, whether it arrives as enum or as string."""
    return str(getattr(event_type, "name", event_type) or "")


class LibraryWatcher(QObject):
    """Report every newly created calibre book to the GUI thread."""

    #: Emitted with a ``list[int]`` of book ids (one per database event).
    books_created = pyqtSignal(object)

    def __init__(self, api, parent=None) -> None:
        super().__init__(parent)
        self._api = api
        self._listening = False
        #: calibre keeps only a weak reference to a registered listener, and a
        #: bound method would be collected immediately, so hold it here.
        self._callback = self._on_db_event

    def start(self) -> bool:
        """Register the database listener; False when this calibre cannot."""
        add = getattr(self._api, "add_listener", None)
        if not callable(add):
            log.info("This calibre version cannot report newly added books")
            return False
        try:
            add(self._callback)
        except Exception:  # noqa: BLE001 - watching is a bonus, never fatal
            log.warning("Could not watch the calibre database", exc_info=True)
            return False
        self._listening = True
        log.info("Watching the calibre database for newly added books")
        return True

    def stop(self) -> None:
        """Unregister the listener (idempotent)."""
        if not self._listening:
            return
        self._listening = False
        remove = getattr(self._api, "remove_listener", None)
        if not callable(remove):
            return
        try:
            remove(self._callback)
        except Exception:  # noqa: BLE001 - shutdown must not raise
            log.debug("Could not stop watching the calibre database", exc_info=True)

    def _on_db_event(self, event_type, _library_id=None, args=()) -> None:
        """Forward one event; runs on calibre's ``DBListener`` thread."""
        if event_name(event_type) != BOOK_CREATED:
            return
        book_id = _first_book_id(args)
        if book_id is None:
            return
        try:
            self.books_created.emit([book_id])
        except Exception:  # noqa: BLE001 - a dead GUI must not break calibre
            log.debug("Could not report a newly added book", exc_info=True)


def _first_book_id(args) -> int | None:
    """The book id of a ``book_created`` event payload."""
    try:
        return int(args[0])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
