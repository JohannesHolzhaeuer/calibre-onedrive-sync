"""Path helpers and validation for the OneDrive sync.

Everything here is pure standard-library code so it can be unit-tested without
calibre or Qt. The validation deliberately rejects a target folder that equals
or lives inside the calibre library: syncing a library into itself would make
every pass copy its own output and never converge.
"""

from __future__ import annotations

import os

from .config import Settings
from .i18n import _

#: Environment variables the OneDrive desktop client sets for its sync root.
ONEDRIVE_ENV_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")


def normalize_folder(path: str) -> str:
    """Expand ``~`` and normalize separators; return ``''`` for empty input."""
    text = (path or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.expanduser(text))


def default_onedrive_folder() -> str:
    """Return the first existing OneDrive folder advertised by the environment."""
    for name in ONEDRIVE_ENV_VARS:
        candidate = normalize_folder(os.environ.get(name, ""))
        if candidate and os.path.isdir(candidate):
            return candidate
    return ""


def target_path(settings: Settings) -> str:
    """The full path that receives the synced library inside OneDrive."""
    root = normalize_folder(settings.onedrive_folder)
    subfolder = (settings.remote_subfolder or "").strip()
    if not root:
        return ""
    if not subfolder:
        return root
    return os.path.normpath(os.path.join(root, subfolder))


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _is_inside(parent: str, child: str) -> bool:
    """True when ``child`` is strictly below ``parent``."""
    if not parent or not child:
        return False
    parent_abs = os.path.normcase(os.path.abspath(parent))
    child_abs = os.path.normcase(os.path.abspath(child))
    try:
        return os.path.commonpath([parent_abs, child_abs]) == parent_abs and not _same_path(
            parent_abs, child_abs
        )
    except ValueError:  # pragma: no cover - different drives on Windows
        return False


def validate_remote_subfolder(subfolder: str) -> str | None:
    """Return an error message when ``subfolder`` could escape the OneDrive root."""
    text = (subfolder or "").strip()
    if not text:
        return _("Enter a folder name inside OneDrive.")
    normalized = text.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if (
        normalized.startswith("/")
        or os.path.isabs(text)
        or ":" in text
        or any(part == ".." for part in parts)
    ):
        return _("The OneDrive subfolder must be a relative folder name.")
    return None


def validate_settings(settings: Settings) -> list[str]:
    """Return a list of human-readable validation errors (empty when valid)."""
    errors: list[str] = []
    library = normalize_folder(settings.library_path)
    onedrive = normalize_folder(settings.onedrive_folder)

    if not library:
        errors.append(_("Select your calibre library folder."))
    elif not os.path.isdir(library):
        errors.append(_("The calibre library folder does not exist."))

    if not onedrive:
        errors.append(_("Select your OneDrive folder."))
    elif not os.path.isdir(onedrive):
        errors.append(_("The OneDrive folder does not exist."))

    subfolder_error = validate_remote_subfolder(settings.remote_subfolder)
    if subfolder_error:
        errors.append(subfolder_error)

    target = target_path(settings)
    if library and target:
        if _same_path(library, target):
            errors.append(_("The OneDrive target must not be the calibre library folder."))
        elif _is_inside(library, target):
            errors.append(_("The OneDrive target must not be inside the calibre library."))
        elif _is_inside(target, library):
            errors.append(_("The calibre library must not be inside the OneDrive target."))

    if target and os.path.exists(target) and not os.path.isdir(target):
        errors.append(_("The OneDrive target exists but is not a folder."))

    return errors
