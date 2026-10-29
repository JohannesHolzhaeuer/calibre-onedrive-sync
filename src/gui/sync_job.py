"""Run sync/review work in a calibre threaded job and marshal GUI-thread calls.

The sync engine performs network I/O in a worker thread. Adding books to the
calibre database (and writing calibre metadata) must happen on the GUI thread,
so :class:`MainThreadCall` uses a blocking cross-thread signal: the worker waits
until the GUI thread has performed the call and handed back the result.

All jobs are ``killable`` so the user can stop them from calibre's jobs list.
calibre marks a killed job as ``failed`` (with no exception) and, when the job
was still queued, never calls the completion callback at all -- so the action
inspects :attr:`worker_active` and ``job.killed`` instead of trusting
``failed`` alone.
"""

from __future__ import annotations

from calibre.gui2.threaded_jobs import ThreadedJob
from qt.core import QObject, Qt, QThread, pyqtSignal

from ..common.i18n import _
from ..common.sync import SyncEngine


class MainThreadCall(QObject):
    """Callable that runs ``callback(value)`` on the object's (GUI) thread."""

    _call = pyqtSignal(object)

    def __init__(self, callback, parent=None) -> None:
        super().__init__(parent)
        self._callback = callback
        self._call.connect(self._handle, Qt.ConnectionType.BlockingQueuedConnection)

    def __call__(self, value):
        if QThread.currentThread() == self.thread():
            return self._callback(value)
        box: dict = {}
        self._call.emit((value, box))
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _handle(self, payload) -> None:
        value, box = payload
        try:
            box["result"] = self._callback(value)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the worker
            box["error"] = exc


class MainThreadBookAdder(MainThreadCall):
    """Callable that imports a downloaded book on the GUI thread."""


class MainThreadBookLinker(MainThreadCall):
    """Callable that stores a OneDrive link in calibre metadata on the GUI thread."""


def _location(engine: SyncEngine) -> str:
    return engine.settings.folder_path or engine.settings.folder_id


class OneDriveJob(ThreadedJob):
    """Killable threaded job that reports whether its worker is still running."""

    #: True while ``_run`` executes; the action uses it to tell a killed but
    #: still-running job from one that never started (calibre skips the
    #: callback for jobs killed while queued).
    worker_active = False

    def __init__(self, description, callback) -> None:
        super().__init__(
            "onedrive_sync",
            description,
            self._run_guarded,
            (),
            {},
            callback,
            max_concurrent_count=1,
            killable=True,
        )

    def _run_guarded(self, notifications, abort, log):
        self.worker_active = True
        try:
            return self._run(notifications, abort, log)
        finally:
            self.worker_active = False

    def _run(self, notifications, abort, log):  # pragma: no cover - overridden
        raise NotImplementedError


class ScanJob(OneDriveJob):
    """A ``ThreadedJob`` that compares the library with OneDrive for review."""

    def __init__(
        self,
        engine: SyncEngine,
        callback,
        *,
        description: str | None = None,
        on_plan=None,
        on_progress=None,
    ) -> None:
        self.engine = engine
        self._on_plan = on_plan
        self._on_progress = on_progress
        super().__init__(description or _("Matching OneDrive books"), callback)

    def _run(self, notifications, abort, log):
        self.engine.on_progress = lambda fraction, message: self._progress(
            notifications, fraction, message
        )
        self.engine.on_plan = self._on_plan
        self.engine.should_abort = abort.is_set
        log(f"Scanning OneDrive for {_location(self.engine)}")
        plan = self.engine.scan()
        log(plan.summary())
        return plan

    def _progress(self, notifications, fraction: float, message: str) -> None:
        """Report to calibre's job panel and to the live review window."""
        notifications.put((fraction, message))
        if self._on_progress is None:
            return
        try:
            self._on_progress(fraction, message)
        except Exception:  # noqa: BLE001 - a status update must not kill the scan
            pass


class IndexJob(OneDriveJob):
    """A ``ThreadedJob`` that checks whether the folder has a calibre index."""

    def __init__(self, engine: SyncEngine, callback, *, description: str | None = None) -> None:
        self.engine = engine
        super().__init__(description or _("Checking the OneDrive folder"), callback)

    def _run(self, notifications, abort, log):
        self.engine.should_abort = abort.is_set
        log(f"Checking the calibre index for {_location(self.engine)}")
        return self.engine.index_available()


