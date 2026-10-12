"""Offline SQLite backup adapter. PostgreSQL will use its own infrastructure adapter."""

import hashlib
import json
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.jobs.locking import WorkerLock
from common.private_files import private_file_path

MAX_DATABASE_BYTES = 1_000_000_000
MAX_TOTAL_BYTES = 2_000_000_000
MAX_MANIFEST_BYTES = 4_000_000
MAX_FILES = 10000


def require_private_destination(path: Path, media_root: Path) -> None:
    """Keep databases and archives outside every directory served by Django static handlers."""
    public = [media_root, Path(settings.STATIC_ROOT), *settings.STATICFILES_DIRS]
    if any(path.resolve().is_relative_to(Path(root).resolve()) for root in public):
        raise ValidationError("El destino no puede estar en directorios servidos por HTTP.")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def sqlite_integrity(path: Path) -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValidationError("La copia SQLite no supera la comprobación de integridad.")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValidationError("La copia contiene relaciones inconsistentes.")


def referenced_documents(path: Path) -> dict[str, str]:
    documents = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        for table, field, sha in (
            ("proposals_proposal", "pdf", "pdf_sha256"),
            ("proposals_salesasset", "file", "sha256"),
        ):
            for name, expected in connection.execute(f"SELECT {field}, {sha} FROM {table}"):
                if name:
                    documents[name] = expected
    if len(documents) > MAX_FILES:
        raise ValidationError("La copia supera el límite de 10000 PDFs.")
    return documents


def backup_database(
    *, destination: Path, database_path: Path | None = None, media_root: Path | None = None
) -> dict:
    """Copy SQLite through its backup API and retain only referenced, verified private PDFs."""
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        raise ValidationError("Este adaptador solo admite SQLite; PostgreSQL requiere pg_dump.")
    source = Path(database_path or settings.DATABASES["default"]["NAME"]).resolve()
    media_root = Path(media_root or settings.MEDIA_ROOT)
    destination = Path(destination).absolute()
    if (
        not source.is_file()
        or destination.exists()
        or destination.is_symlink()
        or not destination.parent.is_dir()
    ):
        raise ValidationError(
            "La base debe existir y el destino debe ser un archivo nuevo en una carpeta existente."
        )
    require_private_destination(destination, media_root)
    created = False
    completed = False
    try:
        with (
            WorkerLock(Path(f"{source}.worker.lock")),
            tempfile.TemporaryDirectory(prefix="agencycrm-backup-") as temporary,
        ):
            snapshot = Path(temporary) / "database.sqlite3"
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
                with closing(sqlite3.connect(snapshot)) as target:
                    original.backup(target)
            sqlite_integrity(snapshot)
            size = snapshot.stat().st_size
            if size > MAX_DATABASE_BYTES:
                raise ValidationError("La base supera el límite de 1 GB del adaptador local.")
            files = {"database.sqlite3": {"size": size, "sha256": digest(snapshot)}}
            paths = {"database.sqlite3": snapshot}
            for name, expected in referenced_documents(snapshot).items():
                path = private_file_path(media_root, name)
                if (
                    not path.is_file()
                    or path.stat().st_size > 2_000_000
                    or digest(path) != expected
                ):
                    raise ValidationError("Falta un PDF referenciado o su integridad no coincide.")
                entry = f"media/{name}"
                files[entry] = {"size": path.stat().st_size, "sha256": expected}
                paths[entry] = path
            if sum(item["size"] for item in files.values()) > MAX_TOTAL_BYTES:
                raise ValidationError("La copia supera el límite total de 2 GB.")
            manifest = {
                "format": "agencycrm-backup-v1",
                "created_at": datetime.now(UTC).isoformat(),
                "files": files,
            }
            with destination.open("xb") as handle:
                created = True
                with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name, path in paths.items():
                        archive.write(path, name)
                    archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            with zipfile.ZipFile(destination) as archive:
                for name, expected in files.items():
                    checksum = hashlib.sha256()
                    with archive.open(name) as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            checksum.update(chunk)
                    if checksum.hexdigest() != expected["sha256"]:
                        raise ValidationError(
                            "Un archivo cambió durante la copia. Repite en mantenimiento."
                        )
            completed = True
            return manifest
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        raise ValidationError("No se pudo crear una copia íntegra.") from error
    finally:
        if created and not completed:
            destination.unlink(missing_ok=True)


