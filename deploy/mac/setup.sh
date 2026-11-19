#!/bin/bash
# Run from a downloaded checkout; no curl | sh bootstrap for project code.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ACTION=${1:-setup}

if [[ "$ACTION" == help || "$ACTION" == -h || "$ACTION" == --help ]]; then
  cat <<'USAGE'
Ordinary Muse Mac worker

  bash deploy/mac/setup.sh                         # interactive install and start
  bash deploy/mac/setup.sh setup --ecs-host HOST --ecs-user USER
  bash deploy/mac/setup.sh start                   # start / enable login startup
  bash deploy/mac/setup.sh status                  # check local and ECS endpoints
  bash deploy/mac/setup.sh logs                    # show recent logs
  bash deploy/mac/setup.sh stop                    # stop / disable login startup
  bash deploy/mac/setup.sh public-key              # show the worker's public key
  bash deploy/mac/setup.sh uninstall               # remove agents; keep data/keys

Setup options:
  --ssh-port 22 --remote-port 19000 --local-port 8000
  --device auto|mps|cpu --identity-file PATH --wait-seconds 120
  --no-authorize   # use a key already authorized on ECS; do not append a public key

Use an existing ECS SSH account. Setup can append its new public key after you
authenticate with an existing SSH key or password. It does not create ECS users,
change sshd/security groups, or switch the ECS H5 upstream.
USAGE
  exit 0
fi

if [[ $(uname -s) != Darwin ]]; then
  echo "此脚本用于 macOS。" >&2
  exit 1
fi
if [[ $EUID -eq 0 ]]; then
  echo "请用登录 Mac 的普通用户运行，不要使用 sudo。" >&2
  exit 1
fi

find_brew() {
  if command -v brew >/dev/null 2>&1; then
    command -v brew
  elif [[ -x /opt/homebrew/bin/brew ]]; then
    echo /opt/homebrew/bin/brew
  elif [[ -x /usr/local/bin/brew ]]; then
    echo /usr/local/bin/brew
  fi
}

BREW_BIN=$(find_brew)
if [[ "$ACTION" == setup ]]; then
  if [[ -z "$BREW_BIN" ]]; then
    echo "未找到 Homebrew；将运行官方安装器，按提示完成安装。"
    INSTALLER=$(mktemp -t ordinary-muse-homebrew)
    trap 'rm -f "$INSTALLER"' EXIT
    /usr/bin/curl --fail --location --show-error \
      https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh \
      --output "$INSTALLER"
    /bin/bash "$INSTALLER"
    rm -f "$INSTALLER"
    trap - EXIT
    BREW_BIN=$(find_brew)
    [[ -n "$BREW_BIN" ]] || { echo "未找到安装后的 Homebrew。" >&2; exit 1; }
  fi
  "$BREW_BIN" install python@3.11 ffmpeg
fi

PYTHON_BIN=""
if [[ -n "$BREW_BIN" ]]; then
  PYTHON_PREFIX=$("$BREW_BIN" --prefix python@3.11 2>/dev/null || true)
  if [[ -x "$PYTHON_PREFIX/bin/python3.11" ]]; then
    PYTHON_BIN="$PYTHON_PREFIX/bin/python3.11"
  fi
fi
if [[ -z "$PYTHON_BIN" ]]; then
  # Management commands don't install dependencies. The controller supports 3.9+.
  PYTHON_BIN=$(command -v python3 || true)
fi
[[ -n "$PYTHON_BIN" ]] || { echo "请先运行 bash deploy/mac/setup.sh setup。" >&2; exit 1; }

if [[ $# -eq 0 ]]; then
  set -- setup
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/worker.py" "$@"
