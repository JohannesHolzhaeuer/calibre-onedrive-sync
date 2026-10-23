"""Build the onedrive-sync calibre plugin zip.

Zips the *contents* of ``src/`` into ``dist/onedrive-sync.zip`` so that
``__init__.py`` sits at the archive ROOT (calibre requirement). If you zip the
``src/`` folder itself, calibre resolves imports as
``calibre_plugins.onedrive_sync.src.*`` and the plugin breaks.

Pure stdlib only (os, zipfile, argparse, shutil, subprocess). No third-party
dependencies.

Usage::

    python build.py            # build dist/onedrive-sync.zip
    python build.py --install  # build + install into calibre
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
OUT = os.path.join(HERE, "dist", "onedrive-sync.zip")

EXCLUDE_DIRS = {"__pycache__", ".egg-info"}
EXCLUDE_FILES = {".DS_Store", ".gitkeep", "Thumbs.db"}


def _should_prune(root: str, name: str) -> bool:
    """Return True when ``name`` under ``root`` must be skipped from the zip."""
    full = os.path.join(root, name)
    if os.path.isdir(full):
        return name in EXCLUDE_DIRS or name.endswith(".egg-info")
    return name in EXCLUDE_FILES or name.endswith((".pyc", ".pyo"))


def build_zip(out_path: str = OUT, src: str = SRC) -> str:
    """Zip the contents of ``src`` into ``out_path`` and return the artifact path."""
    if os.path.exists(out_path):
        os.remove(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if not _should_prune(root, d)]
            for name in files:
                if _should_prune(root, name):
                    continue
                full = os.path.join(root, name)
                arcname = os.path.relpath(full, src)
                zf.write(full, arcname)

    size = os.path.getsize(out_path)
    print(f"Wrote {out_path} ({size} bytes)")

    with zipfile.ZipFile(out_path) as zf:
        names = zf.namelist()
    top = {name.split("/", 1)[0] for name in names}
    assert "__init__.py" in top, "missing __init__.py at ZIP ROOT"
    assert "plugin-import-name-onedrive_sync.txt" in top, "missing import-name marker at ZIP ROOT"
    assert not any(name.startswith("src/") for name in names), "src/ leaked into ZIP"
    assert "common/version.py" in names, "missing common/version.py"
    assert "gui/action.py" in names, "missing gui/action.py"
    print(
        "Verified: __init__.py + import-name marker at ZIP ROOT; "
        "plugin package modules packaged without a src/ prefix."
    )

    return out_path


def _install(out_path: str) -> None:
    cmd = [shutil.which("calibre-customize") or "calibre-customize", "-a", out_path]
    print("Installing plugin via: " + " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
        print("Installed.")
    except Exception as exc:  # noqa: BLE001 - best-effort install
        print(f"Install failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install", action="store_true", help="install into calibre after building"
    )
    parser.add_argument("--out", default=OUT, help="output zip path (default: %(default)s)")
    args = parser.parse_args(argv)

    path = build_zip(args.out)
    if args.install:
        _install(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
