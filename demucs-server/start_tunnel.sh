#!/bin/bash
set -euo pipefail

PROVIDER=${1:-${TUNNEL_PROVIDER:-cloudflared}}
SERVER_HOST=${SERVER_HOST:-"127.0.0.1"}
SERVER_PORT=${SERVER_PORT:-"8000"}
PYTHON_BIN=${PYTHON_BIN:-"python"}
LOCAL_URL="http://${SERVER_HOST}:${SERVER_PORT}"

print_usage() {
  echo "Usage:"
  echo "  ./start_tunnel.sh cloudflared"
  echo "  ./start_tunnel.sh ngrok"
  echo "  ./start_tunnel.sh oray"
  echo
  echo "Environment:"
  echo "  PYTHON_BIN=python"
  echo "  SERVER_PORT=8000"
  echo "  DEMUCS_MODEL=htdemucs_ft"
  echo "  DEMUCS_DEVICE=auto"
}

ensure_python_deps() {
  if ! "$PYTHON_BIN" -c "import uvicorn" >/dev/null 2>&1; then
    echo "uvicorn is not installed in the active Python environment."
    echo "Run:"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
  fi
}

start_server() {
  ensure_python_deps

  echo "=========================================="
  echo " Ordinary Muse Demucs Tunnel"
  echo " Provider: $PROVIDER"
  echo " Local:    $LOCAL_URL"
  echo "=========================================="

  "$PYTHON_BIN" -m uvicorn server:app \
    --host "$SERVER_HOST" \
    --port "$SERVER_PORT" \
    --workers 1 \
    --log-level info &

  SERVER_PID=$!
  trap 'kill "$SERVER_PID" >/dev/null 2>&1 || true' EXIT
  sleep 3
}

case "$PROVIDER" in
  cloudflared)
    if ! command -v cloudflared >/dev/null 2>&1; then
      echo "cloudflared is not installed."
      echo "Install:"
      echo "  brew install cloudflared"
      exit 1
    fi
    start_server
    echo "Copy the https://*.trycloudflare.com URL into the app's server settings."
    cloudflared tunnel --url "$LOCAL_URL"
    ;;

  ngrok)
    if ! command -v ngrok >/dev/null 2>&1; then
      echo "ngrok is not installed."
      echo "Install:"
      echo "  brew install ngrok/ngrok/ngrok"
      exit 1
    fi
    start_server
    echo "Copy the forwarding https://*.ngrok-free.app URL into the app's server settings."
    ngrok http "$LOCAL_URL"
    ;;

  oray|peanuthull|"花生壳")
    start_server
    echo "Open the Oray/Peanuthull client and create an HTTP/HTTPS mapping:"
    echo "  Internal host: $SERVER_HOST"
    echo "  Internal port: $SERVER_PORT"
    echo "Then copy the generated public URL into the app's server settings."
    wait "$SERVER_PID"
    ;;

  help|-h|--help)
    print_usage
    ;;

  *)
    echo "Unknown provider: $PROVIDER"
    print_usage
    exit 1
    ;;
esac
