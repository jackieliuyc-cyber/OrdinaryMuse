#!/usr/bin/env python3
"""Install and manage a per-user macOS Demucs worker and SSH reverse tunnel."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get(
    "ORDINARY_MUSE_STATE_DIR",
    str(Path.home() / "Library/Application Support/OrdinaryMuse/worker"),
)).expanduser().resolve()
AGENTS = Path.home() / "Library/LaunchAgents"
LABELS = {"model": "com.ordinarymuse.worker.model", "tunnel": "com.ordinarymuse.worker.tunnel"}
DOMAIN = f"gui/{os.getuid()}"
SSH = "/usr/bin/ssh"
LAUNCHCTL = "/bin/launchctl"


def run(args, *, capture=False, check=True, **kwargs):
    return subprocess.run(args, text=True, capture_output=capture, check=check, **kwargs)


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_file(path, content, mode=0o600):
    """Atomic writes also work for paths containing spaces, quotes and XML chars."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked():
    private_dir(STATE)
    with (STATE / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("另一个安装或管理命令正在运行，请稍后重试。")
        yield


def validate(config):
    host = config["ecs_host"]
    # IPv4 or DNS name. Never allow ssh options, shell text, or user@host here.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
        raise ValueError("ECS 地址请填写公网 IPv4 或域名，不要包含账号、协议或端口。")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", config["ecs_user"]):
        raise ValueError("ECS SSH 用户名格式不正确。")
    for key in ("ssh_port", "remote_port", "local_port"):
        if type(config[key]) is not int or not 1024 <= config[key] <= 65535:
            if key != "ssh_port" or type(config[key]) is not int or not 1 <= config[key] <= 65535:
                raise ValueError(f"{key} 端口范围不正确（转发端口应为 1024～65535）。")
    if config["remote_port"] == config["ssh_port"]:
        raise ValueError("ECS 转发端口不能与 SSH 端口相同。")
    if config["device"] not in ("auto", "mps", "cpu"):
        raise ValueError("device 必须为 auto、mps 或 cpu。")
    if not 0 <= config["wait_seconds"] <= 1800:
        raise ValueError("wait-seconds 应为 0～1800 秒。")
    return config


def read_config():
    path = STATE / "config.json"
    if not path.exists():
        raise RuntimeError("尚未安装，请先运行 bash deploy/mac/setup.sh setup。")
    return validate(json.loads(path.read_text()))


def setup_config(args):
    config = json.loads((STATE / "config.json").read_text()) if (STATE / "config.json").exists() else {}
    defaults = {"ssh_port": 22, "remote_port": 19000, "local_port": 8000,
                "device": "auto", "wait_seconds": 120,
                "identity_file": str(STATE / "ssh/id_ed25519")}
    for key in ("ecs_host", "ecs_user", *defaults):
        value = getattr(args, key, None)
        if value is not None:
            config[key] = value
        elif key not in config:
            if key in defaults:
                config[key] = defaults[key]
            elif sys.stdin.isatty():
                prompt = "ECS 公网 IPv4 / 域名: " if key == "ecs_host" else "ECS 已存在的 SSH 用户名: "
                config[key] = input(prompt).strip()
            else:
                raise ValueError("非交互运行请提供 --ecs-host 和 --ecs-user。")
    config["project_dir"] = str(ROOT)
    config["identity_file"] = str(Path(config["identity_file"]).expanduser().resolve())
    return validate(config)


def ensure_key(config):
    private_dir(STATE / "ssh")
    key = Path(config["identity_file"])
    if not key.exists():
        if key != STATE / "ssh/id_ed25519":
            raise RuntimeError("--identity-file 指定的私钥不存在；不覆盖或代建外部密钥。")
        run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key),
             "-C", "ordinary-muse-worker"])
    key.chmod(0o600)
    public_path = Path(str(key) + ".pub")
    if not public_path.exists():
        public_key = run(["/usr/bin/ssh-keygen", "-y", "-f", str(key)], capture=True).stdout.strip()
        write_file(public_path, (public_key + "\n").encode(), mode=0o644)
    return public_path.read_text().strip()


