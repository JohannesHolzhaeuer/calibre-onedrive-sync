"""Tests for the progressive review dialog (headless Qt shims)."""

from __future__ import annotations

from dataclasses import replace

from qt.core import QMessageBox, Qt

from onedrive_sync.common.index import BookIndex, IndexEntry
from onedrive_sync.common.models import RemoteItem
from onedrive_sync.common.reconcile import (
    LocalBook,
    ReconcileEntry,
    ReconcilePlan,
    build_pending_plan,
    build_reconcile_plan,
)
from onedrive_sync.gui import review_dialog
from onedrive_sync.gui.review_dialog import ReviewDialog, _stable_key, _status_label


def remote(item_id, name, *, drive="drive", web_url=""):
    return RemoteItem(id=item_id, name=name, drive_id=drive, web_url=web_url)


def plan():
    locals_ = [
        LocalBook(book_id=1, title="Matched", authors="Author", formats=("epub",)),
        LocalBook(book_id=2, title="Upload", authors="Author", formats=("epub",)),
    ]
    remotes = [
        remote("m", "Author - Matched.epub", web_url="https://onedrive.test/m"),
        remote("extra", "Author - Matched copy.epub"),
        remote("d", "Unknown - Download.epub"),
    ]
    return build_reconcile_plan(locals_, remotes)


def test_dialog_lists_entries_and_defaults():
    dialog = ReviewDialog(plan())
    assert dialog.tree.topLevelItemCount() == len(dialog.plan.entries)
    selected = dialog.selected_entries()
    assert {entry.status for entry in selected} == {"upload", "download"}
    assert all(entry.default_selected for entry in selected)
    # A finished plan is immediately actionable.
    assert dialog.apply_button.isEnabled()


def test_dialog_select_all_includes_duplicates():
    dialog = ReviewDialog(plan())
    dialog._set_all(True)
    selected = dialog.selected_entries()
    assert {entry.status for entry in selected} == {"upload", "download", "duplicate"}
    dialog._set_all(False)
    assert dialog.selected_entries() == []
    assert dialog.apply_button.isEnabled()


def test_dialog_does_not_check_matched_rows():
    dialog = ReviewDialog(plan())
    matched = next(entry for entry in dialog.plan.entries if entry.status == "matched")
    item = dialog._items[matched.key]
    assert not matched.selectable
    assert item.checkState(0) == Qt.CheckState.Unchecked


def test_empty_dialog_starts_in_scanning_state():
    dialog = ReviewDialog()
    assert dialog.tree.topLevelItemCount() == 0
    assert not dialog.apply_button.isEnabled()
    dialog.start_scanning()
    assert dialog.progress.isVisible()
    assert dialog.progress.range() == (0, 100)
    assert dialog.progress.value() == 0
    assert "Scanning OneDrive" in dialog.status.text()
    assert not dialog.apply_button.isEnabled()
    assert not dialog.select_all_button.isEnabled()


def test_set_progress_moves_the_bar_and_never_goes_back():
    dialog = ReviewDialog()
    dialog.start_scanning()
    dialog.set_progress(0.05, "Listing files in OneDrive…")
    assert dialog.progress.value() == 5
    assert dialog.status.text() == "Listing files in OneDrive…"

    dialog.set_progress(0.42, "Listing OneDrive… 3 folder(s), 5 file(s) found")
    assert dialog.progress.value() == 42
    assert "3 folder(s)" in dialog.status.text()

    # A later snapshot must not move the bar backwards.
    dialog.set_progress(0.1, "Listing files in OneDrive…")
    assert dialog.progress.value() == 42

    # Out-of-range and junk values are clamped instead of raising.
    dialog.set_progress(2.0)
    assert dialog.progress.value() == 100
    dialog.set_progress("nonsense")
    assert dialog.progress.value() == 100
    dialog.set_progress(-1.0)
    assert dialog.progress.value() == 100

    dialog.finish_scan(build_reconcile_plan([], []))
    assert not dialog.progress.isVisible()
    assert dialog.progress.value() == 100


