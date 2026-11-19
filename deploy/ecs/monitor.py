"""Sample a benchmark unit's memory cgroup and host swap without loading models."""
import json
import subprocess
import sys
import time
from pathlib import Path

unit, output = sys.argv[1:]
samples = []
for _ in range(200):
    show = subprocess.check_output(["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "ControlGroup", "-p", "ExecMainStatus", "-p", "Result"], text=True)
    state = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
    sample = {"elapsed_seconds": len(samples) * 5, **state}
    group = Path("/sys/fs/cgroup/memory") / state.get("ControlGroup", "").lstrip("/")
    for name in ["memory.usage_in_bytes", "memory.max_usage_in_bytes", "memory.memsw.usage_in_bytes", "memory.memsw.max_usage_in_bytes", "memory.failcnt"]:
        try:
            sample[name] = int((group / name).read_text())
        except (OSError, ValueError):
            pass
    memory = dict((line.split(":", 1)[0], line.split(":", 1)[1].strip()) for line in Path("/proc/meminfo").read_text().splitlines())
    sample["host_memory_available"] = memory["MemAvailable"]
    sample["host_swap_free"] = memory["SwapFree"]
    samples.append(sample)
    Path(output).write_text(json.dumps(samples, indent=2))
    if state.get("SubState") in {"exited", "failed", "dead"}:
        break
    time.sleep(5)
