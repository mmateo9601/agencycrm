"""Local private-file boundary, independent of document rendering and HTTP."""

from pathlib import Path, PurePosixPath
from uuid import UUID

from django.core.exceptions import ValidationError


def private_file_path(root: Path, name: str) -> Path:
    """Accept generated UUID PDF paths only; reject symlinks and Windows junctions."""
    parts = PurePosixPath(name).parts
    try:
        if len(parts) != 4 or parts[0] not in {"proposals", "sales_assets"} or "\\" in name:
            raise ValueError
        UUID(parts[1])
        UUID(parts[2])
        if Path(parts[3]).suffix != ".pdf":
            raise ValueError
        UUID(Path(parts[3]).stem)
        root = Path(root).absolute()
        current = root
        for part in ("", *parts):
            current = current / part
            if current.is_symlink() or current.is_junction():
                raise ValueError
        resolved_root = root.resolve()
        resolved = current.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise ValueError
        return resolved
    except (ValueError, OSError) as error:
        raise ValidationError("Ruta de archivo privado no válida.") from error