def test_progress_message_wins_over_the_generic_phase_text():
    locals_ = [LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",))]
    dialog = ReviewDialog()
    dialog.start_scanning()
    dialog.set_progress(0.3, "Listing OneDrive… 7 folder(s), 12 file(s) found")
    dialog.set_plan(build_pending_plan(locals_), "listing")
    assert dialog.status.text() == "Listing OneDrive… 7 folder(s), 12 file(s) found"

    # Starting a fresh scan clears the stale message.
    dialog.start_scanning()
    dialog.set_plan(build_pending_plan(locals_), "library")
    assert "Found 1 book(s)" in dialog.status.text()


def test_listing_phase_fallback_text():
    dialog = ReviewDialog()
    dialog.start_scanning()
    dialog.set_plan(build_pending_plan([], [remote("a", "a.epub")]), "listing")
    assert "1 file(s) found so far" in dialog.status.text()


def test_rows_fill_in_while_scanning():
    locals_ = [
        LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",)),
        LocalBook(book_id=2, title="Fresh", authors="Jane Doe", formats=("epub",)),
    ]
    dialog = ReviewDialog()
    dialog.start_scanning()

    pending = build_pending_plan(locals_)
    dialog.set_plan(pending, "library")
    assert dialog.tree.topLevelItemCount() == 2
    assert "Found 2 book(s)" in dialog.status.text()
    assert dialog._items[pending.entries[0].key].text(1) == "Checking…"
    assert not dialog.apply_button.isEnabled()

    listed = build_reconcile_plan(locals_, [remote("d", "scan001.epub")])
    dialog.set_plan(listed, "onedrive")
    assert "Reading book metadata" in dialog.status.text()
    assert dialog.selected_entries() == listed.default_selection()
    assert not dialog.apply_button.isEnabled()

    matched = build_reconcile_plan(
        locals_,
        [remote("d", "scan001.epub")],
        metadata={"d": ("The Book", "Jane Doe")},
    )
    dialog.finish_scan(matched)
    assert not dialog.progress.isVisible()
    assert dialog.apply_button.isEnabled()
    assert {entry.status for entry in dialog.plan.entries} == {"matched", "upload"}


def test_finish_scan_without_actions_disables_apply():
    dialog = ReviewDialog()
    dialog.start_scanning()
    dialog.finish_scan(
        build_reconcile_plan(
            [LocalBook(book_id=1, title="Only Mobi", formats=("mobi",))],
            [],
        )
    )
    assert not dialog.apply_button.isEnabled()
    assert dialog.progress.isVisible() is False


def test_live_updates_keep_the_users_ticks():
    dialog = ReviewDialog(plan())
    upload = next(entry for entry in dialog.plan.entries if entry.status == "upload")
    download = next(entry for entry in dialog.plan.entries if entry.status == "download")
    dialog._items[download.key].setCheckState(0, Qt.CheckState.Unchecked)
    dialog._items[upload.key].setCheckState(0, Qt.CheckState.Unchecked)

    dialog.set_plan(plan(), "metadata")
    assert upload.key in dialog._items
    assert dialog._items[upload.key].checkState(0) == Qt.CheckState.Unchecked
    assert dialog._items[download.key].checkState(0) == Qt.CheckState.Unchecked

    # Unticking one row and ticking another survives as well.
    dialog._items[upload.key].setCheckState(0, Qt.CheckState.Checked)
    dialog.set_plan(plan(), "metadata")
    assert dialog._items[upload.key].checkState(0) == Qt.CheckState.Checked


def test_a_row_that_becomes_matched_leaves_the_selection():
    locals_ = [LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",))]
    remotes = [remote("scan", "scan001.epub")]
    dialog = ReviewDialog(build_reconcile_plan(locals_, remotes))
    download = next(entry for entry in dialog.plan.entries if entry.status == "download")
    assert download in dialog.selected_entries()

    dialog.set_plan(
        build_reconcile_plan(locals_, remotes, metadata={"scan": ("The Book", "Jane Doe")}),
        "metadata",
    )
    assert dialog.selected_entries() == []
    assert not dialog.apply_button.isEnabled()


def test_metadata_phase_reports_the_running_match_count():
    locals_ = [LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",))]
    remotes = [remote("a", "scan001.epub"), remote("b", "scan002.epub")]
    dialog = ReviewDialog()
    dialog.start_scanning()

    dialog.set_plan(build_reconcile_plan(locals_, remotes), "metadata")
    assert "Reading book metadata… 0 matched so far." in dialog.status.text()

    dialog.set_plan(
        build_reconcile_plan(locals_, remotes, metadata={"a": ("The Book", "Jane Doe")}),
        "metadata",
    )
    assert "1 matched so far." in dialog.status.text()


def test_cancelled_and_failed_states():
    dialog = ReviewDialog(plan())
    dialog.set_cancelled("OneDrive matching cancelled.")
    assert dialog.status.text() == "OneDrive matching cancelled."
    assert not dialog.progress.isVisible()

    dialog.set_failed("Could not compare the library with OneDrive: boom")
    assert "boom" in dialog.status.text()


def test_status_labels_and_stable_keys():
    assert _status_label("checking") == "Checking…"
    assert _status_label("matched") == "Already linked"
    assert _status_label("unknown") == "unknown"
    book = LocalBook(book_id=7, title="T")
    assert _stable_key(plan().entries[0]).startswith(("book:", "remote:"))
    assert (
        _stable_key(build_reconcile_plan([], [remote("x", "x.epub")]).entries[0])
        == "remote:drive:x"
    )
    assert _stable_key(build_pending_plan([book]).entries[0]) == "book:7"
    # A row with neither side keeps its generated key.
    assert _stable_key(ReconcileEntry(key="raw", status="matched")) == "raw"


# -- bulk actions and the index banner ---------------------------------------


def indexed_plan():
    locals_ = [LocalBook(book_id=1, title="The Book", authors="Jane Doe", formats=("epub",))]
    remotes = [remote("m", "whatever.epub")]
    index = BookIndex(
        books=[IndexEntry(book_id=1, name="whatever.epub", item_id="m", drive_id="drive")]
    )
    return build_reconcile_plan(locals_, remotes, index=index)


def test_bulk_buttons_request_their_action():
    for attribute, expected in (
        ("upload_all_button", "upload_all"),
        ("download_all_button", "download_all"),
        ("apply_button", "apply"),
    ):
        dialog = ReviewDialog(plan())
        getattr(dialog, attribute).click()
        assert dialog.action() == expected


def test_enter_applies_the_selection():
    dialog = ReviewDialog(plan())
    dialog.accept()
    assert dialog.action() == "apply"


def test_clear_backup_asks_before_deleting(monkeypatch):
    dialog = ReviewDialog(plan())
    monkeypatch.setattr(
        review_dialog.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    dialog.clear_backup_button.click()
    assert dialog.action() == ""

    monkeypatch.setattr(
        review_dialog.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog.clear_backup_button.click()
    assert dialog.action() == "clear_backup"


def test_bulk_buttons_wait_for_the_scan_to_finish():
    dialog = ReviewDialog(plan())
    assert dialog.upload_all_button.isEnabled()
    assert dialog.clear_backup_button.isEnabled()

    dialog.start_scanning()
    assert not dialog.upload_all_button.isEnabled()
    assert not dialog.download_all_button.isEnabled()
    assert not dialog.clear_backup_button.isEnabled()

    dialog.finish_scan(plan())
    assert dialog.upload_all_button.isEnabled()
    assert dialog.download_all_button.isEnabled()
    assert dialog.clear_backup_button.isEnabled()


def test_index_banner_explains_the_matching():
    indexed = ReviewDialog(indexed_plan())
    assert "maps 1 book(s)" in indexed.index_status.text()

    bare = ReviewDialog(plan())
    assert "No calibre index" in bare.index_status.text()
    assert "Upload all" in bare.index_status.text()

    assert ReviewDialog().index_status.text() == ""


# -- bulk actions run inside the open window ---------------------------------


def collect_requests(dialog):
    """Record every ``bulk_requested`` emission (the shim signal is real)."""
    requests: list[str] = []
    dialog.bulk_requested.connect(requests.append)
    return requests


def test_bulk_button_requests_without_closing_the_window():
    dialog = ReviewDialog(plan())
    requests = collect_requests(dialog)

    dialog.download_all_button.click()
    assert requests == ["download_all"]
    assert dialog.action() == "download_all"
    # The window must stay open: a bulk job takes minutes and hides nothing.
    assert not getattr(dialog, "_accepted", False)
    assert not dialog._busy()


def test_a_second_click_is_ignored_while_a_task_runs():
    dialog = ReviewDialog(plan())
    requests = collect_requests(dialog)

    dialog.upload_all_button.click()
    assert requests == ["upload_all"]

    dialog.start_task("upload_all")
    dialog.upload_all_button.click()
    dialog.download_all_button.click()
    # Nothing new is requested and the running action is not replaced.
    assert requests == ["upload_all"]
    assert dialog.action() == "upload_all"


def test_clicks_are_ignored_while_the_scan_runs():
    dialog = ReviewDialog(plan())
    requests = collect_requests(dialog)

    dialog.start_scanning()
    dialog.download_all_button.click()
    assert requests == []
    assert dialog.action() == ""


def test_start_task_disables_the_actions_and_shows_the_speed_status():
    dialog = ReviewDialog(plan())
    assert dialog.apply_button.isEnabled()

    dialog.start_task("upload_all")
    assert dialog._task == "upload_all"
    assert dialog.progress.range() == (0, 100)
    assert dialog.progress.value() == 0
    assert dialog.progress.isVisible()

    status = dialog.status.text()
    assert "Uploading every book to OneDrive" in status
    assert "measuring speed" in status

    assert not dialog.apply_button.isEnabled()
    assert not dialog.upload_all_button.isEnabled()
    assert not dialog.download_all_button.isEnabled()
    assert not dialog.clear_backup_button.isEnabled()


def test_finish_task_restores_the_idle_window():
    dialog = ReviewDialog(plan())
    dialog.start_task("download_all")
    dialog.set_progress(0.5, "Downloading 3/10: The Book")

    dialog.finish_task("Downloaded 3 book(s).")
    assert dialog.status.text() == "Downloaded 3 book(s)."
    assert not dialog.progress.isVisible()
    assert dialog.progress.value() == 100
    assert dialog._task == ""
    assert dialog._progress_message == ""
    assert not dialog._busy()

    assert dialog.apply_button.isEnabled()
    assert dialog.upload_all_button.isEnabled()
    assert dialog.download_all_button.isEnabled()
    assert dialog.clear_backup_button.isEnabled()


def test_set_progress_updates_the_status_while_a_task_runs():
    dialog = ReviewDialog(plan())
    dialog.start_task("upload_all")

    line = "Uploading 2/8: The Book · 4.5 MB/s · 8 min 12 s left"
    dialog.set_progress(0.25, line)
    assert dialog.status.text() == line
    assert dialog.progress.value() == 25


def test_set_progress_leaves_an_idle_status_alone():
    dialog = ReviewDialog(plan())
    before = dialog.status.text()

    line = "Uploading 2/8: The Book · 4.5 MB/s"
    dialog.set_progress(0.25, line)
    assert dialog.status.text() == before
    # The message is remembered for the next plan snapshot, not shown.
    assert dialog._progress_message == line
    assert dialog.progress.value() == 25


def row_entry(book_id=1, title="The Book", status="matched", transfer="1.2 MB/s · 4.2 s"):
    return ReconcileEntry(
        key=f"book:{book_id}",
        status=status,
        local=LocalBook(book_id=book_id, title=title, authors="Author", formats=("epub",)),
        remote=RemoteItem(id="r", name="Author - The Book.epub", drive_id="drive"),
        transfer=transfer,
    )


def test_the_list_has_a_transfer_column():
    dialog = ReviewDialog()
    assert dialog.tree._headers[-1] == "Transfer"


def test_a_row_shows_its_own_speed_and_time():
    """One book's report belongs in the row, the aggregate one in the status line."""
    dialog = ReviewDialog(ReconcilePlan(entries=[row_entry()]))

    item = dialog._items["book:1"]
    assert item.text(5) == "1.2 MB/s · 4.2 s"
    assert item.text(1) == "Already linked"


def test_a_row_that_never_transferred_leaves_the_column_empty():
    dialog = ReviewDialog(ReconcilePlan(entries=[row_entry(status="upload", transfer="")]))

    assert dialog._items["book:1"].text(5) == ""


def test_a_deleted_row_says_deleted():
    dialog = ReviewDialog(ReconcilePlan(entries=[row_entry(status="deleted", transfer="")]))

    item = dialog._items["book:1"]
    assert item.text(1) == "Deleted from OneDrive"
    # The row is history: it is not offered to a pass any more.
    assert item.flags() == Qt.ItemFlag.ItemIsEnabled


def test_a_transfer_snapshot_keeps_the_live_progress_line():
    """A row update during a transfer must not overwrite the speed line."""
    dialog = ReviewDialog(plan())
    dialog.start_task("upload_all")
    line = "Uploading 2/8: The Book · 4.5 MB/s · 8 min 12 s left"
    dialog.set_progress(0.25, line)

    dialog.set_plan(plan(), phase="transfer")

    assert dialog.status.text() == line


def test_a_transfer_snapshot_without_a_line_counts_the_finished_rows():
    dialog = ReviewDialog(plan())
    dialog.start_task("upload_all")
    dialog._progress_message = ""

    dialog.set_plan(plan(), phase="transfer")

    assert dialog.status.text().startswith("Transferring…")


def test_a_transfer_snapshot_keeps_the_users_ticks():
    dialog = ReviewDialog(plan())
    dialog._set_all(False)
    updated = [
        replace(entry, status="matched", transfer="1 MB/s") if entry.status == "download" else entry
        for entry in dialog.plan.entries
    ]

    dialog.set_plan(ReconcilePlan(entries=updated), phase="transfer")

    # The uploaded row lost its checkbox, and the rows that remain are still
    # unticked: a live update never silently re-checks the user's list.
    remaining = [entry for entry in dialog.plan.entries if entry.selectable]
    assert remaining
    states = [dialog._items[entry.key].checkState(0) for entry in remaining]
    assert all(state == Qt.CheckState.Unchecked for state in states)
