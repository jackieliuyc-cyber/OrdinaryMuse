# ECS private deployment

Target: `i-bp1dhypbynl7gfsclml5` (`cn-hangzhou`), profile `h5-hangzhou`.
Only loopback listeners: H5 Nginx `127.0.0.1:8782`, extraction API `127.0.0.1:8783`.
No security group, domain or public listener changes are required.

## Private shared-domain paths

The private gateway on `127.0.0.1:8782` now uses these namespaces:

| Path | Target |
| --- | --- |
| `/` | HTTP 302 redirect to `/score/` |
| `/score` / `/audio` | HTTP 308 redirect to the canonical trailing slash, preserving query parameters |
| `/score/…` | Score stitcher static page, module/Worker scripts and sample video |
| `/score/api/feedback` | Existing feedback service on `127.0.0.1:8781/api/feedback` |
| `/audio/…` | Audio studio static page |
| `/audio/api/…` | Existing extraction API on `127.0.0.1:8783/api/…` |

Unprefixed `/api/…` and unknown routes return 404 at this gateway. Redirects are
relative, so a later domain or an SSH-forwarded local port needs no URL change.
The backend's native `/api/v1/…` routes remain available on its loopback port for
the iOS App, Mac worker and health checks. Direct Mac H5 hosting at `/` still works.

`prepare_paths.py` packages the public static directories into a fresh release,
adapting the score form action to `./api/feedback` and its JS fetch to the form's
resolved action. Score sources come from the existing ECS deployment of
`jackieliuyc-cyber/dynamic-score-stitcher/h5/dist`; its other assets are copied intact.
It never edits that source directory and refuses unknown feedback markup.
Audio fetch/XHR/preview/download URLs resolve relative to the page's directory.

`activate_paths.py` runs from an uploaded staging bundle containing `web/`,
`nginx.conf` and `prepare_paths.py`. It requires the current config's SHA-256,
builds `/srv/ordinary-muse-h5/path-releases/<name>`, validates Nginx, backs up the
old config and symlink, then hot-reloads only `ordinary-muse-h5`. Failed readiness
checks restore the prior config and symlink. Active static files are selected by
`/srv/ordinary-muse-h5/paths-current`. Feedback/database/API units are untouched.

The score feedback method, 12 KB body limit and rate limit are preserved; forwarding
directly to the feedback service also preserves the gateway's client IP header.
Neither a domain binding nor an 80/443 listener is enabled by this deployment.

Validation commands:

```bash
node --test demucs-server/tests/paths.test.cjs
python3.11 -m unittest discover -s deploy/ecs/tests -v
# On the ECS: redirects/assets/namespaces/invalid feedback + real video extraction.
python3.11 /path/to/staging/smoke_paths.py
```

The smoke check rejects empty feedback before storage/notification, uses the
existing 30-second sample for extraction, and checks full download, Range preview
and completion-based 24-hour retention. It does not send feedback email or run Demucs.

2026-11-19 private deployment verification is recorded in
[reports/2026-11-19-paths.json](reports/2026-11-19-paths.json). The original eight
tasks and their expiry deadlines were preserved, feedback remained at eight rows,
and both pages were checked in a browser through a temporary read-only local proxy.

## Existing runtime

- Python 3.11 virtual environment: `/srv/ordinary-muse-h5/runtime/venv`.
- Separate CPU model environment: `/srv/ordinary-muse-h5/runtime/model-venv` (PyTorch/Torchaudio 2.1.0+cpu, Demucs 4.0.1).
- FFmpeg/ffprobe: `/srv/ordinary-muse-h5/runtime/bin`.
- Application release: `/srv/ordinary-muse-h5/backend-releases/ecs-20261119`.
- Persistent extraction tasks and outputs: `/var/lib/ordinary-muse`.
- Model test input/output/logs: `/srv/ordinary-muse-h5/benchmarks`.
- Model cache: `/srv/ordinary-muse-h5/runtime/cache` (`TORCH_HOME`).

Install the lightweight `requirements-extraction.txt` and set `SEPARATION_ENABLED=0`
to operate video extraction without loading PyTorch or model weights. The API
reports capabilities; unavailable separation endpoints return HTTP 503. Model
tests run as separate, time-limited systemd units with CPU and memory limits.
Do not enable permanent model serving until measurements justify it.

`ordinary-muse-api.service` configures 512 MB memory and one CPU for extraction;
`nginx.conf` preserves the private endpoint and proxies the existing API routes.
The server supports a configurable `MUSE_DATA_DIR` outside its release directory.

`benchmark.py` runs the actual project model, saves four WAV and M4A stems, and
records wall time, CPU time, peak RSS and major page faults. Also collect the
unit's memory cgroup peak, swap usage, exit status, and existing service health.
Short-clip throughput is not a measured full-song completion time.
