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
  selected and the export subfolder beneath it.
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
│   │   ├── errors.py              # typed plugin errors
│   │   ├── graph.py               # Microsoft Graph client (drives, delta, upload/download)
│   │   ├── http.py                # urllib transport with test seams
│   │   ├── i18n.py                # shared `_` translation hook
│   │   ├── log.py                 # logger factory
│   │   ├── models.py              # dataclasses shared by the layers
│   │   ├── oauth.py               # device-code + refresh flows
│   │   ├── state.py               # SQLite import/export/delta state
│   │   ├── sync.py                # import/export engine (network injected)
│   │   └── version.py             # runtime version mirror (release-please-managed)
│   ├── gui/                       # calibre/Qt layer (guarded imports)
│   │   ├── action.py              # InterfaceAction wiring
│   │   ├── auth_dialog.py         # device-code sign-in dialog
│   │   ├── config_widget.py       # configuration dialog
│   │   ├── folder_picker.py       # Graph-backed OneDrive folder browser
│   │   ├── library.py             # calibre DB adapter + add-book helper
│   │   ├── scheduler.py           # DB listener + debounce + timer
│   │   └── sync_job.py            # ThreadedJob + GUI-thread add-book marshalling
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
   `transport` / `downloader` seams so tests never touch Microsoft; do not call
   `urllib` directly from the engine or GUI.
8. **GUI-thread rule.** Long sync work runs in a `ThreadedJob`. Adding books to
   the calibre DB must happen on the GUI thread via
   `MainThreadBookAdder`; never call the calibre API from the worker.

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

- The user supplies a Microsoft Entra **Application (client) ID** in the
  configuration dialog; there is no bundled app registration. The tenant is
  `common` so personal and work/school accounts both work.
- Scopes: `offline_access Files.ReadWrite.All User.Read`. `offline_access`
  yields the refresh token; the access token is cached in memory only.
- `AuthManager` refreshes on expiry and retries one Graph request after a 401.
  `ConfigTokenStore` persists the refresh token (keyring if available, otherwise
  calibre JSON config).
- The folder picker uses `/me/drive` only; shared drives and drive shortcuts are
  not listed. Keep all later requests scoped to the chosen `drive_id`/`folder_id`.
- Import state stores `(drive_id, item_id)` so remote files are never imported
  twice. Export state stores `(book_id, format)` plus the remote item id so
  updates replace the same file. Delta links are stored per drive/folder.
- Scheduling combines the calibre DB listener, a `QFileSystemWatcher` fallback,
  a debounce timer, and a periodic timer. The scheduler is suspended while a
  sync runs to avoid feedback loops.

## When adding a feature

1. Put pure logic in `src/common/` and add tests in `tests/`; inject network via
   `common.http` and calibre via the `Library` protocol in `common.sync`.
2. Wire any UI into `src/gui/` behind guarded imports; never do blocking file
   I/O on the GUI thread (use calibre jobs for long syncs).
3. Add user-facing strings via `_()`.
4. Keep runtime code dependency-free (stdlib + calibre only).
5. Use a Conventional Commit message.
