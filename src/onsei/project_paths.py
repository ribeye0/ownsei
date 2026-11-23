"""Resolve index entries independently of the installation directory."""

from pathlib import Path, PurePosixPath, PureWindowsPath


def project_location(workspace: Path, identity: str, saved: str) -> Path:
    path = Path(saved)
    local = workspace / identity
    safe_id = bool(identity) and identity not in (".", "..") and PureWindowsPath(identity).name == identity
    if safe_id and saved == identity:
        return local
    # Before relative entries, an automatically created project always ended
    # in workspace/<registry ID>. Prefer the directory accompanying this index.
    original = PureWindowsPath(saved) if PureWindowsPath(saved).drive else PurePosixPath(saved)
    if (
        safe_id
        and original.is_absolute()
        and original.name == identity
        and original.parent.name == "workspace"
    ):
        return local
    if not path.is_absolute():
        raise ValueError(f"無法開啟此外部專案路徑：{saved}")
    return path
