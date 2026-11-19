# Mac 分轨执行机：安装与 ECS 反向隧道

在新的 Mac 上下载本仓库的 `codex/h5-version` 分支，解压后用终端进入项目根目录。
脚本路径不依赖开发者用户名，项目目录包含空格也可以运行。建议把项目放在用户主目录下的
`Projects` 等固定位置，避免登录代理受到“下载/桌面/文稿”目录的 macOS 隐私权限限制。

## 一键安装

```bash
bash deploy/mac/setup.sh
```

首次按提示填写 **ECS 公网 IPv4 或域名**、**已存在的 SSH 用户名**。
也可以直接传参（把示例地址与账号换成实际值）：

```bash
bash deploy/mac/setup.sh setup \
  --ecs-host ecs.example.com \
  --ecs-user muse-tunnel
```

脚本会：

1. 检查 Homebrew，缺少时运行官方安装器；安装 Python 3.11 和 FFmpeg。
   Homebrew 首次安装可能要求确认、管理员密码或安装 Xcode Command Line Tools。
2. 生成此执行机专用的 Ed25519 密钥，不覆盖已有 SSH 密钥。
3. 通过已有 SSH 密钥或 ECS 账号密码登录，把新公钥追加到该账号的 `authorized_keys`，保留已有条目。
   首次连接由 SSH 提示核对服务器指纹；脚本不会自动信任陌生主机。
4. 创建独立 Python 虚拟环境，安装项目锁定版本的 Demucs/PyTorch 依赖。
5. 安装两个当前用户的 LaunchAgent：模型服务和 SSH 隧道。首次启动下载模型权重。
6. 检查 Mac 的模型服务，以及从 ECS 经隧道访问模型服务的健康状态。

请以 **Mac 普通登录用户**运行，不要给整个脚本加 `sudo`。模型环境可用于 Apple Silicon / Intel Mac；
`auto` 优先使用可用的 MPS，否则使用 CPU，实际速度以该机器实测为准。Homebrew 能否安装取决于其对当前 macOS / 硬件的支持。

ECS 必须允许此 Mac 连接 SSH 端口，该账号需允许 TCP 远程转发及执行健康检查命令，服务器需有 `curl`。
推荐使用独立账号。脚本 **不创建 ECS 账号、不修改安全组和 sshd 配置，也不开放新的公网端口**。
如果只有阿里云控制台登录权限、SSH 禁用密码登录或没有可用旧密钥，首次授权会失败并显示公钥。
通过控制台把该公钥追加到对应账号的 `~/.ssh/authorized_keys`，再重新运行同一命令即可继续。

如果公钥已事先授权，使用 `--no-authorize` 避免追加公钥。
也支持 `--identity-file /绝对路径/已有私钥`；文件必须存在，有口令的密钥需先加入用户的 ssh-agent，
后台进程无法交互输入密码。默认生成的专用私钥不设口令，仅当前用户可读。

本项目 ECS 已配置专用 `muse-tunnel` 账号，公钥由 root 管理，并限制为反向隧道和固定健康检查命令。
使用该账号时，应先把新 Mac 公钥交给 ECS 管理员授权，再添加 `--no-authorize` 运行本脚本。
已有密钥还需用 `--identity-file` 指向对应私钥；安装脚本默认生成的 worker 密钥可以省略该参数。
该账号不接受脚本自行追加公钥，详细服务端权限见 [ECS SSH 说明](../ecs/ssh/README.md)。

## 连接与端口

```text
Mac 主动连接 ECS 的 SSH 端口（默认 22）
ECS 127.0.0.1:19000  ──加密隧道──>  Mac 127.0.0.1:8000
```

模型服务和 ECS 转发入口均只绑定 `127.0.0.1`，无需花生壳、Mac 公网 IP 或路由器端口映射。
不要在 ECS 安全组开放 `19000`。默认 `8000` 如被其他服务占用，脚本会报错，不会停止那个服务。

自定义端口与计算设备：

```bash
bash deploy/mac/setup.sh setup \
  --ecs-host ecs.example.com --ecs-user muse-tunnel \
  --ssh-port 22 --remote-port 19000 --local-port 8001 --device auto
```

同一台 ECS 上的不同 Mac 必须使用不同的 `--remote-port`。当前 H5 尚无多执行机调度机制，
多个隧道不会自动分担任务。

## 管理命令

```bash
bash deploy/mac/setup.sh status       # 代理 PID、本机健康、ECS 经隧道健康；全通过才退出 0
bash deploy/mac/setup.sh logs         # 每个服务最近 60 行日志
bash deploy/mac/setup.sh stop         # 停止并禁用下次登录启动
bash deploy/mac/setup.sh start        # 启动并重新启用登录启动
bash deploy/mac/setup.sh public-key   # 仅输出公钥
bash deploy/mac/setup.sh uninstall    # 停止并删除两个 LaunchAgent，保留数据及密钥
```

安装后可关闭终端。当前用户登录后会自动启动，进程退出后最多等待约 15 秒再尝试重启；
隧道检测断线后也通过此机制重连。模型运行期间用 `caffeinate -i` 阻止空闲睡眠，
但合盖、手动睡眠、断网、退出登录或关机仍会中断服务。这是 **登录后自动启动**，不包含无人登录的系统守护进程。

首次下载模型可能超过默认 120 秒等待期：命令会非零退出并明确提示未验证完成，后台代理继续运行。
查看 `logs`，之后用 `status` 验证，不需要重复安装。可用 `--wait-seconds 600` 延长等待。
修改配置、移动项目或更新依赖前先 `stop`，再从新目录运行 `setup`；这会中断正在进行的任务，请先等待任务完成。

配置、虚拟环境、专用 SSH 密钥、模型缓存和日志位于：

```text
~/Library/Application Support/OrdinaryMuse/worker/
~/Library/LaunchAgents/com.ordinarymuse.worker.model.plist
~/Library/LaunchAgents/com.ordinarymuse.worker.tunnel.plist
```

项目代码仍从原目录运行，任务数据库和结果仍使用服务端默认的 `demucs-server/output/`。
卸载代理不会删除这些结果、模型缓存或 ECS 上的公钥授权；如需撤销访问，另行从 ECS 删除对应公钥。

## ECS H5 接入边界

在 ECS 上可先验证：

```bash
curl --noproxy '*' --fail http://127.0.0.1:19000/api/v1/health
```

此脚本只准备 Mac 和隧道，**不会切换现有 ECS H5 的上游**。接入时需将相应接口反向代理到
`http://127.0.0.1:19000`，并验证上传、轮询、视频提音后分轨和下载路由的一致性。
当前 ECS 的独立提音服务仍可保持原样；混合两个后端需要额外的路由与任务关联改造。

隧道不会把任务数据库或结果自动复制到 ECS。按现有服务行为，结果完成后保留 24 小时，
持久化和清理在 Mac 上执行；Mac 离线时无法经隧道查询或下载。
若要求 Mac 离线后仍可下载，需要另行实现结果回传与 ECS 存储。

## 验证脚本本身

下面的测试使用临时目录和模拟进程，不安装 Homebrew、不启动模型，也不连接真实 ECS：

```bash
bash -n deploy/mac/setup.sh
python3.11 -m unittest discover -s deploy/mac/tests -v
```
