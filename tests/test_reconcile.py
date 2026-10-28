"""Tests for the GUI-free book/OneDrive matcher."""

from __future__ import annotations

from onedrive_sync.common.index import BookIndex, IndexEntry
from onedrive_sync.common.models import BookLink, RemoteItem
from onedrive_sync.common.reconcile import (
    MATCH_THRESHOLD,
    LocalBook,
    build_pending_plan,
    build_reconcile_plan,
    match_score,
    normalize_text,
    parse_book_filename,
    pick_format,
)


def remote(item_id, name, *, drive="drive", size=0, folder=False, deleted=False, web_url=""):
    return RemoteItem(
        id=item_id,
        name=name,
        drive_id=drive,
        size=size,
        is_folder=folder,
        deleted=deleted,
        web_url=web_url,
    )


def book(book_id, title, authors="", formats=("epub",), identifiers=None):
    return LocalBook(
        book_id=book_id,
        title=title,
        authors=authors,
        formats=tuple(formats),
        identifiers=dict(identifiers or {}),
    )


def test_normalize_text_strips_accents_case_and_punctuation():
    assert normalize_text("  Café  –  Vol. 1! ") == "cafe vol 1"
    assert normalize_text(None) == ""


def test_parse_book_filename():
    book_id, variants = parse_book_filename("Jane Doe - The Book - Part Two (42).epub")
    assert book_id == 42
    assert ("Jane Doe", "The Book - Part Two") in variants
    assert ("Jane Doe - The Book", "Part Two") in variants

    book_id, variants = parse_book_filename("The Book.pdf")
    assert book_id is None
    assert variants == [("", "The Book")]

    assert parse_book_filename("") == (None, [("", "")])


def test_match_score_uses_book_id_and_metadata():
    local = book(7, "The Book", "Jane Doe")
    assert match_score(remote("a", "Jane Doe - The Book (7).epub"), local) == 100
    assert match_score(remote("a", "Jane Doe - The Book.epub"), local) >= 95
    assert match_score(remote("a", "The Book.epub"), local) >= 85
    assert match_score(remote("a", "Completely Different.epub"), local) < MATCH_THRESHOLD

    metadata = {"scan": ("The Book", "Jane Doe")}
    assert match_score(remote("scan", "scan001.epub"), local, metadata) >= 95


def test_match_score_does_not_guess_short_titles_without_author():
    local = book(1, "It")
    assert match_score(remote("a", "It.epub"), local) == 0
    assert match_score(remote("a", "Stephen King - It.epub"), local) == 0
    king = book(1, "It", "Stephen King")
    assert match_score(remote("a", "Stephen King - It.epub"), king) >= 95


def test_match_score_accepts_a_near_identical_title():
    local = book(1, "The Quick Brown Fox", "Jane Doe")
    # One substituted letter is still >= 0.94 similar, so the title alone is
    # strong evidence once the author appears in the file name.
    assert match_score(remote("a", "Jane Doe - The Quick Brown Fax.epub"), local) == 88
    # Without author evidence the same similarity only scores 72.
    assert match_score(remote("a", "The Quick Brown Fax.epub"), local) == 72


def test_match_score_accepts_a_slightly_off_title_from_the_author():
    local = book(3, "A Tale of Two Cities", "Charles Dickens")
    assert match_score(remote("a", "Charles Dickens - A Tale of Two Citys.epub"), local) == 84


def test_match_score_ignores_a_file_name_without_a_title():
    # ``(9).epub`` carries a calibre id but no author/title part at all.
    local = book(1, "The Book", "Jane Doe")
    assert match_score(remote("a", "(9).epub"), local) == 0


def test_match_score_ignores_a_book_without_a_title():
    assert match_score(remote("a", "Anything.epub"), book(1, "")) == 0


def test_preferred_format_prefers_epub_then_pdf():
    assert book(1, "Book", formats=("epub", "pdf")).preferred_format == "epub"
    assert book(1, "Book", formats=("mobi", "pdf")).preferred_format == "pdf"
    assert book(1, "Book", formats=("mobi",)).preferred_format is None


