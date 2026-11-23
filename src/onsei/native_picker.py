"""Local desktop file dialogs. No upload or custom filesystem browser."""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Literal

from onsei.audio import FORMATS
from onsei.util import ROOT

_dialog = threading.Lock()


def choose_path(kind: Literal["file", "directory"], initial: str = "") -> str | None:
    if kind not in {"file", "directory"}:
        raise ValueError("Unknown selection kind")
    if sys.platform == "win32":
        return choose_windows_path(kind, initial)
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise ValueError("目前服務沒有桌面工作階段；請在本機桌面啟動 Ownsei，或直接輸入路徑。")
    kde = "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    program = (shutil.which("kdialog") if kde else None) or shutil.which("zenity") or shutil.which("kdialog")
    if not program:
        raise ValueError("找不到系統檔案選擇器，請安裝 zenity 或 kdialog；也可直接輸入路徑。")
    start = Path(initial).expanduser() if initial else Path.home()
    if not start.is_absolute() or not start.exists():
        start = Path.home()
    title = "Ownsei — 選擇資料夾" if kind == "directory" else "Ownsei — 選擇音檔"
    patterns = " ".join(f"*{suffix} *{suffix.upper()}" for suffix in sorted(FORMATS))
    if Path(program).name == "kdialog":
        args = [program, "--title", title]
        args += (
            ["--getexistingdirectory", str(start)]
            if kind == "directory"
            else ["--getopenfilename", str(start), f"{patterns}|音訊檔案"]
        )
    else:
        args = [
            program,
            "--file-selection",
            "--title=" + title,
            "--filename=" + str(start) + ("/" if start.is_dir() else ""),
        ]
        args += ["--directory"] if kind == "directory" else ["--file-filter=音訊檔案 | " + patterns]
    if not _dialog.acquire(blocking=False):
        raise ValueError("檔案選擇器已開啟，請先完成或取消目前的選擇。")
    try:
        # Inherit the desktop's D-Bus/display environment so GTK/KDE uses its
        # configured desktop integration. Never invoke a shell with user paths.
        result = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if result.returncode == 1:  # User cancelled.
            return None
        if result.returncode != 0:
            raise ValueError("無法開啟系統檔案選擇器，請確認服務由本機桌面啟動，或直接輸入路徑。")
        path = Path(result.stdout.removesuffix("\n"))
        if not path.is_absolute() or not path.exists():
            raise ValueError("選擇的路徑不存在。")
        if kind == "directory" and not path.is_dir():
            raise ValueError("請選擇資料夾。")
        if kind == "file" and (not path.is_file() or path.suffix.lower() not in FORMATS):
            raise ValueError("請選擇支援的音訊檔案。")
        return str(path)
    except OSError as error:
        raise ValueError("無法啟動系統檔案選擇器，請確認桌面環境及 zenity／kdialog 安裝。") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("檔案選擇已逾時，請重新開啟。") from error
    finally:
        _dialog.release()


def choose_windows_path(kind, initial):
    if not _dialog.acquire(blocking=False):
        raise ValueError("檔案選擇器已開啟，請先完成或取消目前的選擇。")
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-STA",
                "-File",
                str(ROOT / "scripts/choose_path.ps1"),
                "-Kind",
                kind,
                "-Initial",
                initial,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        if result.returncode != 0:
            raise ValueError("無法開啟 Windows 檔案選擇器，也可直接輸入路徑。")
        value = result.stdout.removesuffix("\n").removesuffix("\r")
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute() or not path.exists():
            raise ValueError("選擇的路徑不存在。")
        if kind == "directory" and not path.is_dir():
            raise ValueError("請選擇資料夾。")
        if kind == "file" and (not path.is_file() or path.suffix.lower() not in FORMATS):
            raise ValueError("請選擇支援的音訊檔案。")
        return str(path)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("無法完成 Windows 檔案選擇，請重新開啟或直接輸入路徑。") from error
    finally:
        _dialog.release()
