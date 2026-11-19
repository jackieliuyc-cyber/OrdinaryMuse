"""Run the actual project pipeline under a systemd resource envelope."""
import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("input")
parser.add_argument("output")
parser.add_argument("--threads", type=int, default=1)
args = parser.parse_args()
sys.path.insert(0, "/srv/ordinary-muse-h5/backend-releases/ecs-20261119")
out = Path(args.output)
out.mkdir(parents=True, exist_ok=True)
start = time.monotonic()
result = {"model": os.getenv("DEMUCS_MODEL", "htdemucs_ft"), "threads": args.threads,
          "input": args.input, "status": "running"}
(out / "result.json").write_text(json.dumps(result))
try:
    import torch
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    from separator import separator
    loading = time.monotonic()
    separator.load_model()
    result["model_load_seconds"] = time.monotonic() - loading
    processing = time.monotonic()
    stems = separator.separate(args.input, str(out / "stems"))
    result["separate_seconds"] = time.monotonic() - processing
    encoding = time.monotonic()
    separator.encode_aac_stems(stems)
    result["encode_seconds"] = time.monotonic() - encoding
    result["status"] = "completed"
except Exception as error:
    import traceback
    traceback.print_exc()
    result.update(status="failed", error=str(error))
finally:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result.update(total_seconds=time.monotonic() - start, peak_rss_kb=usage.ru_maxrss,
                  cpu_seconds=usage.ru_utime + usage.ru_stime,
                  major_page_faults=usage.ru_majflt)
    (out / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
