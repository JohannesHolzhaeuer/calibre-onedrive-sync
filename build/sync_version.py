"""Keep the runtime version in ``src/common/version.py`` in sync with pyproject.toml.

``pyproject.toml`` is the single source of truth for ``release-please`` (the
``python`` release type bumps ``[project].version`` on release). The plugin's
*runtime* version — the string and tuple in ``src/common/version.py``, imported
by ``src/__init__.py`` — must match. This script rewrites
``src/common/version.py`` from ``pyproject.toml`` so a release can't drift.

Usage::

    python build/sync_version.py --check   # exit 1 if out of sync (CI)
    python build/sync_version.py            # rewrite src/common/version.py

The update keeps the ``# x-release-please-version`` marker (release-please also
writes to files carrying that marker) so both mechanisms agree.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYPROJECT = os.path.join(ROOT, "pyproject.toml")
VERSION_FILE = os.path.join(ROOT, "src", "common", "version.py")

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.M)

VERSION_HEADER = '''"""Single source of truth for the plugin's version.

The version string and tuple used across the plugin and by the build tooling
are defined here and **nowhere else**. ``src/__init__.py`` exposes the tuple on
the plugin class, so bumping one value updates every consumer.

release-please keeps this file in sync: it rewrites ``__version__`` (the
``# x-release-please-version`` marker below defines the replace target) when a
release PR is created. See ``build/sync_version.py``.
"""

from __future__ import annotations

__version__ = "{version}"  # x-release-please-version

#: ``(major, minor, patch)`` tuple consumed by the calibre plugin class.
VERSION_TUPLE: tuple[int, int, int] = tuple(
    int(part) for part in __version__.split(".")
)  # type: ignore[assignment]
'''


def read_pyproject_version() -> str | None:
    with open(PYPROJECT, encoding="utf-8") as handle:
        match = _VERSION_RE.search(handle.read())
    return match.group(1) if match else None


def read_version_file() -> str | None:
    with open(VERSION_FILE, encoding="utf-8") as handle:
        match = re.search(r'__version__\s*=\s*"([^"]+)"', handle.read())
    return match.group(1) if match else None


def write_version_file(version: str) -> None:
    with open(VERSION_FILE, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(VERSION_HEADER.format(version=version))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify sync and exit non-zero otherwise"
    )
    opts = parser.parse_args(argv)

    version = read_pyproject_version()
    if not version:
        print("ERROR: no version found in pyproject.toml", file=sys.stderr)
        return 1
    current = read_version_file()
    if opts.check:
        if current != version:
            print(
                f"ERROR: src/common/version.py is {current!r}, expected {version!r} "
                f"from pyproject.toml. Run `python build/sync_version.py`.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: src/common/version.py is in sync ({version}).")
        return 0
    if current != version:
        write_version_file(version)
        print(f"Updated src/common/version.py {current!r} -> {version!r}")
    else:
        print(f"src/common/version.py already in sync ({version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
