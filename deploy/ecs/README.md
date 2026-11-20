# ECS private deployment

Target: `i-bp1dhypbynl7gfsclml5` (`cn-hangzhou`), profile `h5-hangzhou`.
Only loopback listeners: H5 Nginx `127.0.0.1:8782`, extraction API `127.0.0.1:8783`.
No security group, domain or public listener changes are required.

## Current-device private preview

`private_preview.py` provides a local-only H5 entry over the existing Workbench
profile when a direct SSH local forward is unavailable. It binds exclusively to
`127.0.0.1`; it does not change ECS listeners, SSH permissions or security groups.
Python 3.11 and an already authenticated Workbench CLI are required on this Mac;
Python 3.11 and the existing private H5 gateway are required on ECS.

```bash
python3.11 deploy/ecs/private_preview.py --profile h5-hangzhou
# Open http://127.0.0.1:18882/audio/
```

The instance, region, profile, local/gateway ports, CLI path, local cache and
remote scratch directory are configurable with `--help`. The default local cache
is `~/.cache/ordinary-muse/private-preview` (0700). Each run creates a unique
scratch directory under `/srv/ordinary-muse-h5/preview-cache/` on ECS. Authentication
uses the existing Workbench profile; credentials are never copied into the repo.

The preview keeps running until explicitly stopped; it has no 30-minute idle
exit. For continued use after closing the Terminal, start it in the background:

```bash
nohup python3.11 deploy/ecs/private_preview.py > /tmp/ordinary-muse-preview.log 2>&1 &
```

Restart it after rebooting this Mac. Static assets are cached per URL, so restart
the preview or use a new asset/page version query after deploying changed files.
Media is downloaded completely once, then full, seek, suffix and HEAD requests
are served locally. Cached playback is independent of the Workbench transport
lock, preventing a second player's network request from blocking the first.
Cache access stops at the task's recorded `expires_at`; the cache is a local
download copy, not the model's task store. After stopping the preview, local media
copies can be removed from `--cache-dir`. Remote temporary response files are
removed after successful transfers. Do not run other Workbench commands against
the same shared session during an uncached transfer.

Regression checks (no ECS credentials or model required):

```bash
python3.11 -m unittest discover -s deploy/ecs/tests -v
```

The dedicated, key-only Mac reverse-tunnel account and its restrictions are
documented in [ssh/README.md](ssh/README.md). It only permits remote forwarding to
`127.0.0.1:19000`; the new Mac public key is still pending authorization.

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
