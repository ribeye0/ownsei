"""Process identity, ownership and process-group shutdown on Linux and Windows."""

import os
import subprocess
import signal
import sys
import psutil
from pathlib import Path


def process_identity(pid):
    if sys.platform == "win32":
        try:
            return "win:" + str(psutil.Process(int(pid)).create_time())
        except (psutil.Error, ValueError):
            return None
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = raw[raw.rfind(")") + 2 :].split()
        if fields[0] == "Z":
            return None
        return fields[19]  # Kernel start time, stable across command line changes.
    except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
        return None


def alive(job):
    pid = job.get("pid")
    if not pid:
        return False
    if job.get("process_identity"):
        return process_identity(pid) == job["process_identity"]
    if sys.platform == "win32":
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)  # Legacy records: retain liveness, never authorize a kill from PID alone.
        return process_identity(pid) is not None
    except ProcessLookupError:
        return False


def process_owned(job, arguments, cwd=None):
    if not alive(job):
        return False
    if sys.platform == "win32":
        if not job.get("process_identity"):
            return False
        try:
            process = psutil.Process(job["pid"])
            return all(str(arg) in process.cmdline() for arg in arguments) and (
                cwd is None or Path(process.cwd()).resolve() == Path(cwd).resolve()
            )
        except psutil.Error:
            return False
    try:
        args = Path(f"/proc/{job['pid']}/cmdline").read_bytes().split(b"\0")
        return (
            all(os.fsencode(str(arg)) in args for arg in arguments)
            and os.getpgid(job["pid"]) == job["pid"]
            and (cwd is None or Path(f"/proc/{job['pid']}/cwd").resolve() == Path(cwd).resolve())
        )
    except (OSError, ValueError):
        return False


def runner_owned(job, project):
    return process_owned(job, ["onsei.runner", str(project), job["id"]])


def spawn_options():
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


def signal_group(pid, sig):
    if sys.platform != "win32":
        os.killpg(pid, sig)
    else:
        if sig == signal.SIGTERM:
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
                return
            except OSError:
                pass  # Headless Windows sessions may not have a console.
        try:
            parent = psutil.Process(pid)
            for child in reversed(parent.children(recursive=True)):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
        except psutil.NoSuchProcess:
            pass


def stop_process(process, timeout=5):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