def restore_database(*, archive_path: Path, destination: Path) -> dict:
    """Validate an archive completely before publishing a new, offline installation folder."""
    archive_path = Path(archive_path).resolve()
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValidationError("La restauración necesita una carpeta de destino nueva.")
    require_private_destination(destination, Path(settings.MEDIA_ROOT))
    try:
        with (
            zipfile.ZipFile(archive_path) as archive,
            tempfile.TemporaryDirectory(prefix="agencycrm-restore-") as temporary,
        ):
            members = archive.infolist()
            names = [item.filename for item in members]
            if len(members) > MAX_FILES + 2 or len(set(names)) != len(names):
                raise ValidationError("Archivo con entradas duplicadas o excesivas.")
            info = archive.getinfo("manifest.json")
            if info.file_size > MAX_MANIFEST_BYTES:
                raise ValidationError("Manifiesto demasiado grande.")
            manifest = json.loads(archive.read(info))
            if not isinstance(manifest, dict):
                raise ValidationError("Manifiesto no válido.")
            files = manifest.get("files")
            if (
                manifest.get("format") != "agencycrm-backup-v1"
                or not isinstance(files, dict)
                or "database.sqlite3" not in files
            ):
                raise ValidationError("Formato de copia no válido.")
            if set(names) != {"manifest.json", *files}:
                raise ValidationError("Contenido distinto del manifiesto.")
            stage = Path(temporary)
            total = 0
            for name, expected in files.items():
                item = archive.getinfo(name)
                mode = item.external_attr >> 16
                if item.is_dir() or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                    raise ValidationError("La copia contiene enlaces o directorios no admitidos.")
                if name == "database.sqlite3":
                    path, limit = stage / name, MAX_DATABASE_BYTES
                elif name.startswith("media/"):
                    path, limit = (
                        private_file_path(stage / "media", name.removeprefix("media/")),
                        2_000_000,
                    )
                else:
                    raise ValidationError("Ruta de copia no válida.")
                if (
                    not isinstance(expected, dict)
                    or type(expected.get("size")) is not int
                    or not 0 <= expected["size"] == item.file_size <= limit
                ):
                    raise ValidationError("Tamaño de archivo no válido.")
                total += item.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ValidationError("La copia excede el límite total.")
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as original, path.open("xb") as target:
                    shutil.copyfileobj(original, target, length=1024 * 1024)
                if digest(path) != expected.get("sha256"):
                    raise ValidationError("La copia no supera la verificación de hash.")
            sqlite_integrity(stage / "database.sqlite3")
            documents = referenced_documents(stage / "database.sqlite3")
            if {f"media/{name}" for name in documents} != set(files) - {"database.sqlite3"}:
                raise ValidationError("Faltan documentos referenciados por la base.")
            if any(files[f"media/{name}"]["sha256"] != sha for name, sha in documents.items()):
                raise ValidationError("Hash de documento distinto de la evidencia de la base.")
            # All checks finish before the operator's new destination is created.
            destination.mkdir()
            shutil.copyfile(stage / "database.sqlite3", destination / "database.sqlite3")
            shutil.copytree(stage / "media", destination / "media") if (
                stage / "media"
            ).exists() else (destination / "media").mkdir()
            return manifest
    except (OSError, sqlite3.Error, zipfile.BadZipFile, KeyError, ValueError, TypeError) as error:
        raise ValidationError("No se pudo validar o restaurar la copia.") from error
