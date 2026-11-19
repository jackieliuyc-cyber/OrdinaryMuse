"""Check actual private HTTP extraction and download, without user browser state."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

base = "http://127.0.0.1:8782/audio"
sample = Path("/srv/ordinary-muse-h5/benchmarks/video-30s.mp4")
results = []
for format in ["m4a", "wav", "aiff", "caf"]:
    started = time.monotonic()
    task = json.loads(subprocess.check_output(["curl", "-fsS", "--max-time", "60", "-F", "file=@" + str(sample), base + "/api/v1/extract/async?format=" + format]))
    url = base + "/api/v1/task/" + task["task_id"]
    for _ in range(120):
        with urllib.request.urlopen(url, timeout=10) as response:
            info = json.load(response)
        if info["status"] in {"completed", "failed"}:
            break
        time.sleep(.5)
    assert info["status"] == "completed", info
    with urllib.request.urlopen(url + "/audio", timeout=30) as response:
        audio = response.read()
        assert response.status == 200 and len(audio) > 100
    with urllib.request.urlopen(urllib.request.Request(url + "/audio?preview=true", headers={"Range": "bytes=0-31"}), timeout=10) as response:
        assert response.status == 206 and len(response.read()) == 32
    from datetime import datetime
    assert (datetime.fromisoformat(info["expires_at"]) - datetime.fromisoformat(info["completed_at"])).total_seconds() == 86400
    results.append({"format": format, "task_id": task["task_id"], "processing_seconds": info["processing_time"], "total_seconds": time.monotonic() - started, "download_bytes": len(audio)})
Path("/srv/ordinary-muse-h5/benchmarks/extraction-results.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results))