def ssh_args(config, *, interactive=False, dedicated=True):
    # -o values are parsed as ssh_config text, even when passed as one argv item.
    # Quote the default "Application Support" path, including any literal quotes.
    known_hosts = str(STATE / "ssh/known_hosts").replace("\\", "\\\\").replace('"', '\\"')
    return [SSH, "-T", "-p", str(config["ssh_port"]), "-i", config["identity_file"],
            "-o", f"BatchMode={'no' if interactive else 'yes'}",
            "-o", f"IdentitiesOnly={'yes' if dedicated else 'no'}",
            "-o", f"StrictHostKeyChecking={'ask' if interactive else 'yes'}",
            "-o", f'UserKnownHostsFile="{known_hosts}"',
            "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ConnectTimeout=15"]


def destination(config):
    return f"{config['ecs_user']}@{config['ecs_host']}"


def authorize(config, public_key, no_authorize=False):
    # A pre-authorized key needs no server mutation. Initial host trust is confirmed
    # by ssh itself; never silently accept host keys or disable verification.
    probe = run(ssh_args(config) + [destination(config), "true"], capture=True, check=False, timeout=25)
    if probe.returncode == 0:
        return
    if no_authorize:
        command = "true"
    else:
        quoted = shlex.quote(public_key)
        command = ('umask 077; mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" && '
                   'touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys" && '
                   f'(grep -qxF -- {quoted} "$HOME/.ssh/authorized_keys" || '
                   f'printf \'%s\\n\' {quoted} >> "$HOME/.ssh/authorized_keys")')
    print("首次连接请核对 ECS 主机指纹；可用原 SSH 密钥或 ECS 账号密码完成公钥授权。", flush=True)
    result = run(ssh_args(config, interactive=True, dedicated=no_authorize) +
                 [destination(config), command], check=False)
    if result.returncode:
        print("\n待授权的公钥（不是私钥）：\n" + public_key, flush=True)
        raise RuntimeError("SSH 授权失败。可通过 ECS 控制台追加此公钥到对应账号的 ~/.ssh/authorized_keys，"
                           "再重新运行 setup。不要上传私钥。")
    result = run(ssh_args(config) + [destination(config), "true"], capture=True, check=False, timeout=25)
    if result.returncode:
        raise RuntimeError("后台 SSH 密钥登录未通过：\n" + result.stderr.strip() +
                           "\n有口令的密钥需要先加入当前用户的 ssh-agent。")


def install_dependencies(config):
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("安装模型环境需要 Python 3.11，请从 setup.sh 运行。")
    python = STATE / "venv/bin/python"
    if not python.exists():
        run([sys.executable, "-m", "venv", str(STATE / "venv")])
    version = run([str(python), "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"], capture=True)
    if version.stdout.strip() != "3.11":
        raise RuntimeError("已有 worker 虚拟环境不是 Python 3.11，请先备份并移走 worker/venv 后重试。")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "-r", str(ROOT / "demucs-server/requirements.txt")])
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        # Homebrew is not necessarily present in a new Terminal's PATH yet.
        for prefix in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            if (prefix / "ffmpeg").is_file():
                ffmpeg = str(prefix / "ffmpeg")
                break
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，请先运行 brew install ffmpeg。")
    config["ffmpeg_dir"] = str(Path(ffmpeg).parent)
    config["cert_file"] = run([str(python), "-c", "import certifi; print(certifi.where())"], capture=True).stdout.strip()


