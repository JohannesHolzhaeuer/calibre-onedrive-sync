"""Bookkeeping for the one automatic path: uploading books calibre just added.

The plugin is manual everywhere else, so this module keeps the automatic part as
small and as testable as possible. It holds no Qt and no network code:

* :class:`BookUploadQueue` collects the book ids calibre reports while the user
  adds books. Adding a hundred books fires a hundred events, and one upload pass
  for the batch is both cheaper and easier to watch than a pass per book.
* :data:`AUTO_UPLOAD_DELAY_MS` is the moment the caller waits before draining the
  queue. calibre creates the book *record* before the format files are written,
  so an upload started straight from the event would find nothing to send; a
  couple of seconds is longer than calibre needs for the rest of the import and
  short enough that the book is in OneDrive almost immediately.

The GUI layer (``gui.library_watch`` and ``gui.action``) owns the calibre
database listener and the timer; this module only answers "which books are still
waiting?".
"""

from __future__ import annotations

from collections.abc import Iterable

#: How long to collect new books before uploading them.
#:
#: calibre's ``add_books`` creates the book record first and writes the format
#: afterwards, so a pass started from the ``book_created`` event itself would see
#: a book with no format and skip it. The delay also turns "add 40 books" into
#: one pass instead of 40 network calls per book.
AUTO_UPLOAD_DELAY_MS = 2500


class BookUploadQueue:
    """The book ids waiting for the automatic upload, in the order they arrived.

    Only the GUI thread touches an instance, so no locking is needed. Ids are
    de-duplicated: calibre reports a book once, but a retry (because a manual
    job was running) re-adds what it could not upload yet without uploading any
    book twice.
    """

    def __init__(self) -> None:
        self._ids: list[int] = []
        self._seen: set[int] = set()

    def add(self, book_id: int) -> bool:
        """Queue one book id; returns True when it was not already queued."""
        return self.add_many([book_id]) == 1

    def add_many(self, book_ids: Iterable[int]) -> int:
        """Queue several book ids; returns how many were newly added."""
        added = 0
        for book_id in book_ids:
            try:
                value = int(book_id)
            except (TypeError, ValueError):
                continue
            if value <= 0 or value in self._seen:
                continue
            self._seen.add(value)
            self._ids.append(value)
            added += 1
        return added

    def take(self) -> list[int]:
        """Return every queued id and empty the queue."""
        taken, self._ids, self._seen = self._ids, [], set()
        return taken

    def clear(self) -> None:
        """Forget the queued ids without uploading them."""
        self._ids.clear()
        self._seen.clear()

    def __len__(self) -> int:
        return len(self._ids)

    def __bool__(self) -> bool:
        return bool(self._ids)
