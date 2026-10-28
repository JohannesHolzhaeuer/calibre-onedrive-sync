# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## Project overview

`calibre-onedrive-sync` is a **calibre 9** plugin that syncs a calibre library
with a folder in OneDrive. It talks to **Microsoft Graph** with OAuth 2.0's
**device-code flow**. The local OneDrive desktop client is not required; the
selected folder must be in the user's own OneDrive (`/me/drive`).

A calibre zip registers exactly **one** `Plugin` subclass, so the zip declares a
single **interface action** (`OneDriveSyncAction` in `src/__init__.py`).

Hard product constraints:

- **No password handling, no cloud SDK.** Authentication is delegated to
  Microsoft's device-code flow. Only a refresh token is persisted; the
  short-lived access token stays in memory. Never add `requests`, MSAL, or any
  other third-party runtime dependency.
- **Graph access is folder-scoped by behaviour.** The app requests delegated
  `Files.ReadWrite.All`, but the plugin must only touch the folder the user
  selected — and only its **direct children**; subfolders are never entered.
- **Everything is manual.** No scheduler, startup sync, DB listener or timer may
  be reintroduced. Uploads, downloads, scans and clean-ups only run from an
  explicit toolbar click or a button in the review window.
- **Runtime dependencies are calibre-bundled only.** `pytest`, `pytest-cov` and
  `ruff` are dev/CI-only and must never be imported by plugin runtime code.
- **Never sync a library into itself.** The plugin works remotely, but keep the
  invariant that the target is a dedicated OneDrive folder, never the calibre
  library folder.

## Repository layout

```
.
├── pyproject.toml                 # pytest/coverage + ruff + packaging config (version source of truth)
├── package.json                   # dev-only commitlint tooling
├── commitlint.config.ts           # Conventional Commits rules
├── build.py                       # builds/validates dist/onedrive-sync.zip (stdlib only)
├── build/sync_version.py          # mirror pyproject version into src/common/version.py
├── src/                           # plugin source; its CONTENTS become the zip
│   ├── __init__.py                # OneDriveSyncAction (InterfaceActionBase) — must sit at zip ROOT
│   ├── plugin-import-name-onedrive_sync.txt   # calibre import-namespace marker
│   ├── common/                    # calibre-independent, unit-testable logic
│   │   ├── auth.py                # access-token cache + refresh-token store
│   │   ├── config.py              # persistent settings + token storage
│   │   ├── defaults.py            # shared public app ID
│   │   ├── errors.py              # typed plugin errors
│   │   ├── graph.py               # Microsoft Graph client (drives, children, upload/download)
│   │   ├── http.py                # urllib transport with test seams
│   │   ├── i18n.py                # shared `_` translation hook
│   │   ├── index.py               # calibre-onedrive-index.json format + builder
│   │   ├── log.py                 # logger factory
│   │   ├── models.py              # dataclasses shared by the layers
│   │   ├── oauth.py               # device-code + refresh flows
│   │   ├── reconcile.py           # local/remote book matching for the review UI
│   │   ├── state.py               # SQLite import/export/book-link state
│   │   ├── sync.py                # flat import/export/scan engine (network injected)
│   │   └── version.py             # runtime version mirror (release-please-managed)
│   ├── gui/                       # calibre/Qt layer (guarded imports)
│   │   ├── action.py              # InterfaceAction wiring
│   │   ├── auth_dialog.py         # device-code sign-in dialog
│   │   ├── config_widget.py       # configuration dialog
│   │   ├── folder_picker.py       # Graph-backed OneDrive folder browser
│   │   ├── library.py             # calibre DB adapter + add-book/identifier helper
│   │   ├── review_dialog.py       # matching window (bulk actions + row upload/download/delete)
│   │   └── sync_job.py            # ThreadedJob (index/scan/apply/task) + GUI-thread marshalling
│   └── resources/images/          # toolbar icon
├── tests/                         # pytest suite (imports the package from src/)
└── .github/                       # workflows, release-please, dependabot
```

## Golden rules

1. **Keep testable logic out of the Qt/calibre layer.** Anything unit-testable
   belongs in `src/common/`. `src/gui/` uses guarded imports so the package
   stays importable without calibre/Qt installed (see `tests/*_shims.py`).
2. **Do not hand-edit the version.** `pyproject.toml` (`[project].version`) is
   the release-please source of truth; `src/common/version.py` is the runtime
   mirror, kept in sync by `build/sync_version.py` and by release-please via the
   `# x-release-please-version` marker. Run
   `python build/sync_version.py --check` if unsure.
