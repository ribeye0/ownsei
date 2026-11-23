"""Capacity checks for model files and isolated Python installations."""

from pathlib import Path
import shutil

GIB = 1024**3
RESERVE = 512 * 1024**2


class InsufficientSpace(ValueError):
    pass


def existing_directory(path):
    directory = Path(path).resolve()
    while not directory.is_dir():
        if directory == directory.parent:
            raise FileNotFoundError(directory)
        directory = directory.parent
    return directory


def capacity_message(path, needed=None):
    directory = existing_directory(path)
    free = shutil.disk_usage(directory).free
    available = f"{free / GIB:.2f} GiB" if free >= GIB else f"{free / 1024**2:.0f} MiB"
    estimate = f"，此次準備預估至少需要 {needed / GIB:.1f} GiB 可用空間" if needed is not None else ""
    return f"磁碟空間不足：{directory} 所在磁碟只剩 {available}{estimate}。請清理空間後重試；已下載的檔案會保留供續傳。"


def require_space(requirements):
    """Add requirements on the same filesystem, including a small write reserve."""
    volumes = {}
    for path, size in requirements:
        if size <= 0:
            continue
        directory = existing_directory(path)
        volume = volumes.setdefault(directory.stat().st_dev, [directory, 0])
        volume[1] += size
    for directory, size in volumes.values():
        needed = size + RESERVE
        if shutil.disk_usage(directory).free < needed:
            raise InsufficientSpace(capacity_message(directory, needed))
