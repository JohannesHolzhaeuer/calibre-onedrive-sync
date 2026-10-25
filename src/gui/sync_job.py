"""Run a sync in a calibre threaded job and marshal book imports to the GUI thread.

The sync engine performs network I/O in a worker thread. Adding books to the
calibre database must happen on the GUI thread, so :class:`MainThreadBookAdder`
uses a blocking cross-thread signal: the worker waits until the GUI thread has
imported the book and handed back its id.
"""

from __future__ import annotations

from calibre.gui2.threaded_jobs import ThreadedJob
from qt.core import QObject, Qt, QThread, pyqtSignal

from ..common.i18n import _
from ..common.sync import SyncEngine


class MainThreadBookAdder(QObject):
    """Callable that runs ``callback(path)`` on the object's (GUI) thread."""

    _call = pyqtSignal(object)

    def __init__(self, callback, parent=None) -> None:
        super().__init__(parent)
        self._callback = callback
        self._call.connect(self._handle, Qt.ConnectionType.BlockingQueuedConnection)

    def __call__(self, path: str):
        if QThread.currentThread() == self.thread():
            return self._callback(path)
        box: dict = {}
        self._call.emit((path, box))
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _handle(self, payload) -> None:
        path, box = payload
        try:
            box["result"] = self._callback(path)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the worker
            box["error"] = exc


class SyncJob(ThreadedJob):
    """A ``ThreadedJob`` that runs one import/export pass."""

    def __init__(
        self,
        engine: SyncEngine,
        add_book,
        callback,
        *,
        description: str | None = None,
    ) -> None:
        self.engine = engine
        self._add_book = add_book
        super().__init__(
            "onedrive_sync",
            description or _("OneDrive Sync"),
            self._run,
            (),
            {},
            callback,
            max_concurrent_count=1,
            killable=False,
        )

    def _run(self, notifications, abort, log):
        self.engine.on_progress = lambda fraction, message: notifications.put((fraction, message))
        self.engine.should_abort = abort.is_set
        self.engine.add_book = self._add_book
        location = self.engine.settings.folder_path or self.engine.settings.folder_id
        log(f"Starting OneDrive sync for {location}")
        result = self.engine.run()
        log(result.summary())
        for message in result.errors:
            log(message)
        return result


def start_sync_job(job_manager, engine: SyncEngine, add_book, callback, *, description=None):
    """Create and enqueue a :class:`SyncJob`; returns the job."""
    job = SyncJob(engine, add_book, callback, description=description)
    job_manager.run_threaded_job(job)
    return job