def test_pick_format():
    assert pick_format(["EPUB", "pdf"], "epub_or_pdf") == "epub"
    assert pick_format(["PDF"], "epub_or_pdf") == "pdf"
    assert pick_format(["MOBI"], "epub_or_pdf") is None
    assert pick_format(["EPUB"], "pdf") is None
    assert pick_format([], "epub") is None


def test_plan_assigns_upload_download_and_local_only():
    locals_ = [
        book(1, "Uploaded", "A", formats=("epub",)),
        book(2, "Broken", "B", formats=("mobi",)),
        book(3, "Already", "C"),
    ]
    remotes = [
        remote("r3", "C - Already.epub"),
        remote("r4", "Unknown - Elsewhere.epub"),
    ]
    plan = build_reconcile_plan(locals_, remotes)
    by_status = {entry.status: entry for entry in plan.entries}
    assert by_status["upload"].local.book_id == 1
    assert by_status["local_only"].local.book_id == 2
    assert by_status["matched"].local.book_id == 3
    assert by_status["download"].remote.id == "r4"
    assert plan.counts() == {
        "upload": 1,
        "local_only": 1,
        "matched": 1,
        "download": 1,
    }
    assert "1 already matched" in plan.summary()


def test_plan_matches_a_non_epub_pdf_file():
    # A MOBI-only book and file are still a match, the format only affects the
    # ranking when several files could belong to the same book.
    locals_ = [book(1, "The Book", "Jane Doe", formats=("mobi",))]
    plan = build_reconcile_plan(locals_, [remote("m", "Jane Doe - The Book.mobi")])
    assert [entry.status for entry in plan.entries] == ["matched"]
    assert plan.entries[0].remote.id == "m"


def test_plan_marks_second_copy_as_duplicate():
    locals_ = [book(1, "The Book", "Jane Doe")]
    remotes = [
        remote("a", "Jane Doe - The Book.epub"),
        remote("b", "Jane Doe - The Book.pdf"),
    ]
    plan = build_reconcile_plan(locals_, remotes)
    matched = [entry for entry in plan.entries if entry.status == "matched"]
    duplicates = [entry for entry in plan.entries if entry.status == "duplicate"]
    assert len(matched) == 1
    assert matched[0].remote.id == "a"  # EPUB is preferred
    assert len(duplicates) == 1
    assert duplicates[0].remote.id == "b"
    assert duplicates[0].can_delete
    assert not duplicates[0].default_selected
    assert duplicates[0].selectable


def test_plan_honours_pdf_preference():
    locals_ = [book(1, "The Book", "Jane Doe", formats=("epub", "pdf"))]
    remotes = [
        remote("epub", "Jane Doe - The Book.epub"),
        remote("pdf", "Jane Doe - The Book.pdf"),
    ]
    plan = build_reconcile_plan(locals_, remotes, preference="pdf")
    matched = next(entry for entry in plan.entries if entry.status == "matched")
    duplicates = [entry for entry in plan.entries if entry.status == "duplicate"]
    assert matched.remote.id == "pdf"
    assert duplicates[0].remote.id == "epub"


def test_plan_uses_saved_links_from_state_and_metadata():
    locals_ = [
        book(1, "First", "A", identifiers={"onedrive": "item-1"}),
        book(2, "Second", "B", identifiers={"onedrive": "https://onedrive.test/second"}),
    ]
    remotes = [
        remote("item-1", "anything.epub"),
        remote("item-2", "whatever.epub", web_url="https://onedrive.test/second"),
    ]
    plan = build_reconcile_plan(locals_, remotes)
    assert {entry.status for entry in plan.entries} == {"matched"}
    assert all(entry.note == "Linked" for entry in plan.entries)

    # A link stored only in state links an otherwise unknown item.
    plan = build_reconcile_plan(
        [book(1, "First", "A")],
        [remote("item-9", "anything.epub")],
        links={("drive", "item-9"): 1},
    )
    matched = next(entry for entry in plan.entries if entry.status == "matched")
    assert matched.remote.id == "item-9"


