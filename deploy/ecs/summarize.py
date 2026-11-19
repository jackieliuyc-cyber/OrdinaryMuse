"""Create an auditable summary only after the fixed-limit benchmark ends."""
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

root = Path("/srv/ordinary-muse-h5/benchmarks")
unit = "muse-benchmark-30-v2.service"
show = subprocess.check_output(["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "Result", "-p", "ExecMainStatus", "-p", "CPUUsageNSec"], text=True)
state = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
assert state["SubState"] != "running", "Benchmark has not ended"
run = json.loads((root / "run-30s-1600/result.json").read_text())
if run["status"] == "running" and state.get("Result") != "success":
    run.update(status=state["Result"], note="Worker was terminated by systemd before it could save a final result.")
samples = json.loads((root / "run-30s-1600/resources.json").read_text())
samples = [sample for sample in samples if sample.get("ControlGroup", "").endswith(unit)]
swap_total = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("SwapTotal:")))
resources = {
    "peak_ram_bytes": max(sample.get("memory.max_usage_in_bytes", 0) for sample in samples),
    "peak_ram_plus_swap_bytes": max(sample.get("memory.memsw.max_usage_in_bytes", 0) for sample in samples),
    "sampled_peak_process_swap_bytes": max(sample.get("memory.memsw.usage_in_bytes", 0) - sample.get("memory.usage_in_bytes", 0) for sample in samples),
    "sampled_peak_host_swap_kb": swap_total - min(int(sample["host_swap_free"].split()[0]) for sample in samples),
    "memory_limit_hits": max(sample.get("memory.failcnt", 0) for sample in samples),
}
output = root / "run-30s-1600/stems"
files = sorted(path.name for path in output.glob("*"))
if run["status"] == "completed":
    assert all((output / (stem + extension)).is_file() for stem in ["vocals", "drums", "bass", "other"] for extension in [".wav", ".m4a"])
    for path in output.glob("*"):
        probe = json.loads(subprocess.check_output(["/srv/ordinary-muse-h5/runtime/bin/ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]))
        assert abs(float(probe["format"]["duration"]) - 30) < .2, path
with urllib.request.urlopen("http://127.0.0.1:8782/api/v1/health", timeout=10) as response:
    health = json.load(response)
summary = {
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "input_seconds": 30, "model": "htdemucs_ft", "cpu_quota_percent": 150,
    "threads": 2, "memory_limit_mib": 1600, "timeout_seconds": 900,
    "unit": state, "run": run, "resources": resources, "output_files": files,
    "extraction_baseline": json.loads((root / "extraction-baseline.json").read_text()),
    "extraction_during_diagnostic_run": json.loads((root / "extraction-during-model.json").read_text()),
    "extraction_health": health,
    "services": subprocess.check_output(["systemctl", "is-active", "ordinary-muse-api", "ordinary-muse-h5", "dynamic-score-h5", "dynamic-score-feedback"], text=True).splitlines(),
    "listeners": subprocess.check_output(["ss", "-lntH"], text=True).splitlines(),
    "initial_diagnostic_run": json.loads((root / "run-30s/result.json").read_text()),
}
(root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
print(json.dumps(summary, ensure_ascii=False))
