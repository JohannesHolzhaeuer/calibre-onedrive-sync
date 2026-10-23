# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## Project overview

`calibre-onedrive-sync` is a **calibre 9** plugin that syncs a calibre library
with a folder in OneDrive. It uses the **locally synced OneDrive folder** that
the OneDrive desktop client maintains; it does not talk to the Microsoft Graph
API and never handles Microsoft credentials.

A calibre zip registers exactly **one** `Plugin` subclass, so the zip declares a
single **interface action** (`OneDriveSyncAction` in `src/__init__.py`).

Hard product constraints:

- **No cloud API / no OAuth.** The plugin only reads and writes the local
  OneDrive sync folder. OneDrive handles authentication and transfer.
- **Runtime dependencies are calibre-bundled only.** `pytest`, `pytest-cov` and
  `ruff` are dev/CI-only and must never be imported by plugin runtime code.
- **Never sync a library into itself.** Validation rejects overlapping source
  and target folders; keep that invariant when adding the sync engine.

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
│   │   ├── config.py              # persistent settings (JSONConfig)
│   │   ├── i18n.py                # shared `_` translation hook
│   │   ├── log.py                 # logger factory
│   │   ├── paths.py               # path helpers + validation
│   │   └── version.py             # runtime version mirror (release-please-managed)
│   ├── gui/                       # calibre/Qt layer (guarded imports)
│   │   ├── action.py              # InterfaceAction wiring
│   │   └── config_widget.py       # configuration dialog
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

## When adding a feature

1. Put pure logic in `src/common/` and add tests in `tests/`.
2. Wire any UI into `src/gui/` behind guarded imports; never do blocking file
   I/O on the GUI thread (use calibre jobs for long syncs).
3. Add user-facing strings via `_()`.
4. Keep runtime code dependency-free (stdlib + calibre only).
5. Use a Conventional Commit message.
