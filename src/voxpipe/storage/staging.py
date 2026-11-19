"""Safe cleanup helpers for private local staging references."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from uuid import UUID


def remove_staged_source(
    staging_root: str | Path,
    tenant_id: str,
    session_id: str,
    reference: str | None,
    *,
    include_temporary: bool = False,
) -> bool:
    """Remove one staging entry without following symlinks.

    Temporary upload parts are removed only after the caller confirms that no
    unexpired upload lease exists for this tenant/session.
    """
    tenant = str(UUID(tenant_id))
    session = str(UUID(session_id))
    if not isinstance(reference, str) or not reference or "\\" in reference:
        raise ValueError("staging reference is invalid")
    pure = PurePosixPath(reference)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("staging reference is invalid")
    scoped = pure.parts == (tenant, session, "source")
    legacy = len(pure.parts) == 1
    if not scoped and not legacy:
        raise ValueError("staging reference does not match tenant/session scope")

    configured_root = Path(staging_root).expanduser()
    if configured_root.is_symlink():
        raise ValueError("staging root must not be a symbolic link")
    root = configured_root.resolve(strict=True)
    tenant_dir = root / tenant if scoped else root
    session_dir = tenant_dir / session if scoped else root
    for directory in (tenant_dir, session_dir):
        if directory.is_symlink():
            raise ValueError("staging directory must not be a symbolic link")
        if directory.exists() and not directory.resolve(strict=True).is_relative_to(root):
            raise ValueError("staging directory escaped the configured root")

    target = session_dir / "source" if scoped else root / pure.parts[0]
    removed = False
    if target.is_symlink():
        raise ValueError("staged source must not be a symbolic link")
    if target.exists():
        if not target.is_file():
            raise ValueError("staged source must be a regular file")
        target.unlink()
        removed = True

    if scoped and include_temporary and session_dir.is_dir():
        for temporary in session_dir.glob(".upload-*.part"):
            if temporary.is_symlink() or not temporary.is_file():
                raise ValueError("temporary staging entry is not a regular file")
            temporary.unlink()
            removed = True

    if scoped and session_dir.is_dir():
        try:
            session_dir.rmdir()
        except OSError:
            pass
    return removed