def agent_plists(config):
    python = str(STATE / "venv/bin/python")
    environment = {"HOME": str(Path.home()), "PYTHONUNBUFFERED": "1",
                   "PATH": f"{STATE / 'venv/bin'}:{config['ffmpeg_dir']}:/usr/bin:/bin:/usr/sbin:/sbin",
                   "SEPARATION_ENABLED": "1", "DEMUCS_DEVICE": config["device"],
                   "DEMUCS_MODEL": "htdemucs_ft", "TORCH_HOME": str(STATE / "torch"),
                   "SSL_CERT_FILE": config["cert_file"], "REQUESTS_CA_BUNDLE": config["cert_file"]}
    model = ["/usr/bin/caffeinate", "-i", python, "-m", "uvicorn", "server:app",
             "--host", "127.0.0.1", "--port", str(config["local_port"]), "--workers", "1"]
    tunnel = ssh_args(config) + ["-N", "-o", "ExitOnForwardFailure=yes",
                               "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
                               "-R", f"127.0.0.1:{config['remote_port']}:127.0.0.1:{config['local_port']}",
                               destination(config)]
    plists = {}
    for name, arguments in (("model", model), ("tunnel", tunnel)):
        plists[name] = {"Label": LABELS[name], "ProgramArguments": arguments,
                        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 15,
                        "WorkingDirectory": str(Path(config["project_dir"]) / "demucs-server"),
                        "StandardOutPath": str(STATE / f"logs/{name}.log"),
                        "StandardErrorPath": str(STATE / f"logs/{name}.log")}
    plists["model"]["EnvironmentVariables"] = environment
    return plists


def agent_info(name):
    result = run([LAUNCHCTL, "print", f"{DOMAIN}/{LABELS[name]}"], capture=True, check=False)
    if result.returncode:
        return {"loaded": False, "pid": None, "exit": None}
    pid = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.MULTILINE)
    last_exit = re.search(r"^\s*last exit code = (\d+)\s*$", result.stdout, re.MULTILINE)
    return {"loaded": True, "pid": int(pid[1]) if pid else None,
            "exit": int(last_exit[1]) if last_exit else None}


def start(config):
    if not (STATE / "venv/bin/python").exists():
        raise RuntimeError("模型虚拟环境不存在，请重新运行 setup。")
    server = Path(config["project_dir"]) / "demucs-server/server.py"
    if not server.is_file():
        raise RuntimeError("项目目录已移动；请在新目录重新运行 setup。")
    if not agent_info("model")["loaded"]:
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", config["local_port"]))
        except OSError as error:
            raise RuntimeError(f"本机端口 {config['local_port']} 已被其他程序占用；"
                               "请停止该程序或用 --local-port 重新 setup。") from error
    private_dir(STATE / "logs")
    AGENTS.mkdir(parents=True, exist_ok=True)
    for name, plist in agent_plists(config).items():
        path = AGENTS / f"{LABELS[name]}.plist"
        write_file(path, plistlib.dumps(plist))
        run([LAUNCHCTL, "enable", f"{DOMAIN}/{LABELS[name]}"])
        if not agent_info(name)["loaded"]:
            run([LAUNCHCTL, "bootstrap", DOMAIN, str(path)])
    print("已安装并启动登录代理；模型首次启动会下载权重。", flush=True)


def stop(*, uninstall=False):
    # Only address our two labels, never kill processes by name or port.
    for name in ("tunnel", "model"):
        run([LAUNCHCTL, "disable", f"{DOMAIN}/{LABELS[name]}"], check=False, capture=True)
        if agent_info(name)["loaded"]:
            run([LAUNCHCTL, "bootout", f"{DOMAIN}/{LABELS[name]}"])
        if uninstall:
            (AGENTS / f"{LABELS[name]}.plist").unlink(missing_ok=True)
    print("已停止，登录后也不会自动启动；start 可重新启用。")
    if uninstall:
        print("已移除登录代理，配置、虚拟环境、密钥、模型及任务文件均保留。")


def healthy(payload):
    return (isinstance(payload, dict) and payload.get("status") == "healthy"
            and payload.get("separation_enabled") is not False)