class TaskJob(OneDriveJob):
    """A ``ThreadedJob`` that runs one bulk engine operation.

    ``operation`` is the name of an engine method: ``upload_all``,
    ``download_all`` or ``clear_backup``.
    """

    def __init__(
        self,
        engine: SyncEngine,
        operation: str,
        callback,
        *,
        description: str | None = None,
        on_plan=None,
        on_progress=None,
    ) -> None:
        self.engine = engine
        self.operation = str(operation)
        self._on_plan = on_plan
        self._on_progress = on_progress
        super().__init__(description or _("OneDrive Sync"), callback)

    def _run(self, notifications, abort, log):
        self.engine.on_progress = lambda fraction, message: self._progress(
            notifications, fraction, message
        )
        #: Rows are published straight to the window, which shows the pass running
        #: through the list instead of a progress bar with no content.
        self.engine.on_plan = self._on_plan
        self.engine.should_abort = abort.is_set
        log(f"Running {self.operation} for {_location(self.engine)}")
        result = getattr(self.engine, self.operation)()
        log(result.summary())
        for message in result.errors:
            log(message)
        return result

    def _progress(self, notifications, fraction: float, message: str) -> None:
        """Report to calibre's job panel and to the open review window."""
        notifications.put((fraction, message))
        if self._on_progress is None:
            return
        try:
            self._on_progress(fraction, message)
        except Exception:  # noqa: BLE001 - a status update must not kill the transfer
            pass


class ApplyJob(OneDriveJob):
    """A ``ThreadedJob`` that executes the rows selected in the review dialog."""

    def __init__(
        self,
        engine: SyncEngine,
        plan,
        entries,
        add_book,
        link_book,
        callback,
        *,
        description: str | None = None,
        on_plan=None,
        on_progress=None,
    ) -> None:
        self.engine = engine
        self.plan = plan
        self.entries = list(entries)
        self._add_book = add_book
        self._link_book = link_book
        self._on_plan = on_plan
        self._on_progress = on_progress
        super().__init__(description or _("Applying OneDrive changes"), callback)

    def _run(self, notifications, abort, log):
        self.engine.on_progress = lambda fraction, message: self._progress(
            notifications, fraction, message
        )
        self.engine.on_plan = self._on_plan
        self.engine.should_abort = abort.is_set
        self.engine.add_book = self._add_book
        self.engine.link_book = self._link_book
        log(f"Applying {len(self.entries)} OneDrive review row(s)")
        result = self.engine.apply_plan(self.plan, self.entries)
        log(result.summary())
        for message in result.errors:
            log(message)
        return result

    def _progress(self, notifications, fraction: float, message: str) -> None:
        """Report to calibre's job panel and to the open review window."""
        notifications.put((fraction, message))
        if self._on_progress is None:
            return
        try:
            self._on_progress(fraction, message)
        except Exception:  # noqa: BLE001 - a status update must not kill the transfer
            pass


def start_index_job(
    job_manager,
    engine: SyncEngine,
    callback,
    *,
    description=None,
):
    """Create and enqueue an :class:`IndexJob`; returns the job."""
    job = IndexJob(engine, callback, description=description)
    job_manager.run_threaded_job(job)
    return job


def start_task_job(
    job_manager,
    engine: SyncEngine,
    operation: str,
    callback,
    *,
    description=None,
    on_plan=None,
    on_progress=None,
):
    """Create and enqueue a :class:`TaskJob`; returns the job."""
    job = TaskJob(
        engine,
        operation,
        callback,
        description=description,
        on_plan=on_plan,
        on_progress=on_progress,
    )
    job_manager.run_threaded_job(job)
    return job


def start_scan_job(
    job_manager,
    engine: SyncEngine,
    callback,
    *,
    description=None,
    on_plan=None,
    on_progress=None,
):
    """Create and enqueue a :class:`ScanJob`; returns the job."""
    job = ScanJob(
        engine,
        callback,
        description=description,
        on_plan=on_plan,
        on_progress=on_progress,
    )
    job_manager.run_threaded_job(job)
    return job


def start_apply_job(
    job_manager,
    engine: SyncEngine,
    plan,
    entries,
    add_book,
    link_book,
    callback,
    *,
    description=None,
    on_plan=None,
    on_progress=None,
):
    """Create and enqueue an :class:`ApplyJob`; returns the job."""
    job = ApplyJob(
        engine,
        plan,
        entries,
        add_book,
        link_book,
        callback,
        description=description,
        on_plan=on_plan,
        on_progress=on_progress,
    )
    job_manager.run_threaded_job(job)
    return job
