"""Skeleton smoke tests.

``build.py`` is pure stdlib, so these run in a calibre-free environment and
verify the packaging contract: the zip ROOT must contain ``__init__.py`` and
the import-name marker file (never the ``src/`` folder itself).
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILD = os.path.join(REPO_ROOT, "build.py")
SYNC_VERSION = os.path.join(REPO_ROOT, "build", "sync_version.py")


@pytest.fixture()
def build_dir(tmp_path):
    out = tmp_path / "onedrive-sync.zip"
    subprocess.run([sys.executable, BUILD, "--out", str(out)], check=True, cwd=REPO_ROOT)
    return out


def test_build_creates_zip(build_dir):
    assert build_dir.exists()
    assert build_dir.stat().st_size > 0


def test_zip_root_contains_entry_and_marker(build_dir):
    with zipfile.ZipFile(build_dir) as zf:
        names = zf.namelist()
    top = {name.split("/", 1)[0] for name in names}
    assert "__init__.py" in top
    assert "plugin-import-name-onedrive_sync.txt" in top


def test_zip_contains_package_modules(build_dir):
    with zipfile.ZipFile(build_dir) as zf:
        names = zf.namelist()
    for expected in (
        "common/__init__.py",
        "common/auth.py",
        "common/config.py",
        "common/defaults.py",
        "common/graph.py",
        "common/oauth.py",
        "common/state.py",
        "common/sync.py",
        "common/version.py",
        "gui/action.py",
        "gui/auth_dialog.py",
        "gui/config_widget.py",
        "gui/folder_picker.py",
        "gui/library.py",
        "gui/scheduler.py",
        "gui/sync_job.py",
        "resources/images/onedrive.png",
    ):
        assert expected in names


def test_zip_never_contains_src_prefix(build_dir):
    """Guards against the 'zipped src/ folder by mistake' packaging bug."""
    with zipfile.ZipFile(build_dir) as zf:
        names = zf.namelist()
    assert not any(name.startswith("src/") for name in names)
    assert not any(name.endswith((".pyc", ".pyo")) for name in names)


def test_coverage_is_a_merge_gate():
    import tomllib

    with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as handle:
        config = tomllib.load(handle)
    assert config["tool"]["coverage"]["report"]["fail_under"] >= 90

    with open(os.path.join(REPO_ROOT, "codecov.yml"), encoding="utf-8") as handle:
        codecov = handle.read()
    assert "target: auto" in codecov
    assert "threshold: 0%" in codecov


def test_version_is_single_source_of_truth():
    from onedrive_sync.common.version import VERSION_TUPLE, __version__

    assert VERSION_TUPLE == tuple(int(part) for part in __version__.split("."))


def test_sync_version_check_passes():
    result = subprocess.run(
        [sys.executable, SYNC_VERSION, "--check"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_plugin_is_a_single_interface_action():
    """Calibre registers ONE Plugin per zip; the zip must be the action."""
    import calibre.customize as cc

    import onedrive_sync as plugin

    plugin_subclasses = [
        value
        for value in vars(plugin).values()
        if isinstance(value, type)
        and value.__module__ == "onedrive_sync"
        and issubclass(value, cc.Plugin)
    ]
    assert plugin_subclasses == [plugin.OneDriveSyncAction]
    assert issubclass(plugin.OneDriveSyncAction, cc.InterfaceActionBase)


def test_plugin_reports_customizable():
    from onedrive_sync import OneDriveSyncAction

    assert OneDriveSyncAction().customization_help()