3. **The plugin `__init__.py` must remain at the zip root.** `build.py` zips the
   *contents* of `src/` so calibre resolves imports as
   `calibre_plugins.onedrive_sync.*`. Zipping the `src/` folder itself breaks
   the plugin.
4. **Only one `Plugin` subclass may be declared** in `src/__init__.py`.
5. **Runtime deps = calibre only.** No third-party imports in `src/`; use the
   standard library and calibre's bundled modules.
6. **Mark user-facing strings with `_()`** at the point of use; never bind the
   result to a module constant at import time.
7. **All network access goes through `common.http`.** Keep the injectable
   `transport` / `downloader` / `_open` seams so tests never touch Microsoft; do
   not call `urllib` directly from the engine or GUI. Every transport failure
   (timeout, reset, refused connection) is normalised into `HttpError(status=0)`
   so the OAuth and Graph layers can retry it. The redirect handler there restores
   the bearer token for Microsoft storage hosts, because OneDrive answers
   `/content` with a `302` to `*.files.1drv.com` and Python drops the header
   cross-host; redirects outside `REDIRECT_TRUSTED_SUFFIXES` lose it.
8. **GUI-thread rule.** Long sync work runs in a `ThreadedJob`. Adding books to
   the calibre DB must happen on the GUI thread via
   `MainThreadBookAdder`; never call the calibre API from the worker.
9. **Jobs stay cancellable.** Every job is created with `killable=True` and the
   engine checks `should_abort` between files/books. calibre marks a killed job
   `failed=True` without an exception and skips the callback for a job killed
   while still queued, so the action treats `job.killed`/`abort.is_set()` as a
   cancellation and releases dead job references lazily. Never report a
   cancellation as a sync failure.

## How to build

```bash
python build.py            # creates/validates dist/onedrive-sync.zip
python build.py --install  # ... and installs it into calibre
```

`build.py` asserts the zip ROOT holds `__init__.py` + the import-name marker,
and that no `src/` prefix or bytecode leaks into the archive. Install manually
via *Preferences → Plugins → Load plugin from file*.

## How to test & lint

```bash
pip install -e ".[dev]"
ruff check src tests build build.py
ruff format --check src tests build build.py
pytest
pytest --cov --cov-report=term-missing   # optional: coverage locally
```

`tests/conftest.py` registers `src/` as the `onedrive_sync` package and injects
headless calibre/Qt stand-ins so the GUI modules can be imported and tested
without calibre installed. CI additionally uploads `coverage.xml` to Codecov.

Coverage is a merge gate: `[tool.coverage.report].fail_under = 90` makes
`pytest --cov` fail below 90 %, and `codecov.yml` requires no decrease vs. the
base branch (project) plus coverage of new lines (patch).

## Commit & release conventions

- Commits and PR titles **must** follow
  [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
  `chore:`, `ci:`, `docs:`, `refactor:`, `test:`…), enforced by commitlint.
  Run locally with `npm ci && npx commitlint --from main --to HEAD`.
- One feature per PR, **squash-merge only** into `main`; never push to `main`.
- `feat:` → minor bump, `fix:` → patch bump, `feat!:`/`BREAKING CHANGE:` →
  major.
- Every PR/push runs `ci.yml`: ruff lint/format, `pytest` with coverage uploaded
  to Codecov, `build/sync_version.py --check`, and `python build.py` with
  ROOT-level artifact validation.
- Merging the release-please PR into `main` creates a **draft** release;
  `release-artifact.yml` uploads the built plugin zip to the draft and then
  publishes it (releases are immutable, so assets cannot be added later).

## Authentication and sync design

- A shared public Entra app ID is bundled as `DEFAULT_CLIENT_ID` in
  `common/defaults.py`; users may override it with their own public client. The
  shared app is personal-account-only and uses the `consumers` authority;
  custom app IDs use `common` and must support personal + organizational
  accounts. Never add a client secret.
- Scopes: `offline_access Files.ReadWrite.All User.Read`. `offline_access`
  yields the refresh token; the access token is cached in memory only.
- `AuthManager` refreshes on expiry and retries one Graph request after a 401.
  `ConfigTokenStore` persists the refresh token (keyring if available, otherwise
  calibre JSON config).
- The folder picker uses a lazily loaded tree for `/me/drive` only; shared drives
  and drive shortcuts are not listed. It is opened **only** when no folder is
  stored yet (setup flow) or when a Graph 404 shows the stored folder is gone;
  the picker is then prefilled through :class:`FolderPickerDialog`'s ``initial``
  argument (the "Current folder: …" top-level entry). A configured folder is used
  as-is. Keep all later requests scoped to the chosen `drive_id`/`folder_id`.
