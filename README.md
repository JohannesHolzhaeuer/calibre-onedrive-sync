# calibre-onedrive-sync

A [calibre 9](https://calibre-ebook.com/) plugin that keeps a calibre library
in sync with a folder in **OneDrive**.

The plugin works with the **locally synced OneDrive folder** maintained by the
OneDrive desktop client. It does not call the Microsoft Graph API and never
asks for a Microsoft account or token — OneDrive itself handles authentication
and cloud transfer.

> **Status:** repository/CI/release scaffolding and the plugin's configuration
> + toolbar action are in place. The copy/mirror engine itself lands in a
> follow-up change. *Sync now* validates the configuration and says so.

## What is included

- A single calibre **interface action** (`OneDrive Sync`) with a toolbar menu:
  *Sync now* and *Open configuration…*.
- A configuration dialog for the calibre library folder, the local OneDrive
  folder, the destination subfolder, the sync mode, and metadata/debug options.
- Pure, unit-tested validation that rejects unsafe layouts (for example a
  OneDrive target inside the calibre library).
- Build tooling that produces a calibre-installable zip with `__init__.py` at
  the archive ROOT.
- GitHub Actions for lint/format/test/build, Conventional Commits, release
  automation, and release assets.
- release-please with a single version source (`pyproject.toml`) mirrored into
  `src/common/version.py`.

## Requirements

- calibre **9.0** or newer (Qt6 / Python 3).
- For development: Python **3.11+** plus the dev extras
  (`pip install -e ".[dev]"`).

## Project structure

| Path | Purpose |
| --- | --- |
| `src/` | All plugin source. The **contents** of this folder become the zip. |
| `src/__init__.py` | Declares the `OneDriveSyncAction` plugin class (`InterfaceActionBase`). Must sit at the zip root. |
| `src/common/` | Calibre-independent logic (settings, paths, validation, i18n, logging). |
| `src/gui/` | Interface action + configuration widget (guarded Qt imports). |
| `src/resources/` | Icons. |
| `tests/` | pytest suite (imports the package from `src/`). |
| `dist/` | Build output — `dist/onedrive-sync.zip` (gitignored). |
| `build.py` | Zips the contents of `src/` into `dist/onedrive-sync.zip`. |
| `build/` | Build tooling (`sync_version.py`). |

## Build & install

```bash
python build.py            # creates dist/onedrive-sync.zip
python build.py --install  # ... and installs it into calibre
```

Install the zip manually via *calibre → Preferences → Plugins → Load plugin
from file*.

## Configuration

Open *Preferences → Plugins → Customize plugin → OneDrive Sync*, or use the
toolbar menu's *Open configuration…*:

| Field | Meaning |
| --- | --- |
| **Calibre library folder** | The folder that contains `metadata.db`. Defaults to the currently open library. |
| **OneDrive folder** | The local folder the OneDrive desktop client syncs (auto-detected from `OneDrive`, `OneDriveCommercial` or `OneDriveConsumer`). |
| **Folder inside OneDrive** | Destination subfolder, default `calibre-library`. Must stay inside the OneDrive folder. |
| **Sync mode** | *Backup* copies new/changed files only. *Mirror* also removes files that no longer exist in the library. |
| **Delete files …** | Only available in *Mirror* mode. |
| **Include calibre metadata.db** | Whether the database file is part of the sync. |
| **Debug** | Verbose logging via calibre's log. |

After installing, add the **OneDrive Sync** button to a toolbar via
*Preferences → Toolbars & menus* (main toolbar).

## Development

```bash
pip install -e ".[dev]"
ruff check src tests build build.py
ruff format --check src tests build build.py
pytest --cov --cov-report=term-missing
python build/sync_version.py --check
python build.py
```

`tests/conftest.py` puts `src/` on `sys.path` and registers the tree as the
`onedrive_sync` package, so tests import the exact code path calibre uses.
`tests/calibre_shims.py` and `tests/qt_shims.py` provide headless stand-ins so
the GUI modules can be imported without calibre or PyQt installed.

## CI, branch rules & releases

- Every PR and push to `main` runs `.github/workflows/ci.yml`: **ruff**
  lint/format, **pytest** with coverage (uploaded to Codecov), the version-sync
  check, and `python build.py` plus ROOT-level artifact validation.
- Every PR runs `.github/workflows/commitlint.yml`: the PR title and all commits
  must be [Conventional Commits](https://www.conventionalcommits.org/).
- `main` is protected: pull requests only, squash merges only, linear history,
  no force pushes/deletions, and the CI + commitlint checks are required.
- Merging the release-please PR creates a **draft** GitHub Release;
  `.github/workflows/release-artifact.yml` builds `dist/onedrive-sync.zip`,
  uploads it to the draft, and publishes the release. Drafts are required
  because this repository enables **immutable releases**.

## License

GPL-3.0. See `LICENSE`.
