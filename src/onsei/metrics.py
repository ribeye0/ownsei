"""Best-effort system GPU memory observations; absence never blocks inference."""

from __future__ import annotations
import subprocess
import threading
import time
from pathlib import Path
from onsei import backend


class GpuMetrics:
    def __init__(self):
        self.stop = threading.Event()
        self.data = {"devices": {}, "scope": "system_device_memory_including_other_applications"}
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.started = time.monotonic()
        self.selected = set()
        for engine in ("speech", "llama"):
            try:
                identity = backend.device(engine)
                row = next(d for d in backend.devices(engine) if d["id"] == identity)
                self.selected.add(row["name"].split(" (")[0].split(",")[0])
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

    def observe(self, name, used, free):
        value = self.data["devices"].setdefault(
            name,
            {"baseline_device_used_mb": used, "peak_device_used_mb": used, "minimum_device_free_mb": free},
        )
        value["peak_device_used_mb"] = max(value["peak_device_used_mb"], used)
        value["minimum_device_free_mb"] = min(value["minimum_device_free_mb"], free)
        if any(selected in name or name in selected for selected in self.selected):
            self.data["peak_device_used_mb"] = max(self.data.get("peak_device_used_mb", 0), used)

    def sample(self):
        while not self.stop.is_set():
            try:
                for card in Path("/sys/class/drm").glob("card[0-9]*/device"):
                    used = int((card / "mem_info_vram_used").read_text()) // 1024**2
                    total = int((card / "mem_info_vram_total").read_text()) // 1024**2
                    # sysfs lacks a stable marketing name; keep PCI identity rather than guessing a Vulkan ordinal.
                    self.observe(str(card.resolve()), used, max(0, total - used))
            except (OSError, ValueError):
                pass
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,memory.used,memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                for row in result.stdout.splitlines():
                    name, used, free = row.rsplit(",", 2)
                    self.observe(name.strip(), int(used.strip()), int(free.strip()))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self.stop.wait(1)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=4)
        self.data["wall_seconds"] = time.monotonic() - self.started