def test_plan_ignores_folders_deleted_and_unsupported_files():
    locals_ = [book(1, "Book", "Author")]
    remotes = [
        remote("folder", "Folder", folder=True),
        remote("deleted", "Deleted.epub", deleted=True),
        remote("notes", "notes.xyz"),
    ]
    plan = build_reconcile_plan(locals_, remotes)
    assert [entry.status for entry in plan.entries] == ["upload"]
    assert plan.remote_items == []


def test_plan_does_not_match_ambiguous_titles():
    locals_ = [
        book(1, "Same", "Author One"),
        book(2, "Same", "Author Two"),
    ]
    remotes = [remote("r", "Same.epub")]
    plan = build_reconcile_plan(locals_, remotes)
    assert {entry.status for entry in plan.entries} == {"upload", "download"}


def test_plan_metadata_resolves_unusual_filename():
    locals_ = [book(1, "The Book", "Jane Doe")]
    remotes = [remote("scan", "scan001.epub")]
    plan = build_reconcile_plan(locals_, remotes)
    assert plan.entries[0].status == "download"

    plan = build_reconcile_plan(
        locals_,
        remotes,
        metadata={"scan": ("The Book", "Jane Doe")},
    )
    assert plan.entries[0].status == "matched"


def test_plan_with_link_and_helpers():
    locals_ = [book(1, "Book", "Author"), book(2, "Other", "Author")]
    remotes = [remote("r1", "Book.epub"), remote("r2", "Other.epub")]
    plan = build_reconcile_plan(locals_, remotes)
    linked = plan.with_link(1, remotes[1])
    matched = next(
        entry for entry in linked.entries if entry.status == "matched" and entry.local.book_id == 1
    )
    assert matched.remote.id == "r2"
    assert linked.entry(matched.key) is matched
    assert linked.entry("missing") is None
    assert linked.default_selection()
    assert locals_[0].display() == "Book — Author (#1)"
    assert plan.entries[0].display()


def test_book_link_from_remote():
    item = remote("item", "The Book.EPUB", web_url="https://onedrive.test/item")
    link = BookLink.from_remote(5, item)
    assert link.book_id == 5
    assert link.drive_id == "drive"
    assert link.item_id == "item"
    assert link.web_url == "https://onedrive.test/item"
    assert link.extension == "epub"
    assert link.matches(item)
    assert not link.matches(remote("other", "x.epub"))


def test_build_pending_plan_lists_local_books_as_checking():
    locals_ = [book(2, "Second", "B"), book(1, "First", "A", formats=("epub",))]
    pending = build_pending_plan(locals_, preference="pdf")
    assert [entry.status for entry in pending.entries] == ["checking", "checking"]
    assert [entry.local.book_id for entry in pending.entries] == [1, 2]
    assert all(not entry.selectable for entry in pending.entries)
    assert all(not entry.default_selected for entry in pending.entries)
    assert pending.entries[0].note
    assert pending.preference == "pdf"
    assert "Scanning OneDrive" in pending.summary()
    assert pending.counts() == {"checking": 2}
    assert pending.default_selection() == []


def test_build_pending_plan_adds_the_remote_files_found_so_far():
    locals_ = [book(1, "First", "A")]
    remotes = [
        remote("b", "Second.epub"),
        remote("a", "First.epub"),
        remote("folder", "Subfolder", folder=True),
        remote("gone", "Gone.epub", deleted=True),
        remote("notes", "notes.txt"),
        remote("cover", "cover.jpg"),
    ]
    pending = build_pending_plan(locals_, remotes)
    assert [entry.status for entry in pending.entries] == ["checking"] * 4
    # Local books come first, then remote files sorted by name; folders,
    # deletions and non-book files are not listed.
    assert [entry.local.book_id for entry in pending.entries if entry.local] == [1]
    assert [entry.remote.name for entry in pending.entries if entry.remote] == [
        "First.epub",
        "notes.txt",
        "Second.epub",
    ]
    assert [item.name for item in pending.remote_items] == [
        "First.epub",
        "notes.txt",
        "Second.epub",
    ]
    remote_entry = next(entry for entry in pending.entries if entry.remote is not None)
    # Same key the final plan uses, so the row keeps its identity.
    assert remote_entry.key == "remote:drive:a"
    assert not remote_entry.selectable


