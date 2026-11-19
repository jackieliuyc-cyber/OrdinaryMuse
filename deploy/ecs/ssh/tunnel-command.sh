#!/bin/sh
# Keep the installed Mac worker's probes working without granting a shell.
# No eval, shell interpolation, or caller-supplied curl arguments.
set -eu
case "${SSH_ORIGINAL_COMMAND-}" in
  true)
    exit 0
    ;;
  "curl --noproxy '*' --fail --silent --show-error --max-time 8 http://127.0.0.1:19000/api/v1/health")
    exec /usr/bin/curl --noproxy '*' --fail --silent --show-error --max-time 8 \
      http://127.0.0.1:19000/api/v1/health
    ;;
  *)
    echo 'This account only supports the Ordinary Muse reverse tunnel and its health check.' >&2
    exit 126
    ;;
esac
