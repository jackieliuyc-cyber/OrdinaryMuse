#!/bin/bash
export DEMUCS_MODEL=${DEMUCS_MODEL:-"htdemucs_ft"}
export DEMUCS_DEVICE=${DEMUCS_DEVICE:-"auto"}
export SERVER_PORT=${SERVER_PORT:-"8000"}

echo "=========================================="
echo " Demucs Stem Separation Server"
echo " Model:  $DEMUCS_MODEL"
echo " Device: $DEMUCS_DEVICE"
echo " Port:   $SERVER_PORT"
echo "=========================================="

PYTHON_BIN=${PYTHON_BIN:-"python"}

if ! $PYTHON_BIN -c "import uvicorn" >/dev/null 2>&1; then
  echo "uvicorn is not installed in the active Python environment."
  echo "Run:"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required for MP3/M4A/FLAC input decoding."
  echo "Install it with:"
  echo "  brew install ffmpeg"
  exit 1
fi

CERT_FILE=$($PYTHON_BIN -c "import certifi; print(certifi.where())" 2>/dev/null)
if [ -n "$CERT_FILE" ]; then
  export SSL_CERT_FILE=${SSL_CERT_FILE:-"$CERT_FILE"}
  export REQUESTS_CA_BUNDLE=${REQUESTS_CA_BUNDLE:-"$CERT_FILE"}
fi

$PYTHON_BIN -m uvicorn server:app --host 0.0.0.0 --port $SERVER_PORT --workers 1 --log-level info
