"""Match calibre books against files in OneDrive.

The matcher is deliberately conservative: it only links a remote file to a
local book when the evidence is strong (a saved OneDrive link, the calibre book
id in the generated export filename, or a matching title plus author). Everything
else is shown as "download" so the user can link it manually.

This module is GUI-free and network-free; :class:`common.sync.SyncEngine` feeds
it local metadata, remote item metadata and (when available) ebook metadata that
was read from the remote file.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .i18n import _
from .index import BookIndex
from .models import ONEDRIVE_IDENTIFIER, RemoteItem

#: Formats calibre can normally import. Unknown files are ignored.
IMPORT_EXTENSIONS = frozenset(
    {
        "epub",
        "pdf",
        "mobi",
        "azw",
        "azw3",
        "fb2",
        "fbz",
        "lit",
        "lrf",
        "pdb",
        "prc",
        "rb",
        "rtf",
        "snb",
        "tcr",
        "txt",
        "html",
        "htm",
        "docx",
        "odt",
        "cbz",
        "cbr",
        "cb7",
    }
)

MATCH_THRESHOLD = 80

_STATUS_ORDER = {
    "download": 0,
    "upload": 1,
    "duplicate": 2,
    "matched": 3,
    "local_only": 4,
    "checking": 5,
}

_BOOK_ID = re.compile(r"[\(\[]\s*(\d+)\s*[\)\]]\s*$")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_DASH_SPLIT = re.compile(r"\s+-\s+")


def normalize_text(value: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


def parse_book_filename(name: str) -> tuple[int | None, list[tuple[str, str]]]:
    """Split a filename into a calibre id (if present) and author/title pairs.

    The return value is ``(book_id, [(author, title), ...])``. Both plausible
    orders of ``Author - Title`` are returned because arbitrary user filenames
    may not follow the export naming convention.
    """
    stem = os.path.splitext(str(name or ""))[0]
    match = _BOOK_ID.search(stem)
    book_id = int(match.group(1)) if match else None
    core = _BOOK_ID.sub("", stem).strip()
    parts = [part for part in _DASH_SPLIT.split(core) if part.strip()]
    if len(parts) >= 2:
        return book_id, [
            (parts[0], " - ".join(parts[1:])),
            (" - ".join(parts[:-1]), parts[-1]),
        ]
    return book_id, [("", core)]


def _author_tokens(authors: str) -> list[str]:
    tokens = re.split(r"\s*(?:&|,|;|\band\b|\bwith\b)\s*", str(authors or ""))
    return [token for token in (normalize_text(part) for part in tokens) if token]


def _author_matches(remote_author: str, tokens: list[str], stem: str) -> bool:
    """True when the remote author (or any local author) appears in the file."""
    if remote_author:
        return any(
            token == remote_author or token in remote_author or remote_author in token
            for token in tokens
        )
    return any(token in stem for token in tokens)


def _score_titles(
    remote_title: str,
    remote_author: str,
    local_title: str,
    local_tokens: list[str],
    stem: str,
) -> int:
    """Score one remote author/title pair against the local book."""
    if not remote_title:
        return 0
    author_hit = _author_matches(remote_author, local_tokens, stem)
    # Very short titles ("It") are too weak to match without an author.
    if len(local_title) < 4 and not author_hit:
        return 0

    if remote_title == local_title:
        return 98 if author_hit else 90
    if local_title in remote_title or remote_title in local_title:
        return 92 if author_hit else 82

    ratio = SequenceMatcher(None, remote_title, local_title).ratio()
    if ratio >= 0.94:
        return 88 if author_hit else 72
    if ratio >= 0.86 and author_hit:
        return 84
    return 0


def match_score(
    remote: RemoteItem,
    local: LocalBook,
    metadata: dict[str, tuple[str, str]] | None = None,
) -> int:
    """Return a 0-100 confidence that ``remote`` is ``local``.

    ``metadata`` maps remote item ids to ``(title, authors)`` read from the
    ebook file. It supplements (and usually beats) the filename, because many
    OneDrive folders contain files that were not exported by this plugin.
    """
    book_id, variants = parse_book_filename(remote.name)
    if book_id is not None and book_id == local.book_id:
        return 100

    local_title = normalize_text(local.title)
    if not local_title:
        return 0
    tokens = _author_tokens(local.authors)
    stem = normalize_text(os.path.splitext(remote.name)[0])

    candidates = list(variants)
    remote_meta = (metadata or {}).get(remote.id)
    if remote_meta:
        title, authors = remote_meta
        if normalize_text(title):
            candidates.insert(0, (authors, title))

    best = 0
    for author, title in candidates:
        best = max(
            best,
            _score_titles(
                normalize_text(title),
                normalize_text(author),
                local_title,
                tokens,
                stem,
            ),
        )
    return best


def pick_format(formats: tuple[str, ...] | list[str], preference: str) -> str | None:
    """Pick EPUB or PDF according to the configured preference."""
    available = {str(fmt).lower().lstrip(".") for fmt in formats if fmt}
    if preference == "epub":
        return "epub" if "epub" in available else None
    if preference == "pdf":
        return "pdf" if "pdf" in available else None
    if "epub" in available:
        return "epub"
    if "pdf" in available:
        return "pdf"
    return None


@dataclass(frozen=True)
class LocalBook:
    """The local metadata the review needs, side-effect free."""

    book_id: int
    title: str = ""
    authors: str = ""
    formats: tuple[str, ...] = ()
    identifiers: dict[str, str] = field(default_factory=dict)
    size: int = 0
    mtime: float = 0.0

    @property
    def preferred_format(self) -> str | None:
        return pick_format(self.formats, "epub_or_pdf")

    def display(self) -> str:
        title = self.title or f"Book {self.book_id}"
        authors = f" — {self.authors}" if self.authors else ""
        return f"{title}{authors} (#{self.book_id})"


@dataclass(frozen=True)
class ReconcileEntry:
    """One row in the review dialog."""

    key: str
    status: str
    local: LocalBook | None = None
    remote: RemoteItem | None = None
    note: str = ""

    @property
    def selectable(self) -> bool:
        return self.status in {"upload", "download", "duplicate"}

    @property
    def default_selected(self) -> bool:
        # Deleting is destructive, so duplicates are offered but not pre-ticked.
        return self.status in {"upload", "download"}

    @property
    def can_delete(self) -> bool:
        return self.status == "duplicate"

    def display(self) -> str:
        if self.local is not None:
            return self.local.display()
        return self.remote.name if self.remote is not None else ""


@dataclass
class ReconcilePlan:
    """A full comparison of local books and remote files."""

    entries: list[ReconcileEntry]
    local_books: list[LocalBook] = field(default_factory=list)
    remote_items: list[RemoteItem] = field(default_factory=list)
    links: dict[tuple[str, str], int] = field(default_factory=dict)
    preference: str = "epub_or_pdf"
    metadata: dict[str, tuple[str, str]] = field(default_factory=dict)
    index: BookIndex | None = None

    @property
    def index_present(self) -> bool:
        """True when the folder has a usable ``calibre-onedrive-index.json``."""
        return self.index is not None and bool(self.index.books)

    @property
    def index_books(self) -> int:
        """How many books the index maps."""
        return len(self.index.books) if self.index is not None else 0

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for entry in self.entries:
            result[entry.status] = result.get(entry.status, 0) + 1
        return result

    def summary(self) -> str:
        counts = self.counts()
        if counts.get("checking"):
            return _("Scanning OneDrive… {count} item(s) listed so far.").format(
                count=counts["checking"]
            )
        return (
            f"{counts.get('matched', 0)} already matched, "
            f"{counts.get('download', 0)} to download, "
            f"{counts.get('upload', 0)} to upload, "
            f"{counts.get('duplicate', 0)} duplicate(s) to remove."
        )

    def default_selection(self) -> list[ReconcileEntry]:
        return [entry for entry in self.entries if entry.default_selected]

    def entry(self, key: str) -> ReconcileEntry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None

    def with_link(self, book_id: int, remote: RemoteItem) -> ReconcilePlan:
        """Return a plan where ``book_id`` is explicitly linked to ``remote``."""
        links = {
            key: value
            for key, value in self.links.items()
            if key != (remote.drive_id, remote.id) and value != int(book_id)
        }
        links[(remote.drive_id or "", remote.id)] = int(book_id)
        return build_reconcile_plan(
            self.local_books,
            self.remote_items,
            links=links,
            preference=self.preference,
            metadata=self.metadata,
            index=self.index,
        )


def _explicit_links(
    local_books: list[LocalBook],
    remote_items: list[RemoteItem],
    links: dict[tuple[str, str], int],
) -> dict[str, int]:
    """Map remote item id -> book id from state and from calibre identifiers."""
    known = {book.book_id for book in local_books}
    by_url: dict[str, int] = {}
    by_item: dict[str, int] = {}
    for book in local_books:
        value = str((book.identifiers or {}).get(ONEDRIVE_IDENTIFIER) or "").strip()
        if not value:
            continue
        if value.startswith("http"):
            by_url[value] = book.book_id
        else:
            by_item[value] = book.book_id
    for (_drive_id, item_id), book_id in links.items():
        if book_id in known:
            by_item[str(item_id)] = int(book_id)

    resolved: dict[str, int] = {}
    for remote in remote_items:
        book_id = by_item.get(remote.id)
        if book_id is None and remote.web_url:
            book_id = by_url.get(remote.web_url)
        if book_id is not None and book_id in known:
            resolved[remote.id] = book_id
    return resolved


def _index_matches(
    index: BookIndex | None,
    remote_items: list[RemoteItem],
) -> dict[str, int]:
    """Map remote item id -> book id using the uploaded calibre index.

    The item id wins (it survives renames); a unique filename is the fallback
    for files that were replaced by hand and therefore got a new id.
    """
    if index is None:
        return {}
    by_item = index.item_links()
    by_name = index.name_links()
    resolved: dict[str, int] = {}
    for remote in remote_items:
        book_id = by_item.get((remote.drive_id, remote.id))
        if book_id is None:
            book_id = by_name.get(remote.name.casefold())
        if book_id is not None:
            resolved[remote.id] = book_id
    return resolved


def _format_rank(remote: RemoteItem, book: LocalBook, preference: str) -> int:
    """Prefer the format the user configured (and EPUB before PDF)."""
    preferred = pick_format(book.formats, preference)
    if preferred and remote.extension == preferred:
        return 0
    if remote.extension == "epub":
        return 1
    if remote.extension == "pdf":
        return 2
    return 3


def _best_book_for_remote(
    remote: RemoteItem,
    local_books: list[LocalBook],
    metadata: dict[str, tuple[str, str]],
    preference: str,
) -> tuple[int | None, int, list[int]]:
    """Return ``(best_book_id, best_score, tied_book_ids)``."""
    scored = [
        (
            match_score(remote, book, metadata),
            _format_rank(remote, book, preference),
            book.book_id,
        )
        for book in local_books
    ]
    scored = [item for item in scored if item[0] >= MATCH_THRESHOLD]
    if not scored:
        return None, 0, []
    best_score = max(score for score, _rank, _book_id in scored)
    best = [(rank, book_id) for score, rank, book_id in scored if score == best_score]
    best_rank = min(rank for rank, _book_id in best)
    tied = [book_id for rank, book_id in best if rank == best_rank]
    return (tied[0] if len(tied) == 1 else None), best_score, tied


def build_reconcile_plan(
    local_books: list[LocalBook],
    remote_items: list[RemoteItem],
    *,
    links: dict[tuple[str, str], int] | None = None,
    preference: str = "epub_or_pdf",
    metadata: dict[str, tuple[str, str]] | None = None,
    index: BookIndex | None = None,
) -> ReconcilePlan:
    """Compare local and remote books and assign one action per row.

    Matching precedence is: a link the user chose (state or calibre metadata),
    then the uploaded calibre index, then the filename/title heuristics.
    """
    local_books = sorted(local_books, key=lambda book: (book.title.casefold(), book.book_id))
    remotes = [
        item
        for item in remote_items
        if not item.is_folder and not item.deleted and item.extension in IMPORT_EXTENSIONS
    ]
    remotes = sorted(remotes, key=lambda item: item.name.casefold())
    links = dict(links or {})
    metadata = dict(metadata or {})
    explicit = _explicit_links(local_books, remotes, links)
    indexed = _index_matches(index, remotes)

    local_by_id = {book.book_id: book for book in local_books}
    book_to_remote: dict[int, RemoteItem] = {}
    remote_to_book: dict[str, int] = {}

    # Explicit links win: they were either chosen by the user in an earlier
    # review or written into the calibre book metadata.
    for remote in remotes:
        book_id = explicit.get(remote.id)
        if book_id is None or book_id in book_to_remote:
            continue
        book_to_remote[book_id] = remote
        remote_to_book[remote.id] = book_id

    # The index the plugin wrote is next: it names the file for every book it
    # uploaded, so no guessing is needed even when names were changed.
    for remote in remotes:
        if remote.id in remote_to_book:
            continue
        book_id = indexed.get(remote.id)
        if book_id is None or book_id in book_to_remote:
            continue
        book_to_remote[book_id] = remote
        remote_to_book[remote.id] = book_id

    # Greedy automatic matching, strongest evidence first. Each remote file
    # proposes its single best still-unassigned book. Books proposed by several
    # files are resolved in favour of the stronger/format-preferred proposal,
    # while a file whose best book is genuinely ambiguous is left for download.
    assigned_books = set(book_to_remote)
    while True:
        proposals: dict[int, list[tuple[int, int, RemoteItem]]] = {}
        for remote in remotes:
            if remote.id in remote_to_book:
                continue
            scored = [
                (match_score(remote, item, metadata), _format_rank(remote, item, preference), item)
                for item in local_books
                if item.book_id not in assigned_books
            ]
            scored = [entry for entry in scored if entry[0] >= MATCH_THRESHOLD]
            if not scored:
                continue
            best_score = max(score for score, _rank, _book_item in scored)
            best = [entry for entry in scored if entry[0] == best_score]
            best_rank = min(rank for _score, rank, _book_item in best)
            best = [entry for entry in best if entry[1] == best_rank]
            if len(best) != 1:
                continue
            score, rank, item = best[0]
            proposals.setdefault(item.book_id, []).append((score, rank, remote))

        if not proposals:
            break
        for book_id, options in proposals.items():
            # Every key of ``proposals`` was still unassigned when it was built
            # (see the filter above) and is visited once, so each round assigns
            # at least one book: the empty-proposals check above is the only
            # exit this loop needs.
            options.sort(key=lambda option: (-option[0], option[1], option[2].name.casefold()))
            _score, _rank, remote = options[0]
            book_to_remote[book_id] = remote
            remote_to_book[remote.id] = book_id
            assigned_books.add(book_id)

    entries: list[ReconcileEntry] = []
    used_remote_ids: set[str] = set()
    for book in local_books:
        remote = book_to_remote.get(book.book_id)
        has_export_format = pick_format(book.formats, preference) is not None
        if remote is not None:
            used_remote_ids.add(remote.id)
            if remote.id in explicit:
                note = _("Linked")
            elif remote.id in indexed:
                note = _("Matched from the calibre index")
            else:
                note = _("Matched automatically")
            entries.append(
                ReconcileEntry(
                    key=f"matched:{book.book_id}:{remote.id}",
                    status="matched",
                    local=book,
                    remote=remote,
                    note=note,
                )
            )
        else:
            entries.append(
                ReconcileEntry(
                    key=f"upload:{book.book_id}",
                    status="upload" if has_export_format else "local_only",
                    local=book,
                    note="" if has_export_format else _("No EPUB/PDF version"),
                )
            )

    for remote in remotes:
        if remote.id in used_remote_ids:
            continue
        best_book_id, _score, tied = _best_book_for_remote(
            remote, local_books, metadata, preference
        )
        if best_book_id is None and len(tied) > 1:
            best_book_id = next((book_id for book_id in tied if book_id in book_to_remote), None)
        duplicate = best_book_id is not None and best_book_id in book_to_remote
        entries.append(
            ReconcileEntry(
                key=f"remote:{remote.drive_id}:{remote.id}",
                status="duplicate" if duplicate else "download",
                local=local_by_id.get(best_book_id) if best_book_id is not None else None,
                remote=remote,
                note=_("Another copy is already linked") if duplicate else "",
            )
        )

    entries.sort(
        key=lambda entry: (
            _STATUS_ORDER.get(entry.status, 9),
            (entry.local.title if entry.local else entry.remote.name).casefold()
            if (entry.local or entry.remote)
            else "",
            entry.key,
        )
    )
    return ReconcilePlan(
        entries=entries,
        local_books=local_books,
        remote_items=remotes,
        links=links,
        preference=preference,
        metadata=metadata,
        index=index,
    )


def build_pending_plan(
    local_books: list[LocalBook],
    remote_items: Iterable[RemoteItem] = (),
    *,
    links: dict[tuple[str, str], int] | None = None,
    preference: str = "epub_or_pdf",
) -> ReconcilePlan:
    """A plan for the review window while OneDrive is still being scanned.

    Local books are listed immediately, and ``remote_items`` (the files found
    so far) are added as they arrive, all with a neutral ``checking`` status.
    The window can therefore open before the network work and the metadata
    probes finish.
    """
    books = sorted(local_books, key=lambda book: (book.title.casefold(), book.book_id))
    entries = [
        ReconcileEntry(
            key=f"checking:{book.book_id}",
            status="checking",
            local=book,
            note=_("Waiting for OneDrive…"),
        )
        for book in books
    ]
    remotes = sorted(
        (
            item
            for item in remote_items
            if not item.is_folder and not item.deleted and item.extension in IMPORT_EXTENSIONS
        ),
        key=lambda item: (item.name.casefold(), item.id),
    )
    entries.extend(
        ReconcileEntry(
            # Same key the final plan uses, so a row keeps its identity when the
            # scan resolves it to a download or a duplicate.
            key=f"remote:{item.drive_id}:{item.id}",
            status="checking",
            remote=item,
            note=_("Checking…"),
        )
        for item in remotes
    )
    return ReconcilePlan(
        entries=entries,
        local_books=books,
        remote_items=remotes,
        links=dict(links or {}),
        preference=preference,
    )
