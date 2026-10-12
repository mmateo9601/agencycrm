"""One OS-held lock per local database; process exit releases it automatically."""

import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django.conf import settings


class WorkerAlreadyRunning(Exception):
    """Another worker or recovery operation holds the database's local lock."""


def worker_lock_path() -> Path:
    """Canonicalize the database path so all entry points share the same lock."""
    db = settings.DATABASES["default"]
    name = str(db["NAME"])
    memory = name == ":memory:" or (
        name.startswith("file:")
        and (":memory:" in name or parse_qs(urlsplit(name).query).get("mode") == ["memory"])
    )
    if db["ENGINE"] == "django.db.backends.sqlite3" and not memory:
        return Path(f"{Path(db['NAME']).resolve()}.worker.lock")
    return Path(settings.BASE_DIR) / "agencycrm.worker.lock"


class WorkerLock:
    def __init__(self, path: Path | None = None):
        self.path = path or worker_lock_path()
        self.file = None
        self.acquired = False

    def __enter__(self):
        self.file = self.path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise WorkerAlreadyRunning("Ya hay un worker o recuperación en ejecución.") from exc
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.acquired = False
            self.file.close()
            self.file = None
