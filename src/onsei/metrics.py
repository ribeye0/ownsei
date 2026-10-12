from __future__ import annotations

import subprocess
import threading
import time


class GpuMetrics:
    def __init__(self):
        self.stop = threading.Event()
        self.data = {
            "baseline_device_used_mb": None,
            "peak_device_used_mb": 0,
            "minimum_device_free_mb": None,
        }
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.started = time.monotonic()

    def sample(self):
        while not self.stop.is_set():
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                used, free = [int(x.strip()) for x in r.stdout.splitlines()[0].split(",")]
                if self.data["baseline_device_used_mb"] is None:
                    self.data["baseline_device_used_mb"] = used
                self.data["peak_device_used_mb"] = max(self.data["peak_device_used_mb"], used)
                self.data["minimum_device_free_mb"] = min(self.data["minimum_device_free_mb"] or free, free)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self.stop.wait(0.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=4)
        self.data["wall_seconds"] = time.monotonic() - self.started
