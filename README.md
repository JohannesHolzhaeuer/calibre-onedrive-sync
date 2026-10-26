# calibre-onedrive-sync

A [calibre 9](https://calibre-ebook.com/) plugin that keeps a calibre library
in sync with a folder in **OneDrive**.

The plugin talks to **Microsoft Graph** directly using OAuth 2.0's device-code
flow. It does not need the OneDrive desktop client and never asks for your
Microsoft password. The plugin ships with a shared public Entra app, so you can
sign in without registering your own app. Advanced users can replace the
prefilled Application (client) ID with their own app registration.

## What it does

- **Imports** book files that appear in the selected OneDrive folder into the
  current calibre library. Files are tracked by drive/item id, so a book is
  imported only once.
- **Exports** calibre books as **EPUB** (preferred) or **PDF** into the selected
  OneDrive folder or an optional subfolder. Re-exporting updates the same remote
  file instead of creating duplicates.
- Detects remote changes with **Graph delta queries** and local changes through
  calibre's database listener plus a debounced timer.
- Runs long operations in **calibre threaded jobs**; the GUI stays responsive.
- Does **not** delete local or remote books, and never mirrors deletions.

## Requirements

- calibre **9.0** or newer (Qt6 / Python 3).
- A Microsoft account. The shared app supports personal Microsoft accounts;
  work/school accounts need your own app registration.
- No Entra app registration is required for personal accounts; a shared public
  app is preconfigured.
- For development: Python **3.11+** plus the dev extras
  (`pip install -e ".[dev]"`).

## Microsoft app

The plugin uses the shared public app ID
`9f70f5c3-4b51-43d2-b0d4-3f2abad5754f` by default. It is registered for personal
Microsoft accounts, so every user with a personal account can sign in with it;
each user consents once and the refresh token stays on their machine. No client
secret is used.

Microsoft Graph does not support limiting OAuth consent to a single OneDrive
folder. After sign-in the plugin browses `/me/drive`; only the folder you select
(and its export subfolder, if enabled) is accessed.

### Why the app cannot be created or picked automatically

Two things users often ask for are not possible, which is exactly why a shared
app is bundled:

- **The plugin cannot register an app for you.** Microsoft Entra ID has no
  anonymous or dynamic client registration, and creating an app through the
  Graph API or Azure CLI requires a token with `Application.ReadWrite.*` that
  already has admin consent. An unauthenticated desktop client cannot bootstrap
  its own registration.
- **The app cannot be chosen during sign-in.** The device-code flow sends the
  `client_id` when it requests a device code, *before* any user signs in, so the
  app must be known up front.

The shared public app removes per-user app creation for personal accounts. Only
work/school accounts still need their own registration (plus tenant or admin
consent for `Files.ReadWrite.All`) - see below.

## Use your own Entra app (optional)

Only needed if you do not want to use the shared app, or need work/school
accounts. Register the app for any Entra tenant plus personal Microsoft accounts
and allow public client flows; custom app IDs use the `common` authority.
App registrations and Microsoft Graph API calls are free, but your OneDrive
storage limits still apply.

1. Open the [Microsoft Entra admin center](https://entra.microsoft.com/) →
   **App registrations** → **New registration**.
2. Name it (for example `calibre-onedrive-sync`) and choose the account types
   you need (personal Microsoft accounts and/or work/school accounts). Leave
   the redirect URI empty — the device-code flow does not use one.
3. Open **Authentication** → **Advanced settings** → **Allow public client
   flows** → **Yes**, then save.
4. Open **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions** and add:
   - `Files.ReadWrite.All` — read/write the folder you select in your
     OneDrive;
   - `User.Read` — show the signed-in account name;
   - `offline_access` — receive a refresh token so you do not sign in every
     time.
5. If your organisation requires it, click **Grant admin consent**.
6. Copy the **Application (client) ID** from the app's overview page.

`Files.ReadWrite.All` is broad: it lets the plugin access any file the signed-in
user can access. The plugin only touches the folder you select.

## Build & install

```bash
python build.py            # creates dist/onedrive-sync.zip
python build.py --install  # ... and installs it into calibre
```

Install the zip manually via *calibre → Preferences → Plugins → Load plugin
from file*.

## Configure

Open *Preferences → Plugins → Customize plugin → OneDrive Sync*:

1. The shared **Application (client) ID** is prefilled. Replace it only if you
   are using your own Entra app.
2. Click **Sign in…**. A browser page opens; enter the short code shown in the
   dialog. The dialog closes automatically when sign-in succeeds.
3. Click **Choose folder…** and select an existing folder from the tree. The
   picker uses `/me/drive`; it does not list shared drives.
4. Choose the export destination and format: leave **Export directly into the
   selected folder** unchecked to create/use the **export subfolder** (default
   `Calibre Export`), or check it to export into the selected folder itself.
   Then choose **EPUB, falling back to PDF**, **EPUB only**, or **PDF only**.
5. Enable importing and/or exporting and optionally automatic sync. Local
   library changes sync immediately; the interval controls the fallback check
   for remote OneDrive changes.
6. Add the **OneDrive Sync** button to a toolbar via
   *Preferences → Toolbars & menus*. Clicking the button syncs immediately; if the plugin is not configured
   yet, the first click opens this dialog.

The configuration is stored in calibre's settings. The refresh token is stored
in the platform keyring when available and otherwise in calibre's JSON config;
the short-lived access token is never written to disk.

## How syncing behaves

- **Import**: walks the selected folder (delta query first, recursive listing
  as fallback), downloads files with supported book extensions, and adds them
  through calibre's normal add-books API. The export subfolder is skipped.
- **Export**: uploads one EPUB/PDF per book to the export subfolder. The remote
  item id is remembered so edits replace the same file. If a file with the
  expected name and size already exists, it is adopted instead of duplicated.
- **No deletes**: removing a book locally or remotely does not delete the other
  copy.
- **No self-sync**: the plugin works through Graph and never copies a calibre
  library into itself.
- **Conflicts** are handled with OneDrive's rename conflict behaviour; the
  plugin does not merge different versions.

## Project structure

| Path | Purpose |
| --- | --- |
| `src/` | All plugin source. The **contents** of this folder become the zip. |
| `src/__init__.py` | Declares the `OneDriveSyncAction` plugin class (`InterfaceActionBase`). Must sit at the zip root. |
| `src/common/` | Calibre-independent logic: settings, OAuth, Graph client, sync state, sync engine. |
| `src/gui/` | Interface action, configuration/sign-in/folder dialogs, jobs and scheduler. |
| `src/resources/` | Icons. |
| `tests/` | pytest suite with fake Graph/HTTP/Qt/calibre seams (no network). |
| `dist/` | Build output — `dist/onedrive-sync.zip` (gitignored). |
| `build.py` | Zips the contents of `src/` into `dist/onedrive-sync.zip`. |
| `build/` | Build tooling (`sync_version.py`). |

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
the GUI modules can be imported without calibre or PyQt installed. Network code
is tested through the injectable `transport` / `downloader` seams in
`common/http.py`; the suite never contacts Microsoft.

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
