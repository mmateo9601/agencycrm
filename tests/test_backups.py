import hashlib
import json
import sqlite3
import stat
import uuid
import zipfile
from contextlib import closing
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.jobs.locking import WorkerAlreadyRunning, WorkerLock
from common.backups import backup_database, restore_database


@pytest.fixture
def archive_source(tmp_path):
    source = tmp_path / "source.sqlite3"
    media = tmp_path / "media"
    name = f"proposals/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}.pdf"
    document = media / name
    document.parent.mkdir(parents=True)
    data = b"%PDF-1.4 fixture bytes; no executable content"
    document.write_bytes(data)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE proposals_proposal (pdf TEXT, pdf_sha256 TEXT)")
        connection.execute("CREATE TABLE proposals_salesasset (file TEXT, sha256 TEXT)")
        connection.execute(
            "INSERT INTO proposals_proposal VALUES (?, ?)", (name, hashlib.sha256(data).hexdigest())
        )
        connection.commit()
    return {
        "database": source,
        "media": media,
        "document": document,
        "name": name,
        "archive": tmp_path / "verified.zip",
        "restore": tmp_path / "restored",
    }


def backup(env):
    return backup_database(
        destination=env["archive"], database_path=env["database"], media_root=env["media"]
    )


def rewrite(env, change):
    with zipfile.ZipFile(env["archive"]) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    change(entries)
    with zipfile.ZipFile(env["archive"], "w") as target:
        for name, data in entries.items():
            target.writestr(name, data)


def test_backup_restore_preserves_database_documents_and_excludes_secrets(archive_source):
    env = archive_source
    (env["media"].parent / ".env").write_text("SECRET_KEY=never-archive")
    manifest = backup(env)
    assert set(manifest["files"]) == {"database.sqlite3", f"media/{env['name']}"}
    restored = restore_database(archive_path=env["archive"], destination=env["restore"])
    assert restored == manifest
    assert (env["restore"] / "media" / env["name"]).read_bytes() == env["document"].read_bytes()
    with closing(sqlite3.connect(env["restore"] / "database.sqlite3")) as connection:
        assert connection.execute("SELECT pdf FROM proposals_proposal").fetchone() == (env["name"],)


def test_backups_refuse_worker_lock_and_existing_destinations(archive_source):
    env = archive_source
    with WorkerLock(env["database"].with_name(env["database"].name + ".worker.lock")):
        with pytest.raises(WorkerAlreadyRunning):
            backup(env)
    backup(env)
    with pytest.raises(ValidationError):
        backup(env)
    env["restore"].mkdir()
    marker = env["restore"] / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValidationError):
        restore_database(archive_path=env["archive"], destination=env["restore"])
    assert marker.read_text() == "keep"


@pytest.mark.parametrize("change", ["corrupt", "traversal", "extra", "missing", "manifest", "size"])
def test_restore_rejects_tampered_archives_before_creating_destination(archive_source, change):
    env = archive_source
    backup(env)

    def corrupt(entries):
        if change == "corrupt":
            entries[f"media/{env['name']}"] = b"Changed"
        elif change == "extra":
            entries[".env"] = b"SECRET"
        elif change == "missing":
            del entries[f"media/{env['name']}"]
        else:
            manifest = json.loads(entries["manifest.json"])
            if change == "manifest":
                manifest = []
            elif change == "size":
                manifest["files"]["database.sqlite3"]["size"] = -1
            else:
                item = manifest["files"].pop(f"media/{env['name']}")
                manifest["files"]["media/../../escape.pdf"] = item
                entries["media/../../escape.pdf"] = entries.pop(f"media/{env['name']}")
            entries["manifest.json"] = json.dumps(manifest).encode()

    rewrite(env, corrupt)
    with pytest.raises(ValidationError):
        restore_database(archive_path=env["archive"], destination=env["restore"])
    assert not env["restore"].exists()
    assert not (env["restore"].parent / "escape.pdf").exists()


def test_backup_missing_file_fails_without_partial_archive(archive_source):
    env = archive_source
    env["document"].unlink()
    with pytest.raises(ValidationError):
        backup(env)
    assert not env["archive"].exists()


def test_backup_rechecks_hash_after_archive_write(archive_source):
    env = archive_source
    original = zipfile.ZipFile.write

    def changing(self, path, name, **kwargs):
        if name.startswith("media/"):
            env["document"].write_bytes(b"Changed after validation")
        return original(self, path, name, **kwargs)

    with patch("zipfile.ZipFile.write", new=changing), pytest.raises(ValidationError):
        backup(env)
    assert not env["archive"].exists()


def test_symlinks_are_rejected_in_archives(archive_source):
    env = archive_source
    backup(env)
    with zipfile.ZipFile(env["archive"]) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    with zipfile.ZipFile(env["archive"], "w") as target:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            target.writestr(info, data)
    with pytest.raises(ValidationError):
        restore_database(archive_path=env["archive"], destination=env["restore"])
    assert not env["restore"].exists()


def test_backup_cannot_write_into_public_static_or_media(archive_source, settings):
    env = archive_source
    settings.STATICFILES_DIRS = [env["archive"].parent]
    with pytest.raises(ValidationError):
        backup(env)


def test_backup_and_restore_commands(archive_source, monkeypatch):
    env = archive_source
    monkeypatch.setattr(
        "common.backups.settings.DATABASES",
        {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": env["database"]}},
    )
    monkeypatch.setattr("common.backups.settings.MEDIA_ROOT", env["media"])
    call_command("backup_database", output=env["archive"], stdout=StringIO())
    call_command(
        "restore_database", input=env["archive"], destination=env["restore"], stdout=StringIO()
    )
    with pytest.raises(CommandError):
        call_command("backup_database", output=env["archive"])


def test_corrupt_sqlite_in_valid_archive_is_rejected(archive_source):
    env = archive_source
    backup(env)

    def corrupt(entries):
        data = b"This is not a SQLite database"
        manifest = json.loads(entries["manifest.json"])
        manifest["files"]["database.sqlite3"] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        entries["database.sqlite3"] = data
        entries["manifest.json"] = json.dumps(manifest).encode()

    rewrite(env, corrupt)
    with pytest.raises(ValidationError):
        restore_database(archive_path=env["archive"], destination=env["restore"])
    assert not env["restore"].exists()


def test_unsupported_database_adapter_fails_cleanly(archive_source, monkeypatch):
    monkeypatch.setattr(
        "common.backups.settings.DATABASES",
        {"default": {"ENGINE": "django.db.backends.postgresql"}},
    )
    with pytest.raises(ValidationError, match="PostgreSQL"):
        backup(archive_source)
