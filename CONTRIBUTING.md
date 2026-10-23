# Contributing

Thanks for your interest in `calibre-onedrive-sync`!

## Development model

- **Trunk-based, one feature per PR.** Every change ships as its own pull
  request and is squash-merged into `main`.
- **Conventional Commits** (https://www.conventionalcommits.org/): `feat:`,
  `fix:`, `chore:`, `ci:`, `docs:`, `refactor:`, `test:`. `feat:` → minor bump,
  `fix:` → patch bump, `feat!:`/`BREAKING CHANGE:` → major.
- Never push directly to `main`; branch protection requires a PR and passing
  checks.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/` | All plugin source (contents become the zip). |
| `src/common/` | Calibre-independent, unit-testable logic. |
| `src/gui/` | Calibre/Qt UI (guarded imports). |
| `tests/` | pytest suite (imports from `src/`). |
| `dist/` | Build output only — **gitignored, never committed**. |

## Workflow

1. Create a feature branch off `main`.
2. Keep testable logic in `src/common/`. Anything that can be unit-tested
   belongs there, not in the Qt/calibre layer.
3. GUI code uses guarded imports so `src/` stays importable without calibre/Qt
   (the shims in `tests/` make this possible).
4. Add/update tests for any new logic, and update `README.md` /
   `AGENTS.md` when user-visible behaviour or the layout changes.
5. Run the checks before pushing:

   ```bash
   pip install -e ".[dev]"
   ruff check src tests build build.py
   ruff format --check src tests build build.py
   pytest --cov --cov-report=term-missing
   python build/sync_version.py --check
   python build.py
   ```

6. Open a pull request, keeping it scoped to a single feature.

## Hard product constraints

- **No cloud API and no OAuth.** The plugin only reads and writes the local
  folder maintained by the OneDrive desktop client. Do not add Microsoft Graph
  calls or ask users for account credentials/tokens.
- **Do not sync a library into itself.** The validation in
  `src/common/paths.py` rejects overlapping source/target folders; keep that
  guarantee when implementing the sync engine.
- **Runtime dependencies are calibre-bundled only.** pytest/ruff live only in
  CI/dev and are never imported by plugin runtime code.

## User-facing strings

Mark user-facing strings with the translation hook at the point of use:

```python
from ..common.i18n import _

label = _("Sync now")
```

`common.i18n.activate()` binds the shared `_` to calibre's gettext at plugin
startup; outside calibre it is an identity function. There are no translation
catalogues yet — when German strings are added, the `.po`/`.mo` build wiring
from the sibling `onleihe-import` project can be adopted.

## CI, branch protection & releases

Every change to this repo goes through the automated gates; `dist/` is
**CI-produced and gitignored** — artifacts are never committed.

- **Required checks** on every PR (branch protection):
  1. `lint, test, build` — ruff lint/format, pytest + coverage, version-sync
     check, plugin build with ROOT-level artifact validation.
  2. `Conventional Commits` — the PR title and every commit.
- **Releases**: merging the release-please PR into `main` tags a **draft**
  release (version bump + CHANGELOG). `release-artifact.yml` then builds
  `dist/onedrive-sync.zip`, uploads it to the draft, and publishes the release.
  Drafts are required because the repository uses **immutable releases**.
- **Single place the version lives**: `pyproject.toml` (`[project].version`) is
  the release-please source of truth; `src/common/version.py` is the runtime
  mirror. Run `python build/sync_version.py --check` in CI.
- **Coverage is a merge gate.** `fail_under = 90` in `pyproject.toml` is the
  local/CI floor; Codecov enforces `target: auto` with a 0 % threshold for the
  project and patch. Add a `CODECOV_TOKEN` secret for reliable uploads (public
  repositories can also use tokenless uploads).
