import os

from core import database


def test_frozen_app_uses_persistent_local_app_data_for_database(monkeypatch, tmp_path):
    workspace_root = r"c:\Users\lokad\Music\Practical\StrictRecords"
    original_isdir = os.path.isdir
    monkeypatch.setattr(
        database.os.path,
        "isdir",
        lambda path: False if path == workspace_root else original_isdir(path),
    )
    monkeypatch.setattr(database.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert database._default_database_path() == os.path.join(
        str(tmp_path), "StrictRecords", "data", "strictrecords.db"
    )


def test_existing_workspace_database_location_is_preserved(monkeypatch):
    workspace_root = r"c:\Users\lokad\Music\Practical\StrictRecords"
    original_isdir = os.path.isdir
    monkeypatch.setattr(
        database.os.path,
        "isdir",
        lambda path: True if path == workspace_root else original_isdir(path),
    )

    assert database._default_database_path() == os.path.join(
        workspace_root, "data", "strictrecords.db"
    )