- Exports always go directly into the selected folder. There is no export
  subfolder: `SyncEngine._ensure_export_folder` resolves the selected folder with
  `graph.get_item` and uploads into it.
- `SyncEngine._list_folder` is the only listing path. It calls
  `graph.list_children` and drops `deleted` items; recursive listing, delta
  links and subfolder traversal must not come back. A folder with no calibre
  index is handled by the action's clear+upload offer, never by guessing.
- Import state stores `(drive_id, item_id)` so remote files are never imported
  twice. Export state stores `(book_id, format)` plus the remote item id so
  updates replace the same file.
- Book links are stored twice: in the `book_links` SQLite table for fast
  matching and in the calibre `identifiers` field under the `onedrive` key (web
  URL, or item id when Graph has no URL). The review window persists matches for
  both, and imports/exports save links automatically.
- Each upload also rewrites `calibre-onedrive-index.json` in the selected folder
  (`common.index`). It maps `(drive_id, item_id)` and the unique filename to the
  calibre book id, so a scan against a folder filled by an earlier export is
  deterministic. A missing, empty or broken index is not an error for the scan
  (it falls back to the per-row heuristics), but `SyncEngine.index_available()`
  reports it as absent so the action can offer a fresh upload. Bulk actions and
  `apply_plan` refresh the index.
- `common.reconcile` builds the review plan. Matching precedence is: an explicit
  link (state or `identifiers["onedrive"]`), then the calibre index (item id,
  then a unique casefolded filename), then filename/title evidence. EPUB/PDF
  metadata read during the scan resolves files with unusual names; one book is
  assigned at most one remote item, and further copies are offered for deletion.
- The toolbar click flow in `gui/action.py` is: `_settings_for` →
  `_prepare_engine` → `_ensure_folder` (the stored folder is used as-is;
  `_pick_folder` → `_remember_folder` only when none is stored) → `_open_window`
  (the review window is shown **first**, so every later step is visible) →
  `_check_index` (`start_index_job` runs `SyncEngine.index_available()` off the
  GUI thread) → `_on_index_checked`. An index starts the scan into the open
  window; otherwise `_offer_fresh_upload` asks once, parented to that window, and
  on *Yes* sets `_pending_task = "clear_backup"` plus `_next_task = "upload_all"`
  and closes the window — `_finish_review` then starts the chain. `_on_task_done`
  keeps the engine/state alive and chains the follow-up only when the first task
  neither failed nor was cancelled. A Graph 404 during the check means the stored
  folder is gone: `_reselect_folder` asks for a new one and reopens the window.
- Listing is streamed: `GraphClient.list_children(..., on_page=...)` reports every
  Graph page, `SyncEngine.scan` turns each page into a `listing` plan snapshot and
  raises `CancelledError` from the page callback, so closing the window stops a
  scan of a huge folder. `SyncEngine._load_index_by_path` reads the index file
  through `graph.download_path` in one request — a folder with thousands of files
  is never listed just to find it.
- The review window's bulk buttons map to engine methods through `TaskJob`:
  `upload_all` (uploads missing books, links existing files, writes the index),
  `download_all` (imports every not-yet-imported ebook and deliberately keeps
  duplicates for calibre's Find Duplicates plugin), and `clear_backup` (deletes
  every direct child after the dialog's confirmation, then
  `state.forget_drive` and the action clears the calibre `onedrive`
  identifiers). `forget_drive` is only called when nothing failed or was
  cancelled.
- The scan job streams plan snapshots (`SyncEngine.on_plan`, phases `library`,
  `onedrive`, `metadata`, `final`) and progress lines (`SyncEngine.on_progress`,
  forwarded by `ScanJob`) into the open window while the worker runs. Closing the
  window aborts the scan (`job.abort`). There is no other entry point into the
  engine.
- `config_widget.py` only exposes the client ID, sign-in, folder, export format
  and debug toggle. The remaining `Settings` fields (`export_subfolder`,
  `export_directly`, `export_enabled`, `import_enabled`, `auto_sync`,
  `sync_interval_minutes`) are legacy compatibility only — do not wire them back
  into behaviour.

## When adding a feature

1. Put pure logic in `src/common/` and add tests in `tests/`; inject network via
   `common.http` and calibre via the `Library` protocol in `common.sync`.
2. Wire any UI into `src/gui/` behind guarded imports; never do blocking file
   I/O on the GUI thread (use calibre jobs for long syncs).
3. Add user-facing strings via `_()`.
4. Keep runtime code dependency-free (stdlib + calibre only).
5. Use a Conventional Commit message.
