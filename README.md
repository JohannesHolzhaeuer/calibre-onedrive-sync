# calibre-onedrive-sync

A [calibre 9](https://calibre-ebook.com/) plugin that keeps a calibre library
in sync with a folder in **OneDrive**.

The plugin talks to **Microsoft Graph** directly using OAuth 2.0's device-code
flow. It does not need the OneDrive desktop client and never asks for your
Microsoft password. The plugin ships with a shared public Entra app, so you can
sign in without registering your own app. Advanced users can replace the
prefilled Application (client) ID with their own app registration.

## What it does

Everything is **manual**. The plugin never syncs at startup, on a timer or when
the library changes: nothing leaves your machine and nothing is imported until
you click the toolbar button. It only ever looks at the **direct children of the
selected folder** — subfolders are never read.

- **Stored folder**: the toolbar button uses the OneDrive folder from the
  settings — the picker only appears while setting the plugin up, when no folder
  is stored yet. The plugin then checks whether that folder already carries a
  calibre index.
- **Index gate**: if the folder has a usable `calibre-onedrive-index.json`, the
  matching window opens. If it does not, the plugin offers to clear the folder
  and upload the whole library together with a fresh index.
- **Imports** book files that appear in the selected folder into the current
  calibre library — only when you ask for it (single rows or **Download all** in
  the review window). Files are tracked by drive/item id, so a book is imported
  only once.
- **Exports** calibre books as **EPUB** (preferred) or **PDF** directly into the
  selected folder. Re-exporting updates the same remote file instead of creating
  duplicates.
- **Matches before syncing**: the review window fills in while OneDrive is
  scanned: local books first, then their OneDrive counterparts, then
  metadata-based refinements. It lists what would be uploaded or downloaded and
  which extra copies can be removed. Nothing is transferred until you confirm.
- **Writes a calibre index** (`calibre-onedrive-index.json`) into the selected
  folder on every upload. The index maps each stored file to its calibre book id,
  so a later scan matches by item id instead of guessing from the filename.
- **Bulk actions** in the review window: **Upload all** uploads every book and
  writes the index, **Download all** imports everything without matching (clean
  duplicates up afterwards with calibre's *Find Duplicates* plugin), and
  **Clear backup** deletes every file in the selected folder after a
  confirmation so you can start over.
- **Remembers the OneDrive link** for each book in calibre's identifiers field,
  so later scans recognise the same file even after it was renamed.
- Runs long operations in **calibre threaded jobs**; the GUI stays responsive and
  a running scan/upload/download can be cancelled from calibre's job panel.
- Never deletes local books and never mirrors local deletions. Deleting a
  duplicate OneDrive copy is possible from the review window and moves that file
  to the OneDrive recycle bin; **Clear backup** deletes the whole remote folder
  only after an explicit confirmation.

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
is accessed (and only its direct children).

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
   picker uses `/me/drive`; it does not list shared drives. This folder is what
   every toolbar click uses; change it here to point the plugin somewhere else.
4. Choose the export format: **EPUB, falling back to PDF**, **EPUB only**, or
   **PDF only**. Books are always uploaded directly into the selected folder.
5. Add the **OneDrive Sync** button to a toolbar via *Preferences → Toolbars &
   menus*. If the plugin is not configured yet, the first click opens this
   dialog.

There is no automatic sync and no interval to configure. The configuration is
stored in calibre's settings. The refresh token is stored in the platform
keyring when available and otherwise in calibre's JSON config; the short-lived
access token is never written to disk.

## How syncing behaves

- **One click, one decision**: clicking the toolbar button uses the configured
  OneDrive folder and checks it for `calibre-onedrive-index.json`. Only when no
  folder is stored yet does the folder picker open.
  - **Index found** → the matching window opens immediately and fills in while
    the scan runs (local books first, then the OneDrive files, then
    metadata-based refinements).
  - **No index** → you are asked whether to clear the folder (everything goes to
    the OneDrive recycle bin) and upload your whole library with a fresh index.
    Only a confirmed *Yes* starts that chain; any error or cancellation stops it.
  - **Folder is gone** (deleted or renamed since you chose it) → you are told so
    and asked to pick a new folder.
  Closing the window stops the scan.
- **Flat folder only**: only the direct children of the selected folder are
  listed. Nothing inside a subfolder is read, matched, downloaded or deleted.
- **Bulk review actions**: **Upload all** uploads every book that is not in
  OneDrive yet (existing files are linked, not uploaded again) and rewrites the
  index. **Download all** imports every ebook file it has not imported before,
  deliberately allowing duplicates — run calibre's *Find Duplicates* plugin
  afterwards to clean them up. **Clear backup** asks once, then deletes every
  item directly inside the selected OneDrive folder, forgets the sync state for
  that drive, and clears the `onedrive` identifier from your books, so the next
  **Upload all** starts from an empty folder.
- **Matching**: a saved OneDrive link wins, then the `calibre-onedrive-index.json`
  written by the plugin (item id first, then a unique filename), then the
  calibre book id in the export filename, then title and author. For remote
  EPUB/PDF files whose names are not conclusive it temporarily downloads the
  file to read its embedded metadata (never importing the book just for this),
  and caches the result until the file changes.
- **Import**: lists the direct children of the selected folder, downloads files
  with supported book extensions, and adds them through calibre's normal
  add-books API. The source item is immediately stored as the book's OneDrive
  link, so a later upload does not add it back as a duplicate.
- **Export**: uploads one EPUB/PDF per book into the selected folder. The remote
  item id is remembered so edits replace the same file. If a file for the same
  book already exists under another name or format, it is linked and adopted
  instead of duplicated. After a successful pass the index is rewritten.
- **Links**: each linked book gets an `onedrive` identifier containing the
  OneDrive web URL (or the item id when Graph supplies no URL). The SQLite state
  stores the same information so a reinstall can rebuild it on the next scan.
- **No local deletes**: removing a book locally or remotely does not delete the
  other copy. Only an explicitly ticked duplicate in the review window is
  removed, and it goes to the OneDrive recycle bin.
- **No self-sync**: the plugin works through Graph and never copies a calibre
  library into itself.
- **Conflicts** are handled with OneDrive's rename conflict behaviour; the
  plugin does not merge different versions.

## Project structure

| Path | Purpose |
| --- | --- |
| `src/` | All plugin source. The **contents** of this folder become the zip. |
| `src/__init__.py` | Declares the `OneDriveSyncAction` plugin class (`InterfaceActionBase`). Must sit at the zip root. |
| `src/common/` | Calibre-independent logic: settings, OAuth, Graph client, sync state, matching, sync engine. |
| `src/gui/` | Interface action, configuration/sign-in/folder dialogs, matching window and jobs. |
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