# -- calibre index matching --------------------------------------------------


def index_entry(book_id, name, item_id, drive="drive"):
    return IndexEntry(book_id=book_id, name=name, item_id=item_id, drive_id=drive)


def test_index_wins_over_a_misleading_filename():
    locals_ = [book(1, "Alpha", "A"), book(2, "Beta", "B")]
    remotes = [remote("i1", "Alpha - A.epub")]
    index = BookIndex(books=[index_entry(2, "whatever.epub", "i1")])

    plan = build_reconcile_plan(locals_, remotes, index=index)

    matched = [entry for entry in plan.entries if entry.status == "matched"]
    assert len(matched) == 1
    assert matched[0].local.book_id == 2
    assert "index" in matched[0].note.lower()
    assert plan.index_present is True
    assert plan.index_books == 1
    # Book 1 has no file left, so it is offered for upload.
    uploads = [entry for entry in plan.entries if entry.status == "upload"]
    assert [entry.local.book_id for entry in uploads] == [1]


def test_index_matches_a_replaced_file_by_its_unique_name():
    locals_ = [book(1, "The Book", "Jane Doe")]
    remotes = [remote("new-id", "The Book - Jane Doe.epub")]
    index = BookIndex(books=[index_entry(1, "The Book - Jane Doe.epub", "old-id")])

    plan = build_reconcile_plan(locals_, remotes, index=index)

    assert {entry.status for entry in plan.entries} == {"matched"}
    assert plan.entries[0].remote.id == "new-id"
    assert "index" in plan.entries[0].note.lower()


def test_index_does_not_override_an_explicit_link():
    locals_ = [book(1, "Alpha", "A"), book(2, "Beta", "B")]
    remotes = [remote("i1", "Alpha - A.epub")]
    index = BookIndex(books=[index_entry(2, "Alpha - A.epub", "i1")])

    plan = build_reconcile_plan(locals_, remotes, links={("drive", "i1"): 1}, index=index)

    matched = [entry for entry in plan.entries if entry.status == "matched"]
    assert matched[0].local.book_id == 1
    assert matched[0].note == "Linked"


def test_unindexed_files_still_use_the_filename_heuristics():
    locals_ = [book(1, "First", "A"), book(2, "Second", "B")]
    remotes = [
        remote("i1", "unknown-name.epub"),
        remote("i2", "B - Second (2).epub"),
    ]
    index = BookIndex(books=[index_entry(1, "unknown-name.epub", "i1")])

    plan = build_reconcile_plan(locals_, remotes, index=index)

    notes = {entry.local.book_id: entry.note for entry in plan.entries if entry.status == "matched"}
    assert notes[1] == "Matched from the calibre index"
    assert notes[2] == "Matched automatically"


def test_plan_without_index_reports_it():
    plan = build_reconcile_plan([book(1, "A", "B")], [remote("a", "a.epub")])
    assert plan.index is None
    assert plan.index_present is False
    assert plan.index_books == 0


def test_an_empty_index_counts_as_missing():
    plan = build_reconcile_plan([], [], index=BookIndex())
    assert plan.index_present is False
    assert plan.index_books == 0


def test_with_link_keeps_the_index():
    locals_ = [book(1, "A", "B")]
    remotes = [remote("r1", "r1.epub"), remote("r2", "r2.epub")]
    index = BookIndex(books=[index_entry(1, "r1.epub", "r1")])
    plan = build_reconcile_plan(locals_, remotes, index=index)

    rebuilt = plan.with_link(1, remotes[1])

    assert rebuilt.index is index
    assert rebuilt.index_present is True
    matched = [entry for entry in rebuilt.entries if entry.status == "matched"]
    assert matched[0].remote.id == "r2"