def local_health(config):
    # Ignore user/system HTTP proxies for loopback checks.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{config['local_port']}/api/v1/health", timeout=3) as response:
            return healthy(json.load(response))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def remote_health(config):
    command = ("curl --noproxy '*' --fail --silent --show-error --max-time 8 "
               f"http://127.0.0.1:{config['remote_port']}/api/v1/health")
    try:
        result = run(ssh_args(config) + [destination(config), command], capture=True, check=False, timeout=25)
        return result.returncode == 0 and healthy(json.loads(result.stdout))
    except (subprocess.TimeoutExpired, ValueError):
        return False


def status(config):
    infos = {name: agent_info(name) for name in LABELS}
    local_ok = local_health(config)
    # Don't mistake an unrelated service occupying the ECS port for our tunnel.
    remote_ok = bool(infos["tunnel"]["pid"]) and local_ok and remote_health(config)
    for name, info in infos.items():
        print(f"{name}: loaded={info['loaded']} pid={info['pid']} last_exit={info['exit']}")
    print(f"Mac 分轨健康检查: {'通过' if local_ok else '未通过 / 启动中'}")
    print(f"ECS 经隧道健康检查: {'通过' if remote_ok else '未通过'}")
    print(f"ECS 上游地址: http://127.0.0.1:{config['remote_port']}")
    print(f"日志目录: {STATE / 'logs'}")
    return bool(infos["model"]["pid"] and infos["tunnel"]["pid"] and local_ok and remote_ok)


def wait_ready(config):
    deadline = time.monotonic() + config["wait_seconds"]
    print(f"等待模型就绪，最多 {config['wait_seconds']} 秒；权重下载详情可用 logs 查看。", flush=True)
    while True:
        if local_health(config) and agent_info("tunnel")["pid"] and remote_health(config):
            return status(config)
        if time.monotonic() >= deadline:
            status(config)
            print("尚未验证完成，后台代理仍在运行。请查看 logs，下载完成或排障后再运行 status。")
            return False
        time.sleep(min(3, max(0, deadline - time.monotonic())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="setup",
                        choices=("setup", "start", "status", "logs", "stop", "public-key", "uninstall"))
    parser.add_argument("--ecs-host")
    parser.add_argument("--ecs-user")
    for name in ("ssh-port", "remote-port", "local-port", "wait-seconds"):
        parser.add_argument(f"--{name}", type=int)
    parser.add_argument("--device", choices=("auto", "mps", "cpu"))
    parser.add_argument("--identity-file")
    parser.add_argument("--no-authorize", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or os.geteuid() == 0:
        raise RuntimeError("请在 Mac 上以当前登录的普通用户运行。")
    if args.action == "logs":
        for name in LABELS:
            path = STATE / f"logs/{name}.log"
            print(f"\n{name}: {path}", flush=True)
            if path.exists():
                run(["/usr/bin/tail", "-n", "60", str(path)])
        return 0
    if args.action == "status":
        return 0 if status(read_config()) else 1
    if args.action == "public-key":
        print(Path(read_config()["identity_file"] + ".pub").read_text().strip())
        return 0
    with locked():
        if args.action in ("stop", "uninstall"):
            stop(uninstall=args.action == "uninstall")
            return 0
        if args.action == "setup":
            if any(agent_info(name)["loaded"] for name in LABELS):
                raise RuntimeError("代理已加载。更新配置或依赖前请先运行 stop，避免打断正在处理的任务。")
            config = setup_config(args)
            key = ensure_key(config)
            # Persist settings so that manual authorization and a re-run can resume.
            write_file(STATE / "config.json", (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode())
            authorize(config, key, args.no_authorize)
            install_dependencies(config)
            write_file(STATE / "config.json", (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode())
        else:
            config = read_config()
        start(config)
        return 0 if wait_ready(config) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"错误：{error}", file=sys.stderr)
        sys.exit(1)
