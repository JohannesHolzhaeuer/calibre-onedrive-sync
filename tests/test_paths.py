"""Tests for pure path/validation helpers."""

from __future__ import annotations

import os

from onedrive_sync.common import paths
from onedrive_sync.common.config import Settings


def test_normalize_folder(tmp_path):
    assert paths.normalize_folder("") == ""
    assert paths.normalize_folder("  ") == ""
    assert paths.normalize_folder(str(tmp_path)) == os.path.normpath(str(tmp_path))


def test_default_onedrive_folder_uses_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    assert paths.default_onedrive_folder() == ""

    monkeypatch.setenv("OneDrive", str(tmp_path / "missing"))
    assert paths.default_onedrive_folder() == ""

    monkeypatch.setenv("OneDriveCommercial", str(tmp_path))
    assert paths.default_onedrive_folder() == str(tmp_path)


def test_target_path(tmp_path):
    root = str(tmp_path)
    settings = Settings(onedrive_folder=root, remote_subfolder="calibre-library")
    assert paths.target_path(settings) == os.path.join(root, "calibre-library")

    settings.remote_subfolder = ""
    assert paths.target_path(settings) == root

    settings.onedrive_folder = ""
    assert paths.target_path(settings) == ""


def test_validate_remote_subfolder():
    assert paths.validate_remote_subfolder("") is not None
    assert paths.validate_remote_subfolder("   ") is not None
    assert paths.validate_remote_subfolder("../escape") is not None
    assert paths.validate_remote_subfolder("nested/../escape") is not None
    assert paths.validate_remote_subfolder("C:\\absolute") is not None
    assert paths.validate_remote_subfolder("/absolute") is not None
    assert paths.validate_remote_subfolder("calibre-library") is None
    assert paths.validate_remote_subfolder("nested/library") is None


def test_validate_settings_requires_both_folders():
    errors = paths.validate_settings(Settings())
    assert len(errors) == 2

    errors = paths.validate_settings(
        Settings(library_path="C:/missing", onedrive_folder="C:/also-missing")
    )
    assert any("calibre library folder does not exist" in error for error in errors)
    assert any("OneDrive folder does not exist" in error for error in errors)


def test_validate_settings_accepts_valid_layout(tmp_path):
    library = tmp_path / "library"
    onedrive = tmp_path / "onedrive"
    library.mkdir()
    onedrive.mkdir()

    settings = Settings(
        library_path=str(library),
        onedrive_folder=str(onedrive),
        remote_subfolder="calibre-library",
    )
    assert paths.validate_settings(settings) == []


def test_validate_settings_rejects_overlapping_folders(tmp_path):
    library = tmp_path / "library"
    library.mkdir()

    same = Settings(library_path=str(library), onedrive_folder=str(library), remote_subfolder="")
    errors = paths.validate_settings(same)
    assert any("must not be the calibre library" in error for error in errors)

    inside = Settings(
        library_path=str(library), onedrive_folder=str(library), remote_subfolder="backup"
    )
    errors = paths.validate_settings(inside)
    assert any("must not be inside" in error for error in errors)

    parent = Settings(
        library_path=str(library / "nested"),
        onedrive_folder=str(tmp_path),
        remote_subfolder="library",
    )
    errors = paths.validate_settings(parent)
    assert any("must not be inside the OneDrive target" in error for error in errors)


def test_validate_settings_rejects_file_target(tmp_path):
    library = tmp_path / "library"
    onedrive = tmp_path / "onedrive"
    library.mkdir()
    onedrive.mkdir()
    target_file = onedrive / "calibre-library"
    target_file.write_text("not a folder", encoding="utf-8")

    settings = Settings(
        library_path=str(library),
        onedrive_folder=str(onedrive),
        remote_subfolder="calibre-library",
    )
    errors = paths.validate_settings(settings)
    assert any("exists but is not a folder" in error for error in errors)


def test_validate_settings_rejects_bad_subfolder(tmp_path):
    library = tmp_path / "library"
    onedrive = tmp_path / "onedrive"
    library.mkdir()
    onedrive.mkdir()

    settings = Settings(
        library_path=str(library),
        onedrive_folder=str(onedrive),
        remote_subfolder="../escape",
    )
    errors = paths.validate_settings(settings)
    assert any("relative folder name" in error for error in errors)
