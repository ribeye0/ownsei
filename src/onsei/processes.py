"""Bounded subprocess shutdown and Linux process identity checks."""

import os
import subprocess
from pathlib import Path


def process_identity(pid):
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
    try:
        os.kill(pid, 0)  # Legacy records: retain liveness, never authorize a kill from PID alone.
        return process_identity(pid) is not None
    except ProcessLookupError:
        return False


def runner_owned(job, project):
    if not alive(job):
        return False
    try:
        args = Path(f"/proc/{job['pid']}/cmdline").read_bytes().split(b"\0")
        return (
            b"onsei.runner" in args
            and os.fsencode(str(project)) in args
            and os.fsencode(job["id"]) in args
            and os.getpgid(job["pid"]) == job["pid"]
        )
    except (FileNotFoundError, ProcessLookupError):
        return False


def stop_process(process, timeout=5):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
