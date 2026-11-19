# ECS 专用 SSH 反向隧道账号

2026-11-19 已在杭州 ECS `i-bp1dhypbynl7gfsclml5` 配置，使用现有 SSH 端口 `22`。
未修改安全组、域名或 Web 监听。SSH 管理员原生效配置保持不变，采用热加载，未重启现有连接。

| 项目 | 配置 |
| --- | --- |
| 专用账号 | `muse-tunnel`，普通 UID/GID 1000，无 sudo 权限 |
| 认证 | 仅公钥，密码锁定；禁用该账号的云端动态公钥命令 |
| 服务端转发入口 | 仅 `127.0.0.1:19000`，其他地址或端口不允许 |
| 转发方式 | 仅 SSH 远程 TCP 转发；本地 TCP、Unix socket、代理与 X11 转发关闭 |
| 终端 | 禁止 PTY；通过强制命令限制通用 shell、SFTP 和其他命令 |
| 保留命令 | Mac 脚本的 `true` 探测和固定的 `curl` 模型健康检查 |
| 公钥文件 | `/etc/ssh/authorized_keys/muse-tunnel`，root 所有、0644 |
| 命令入口 | `/usr/local/libexec/ordinary-muse-tunnel-command`，root 所有、0755 |

公钥是公开数据，0644 使 sshd 在切换到目标用户后仍能读取，同时只有 root 能修改。
不要把公钥加到此账号的 `~/.ssh/authorized_keys`：该账号已配置为读取上表的 root 管理路径。

当前授权文件为空，等待新 Mac 公钥。没有给新 Mac 生成、复制或保存私钥。
账号规则本身不会创建 `19000` 监听；只有已授权客户端连接成功后才会建立回环监听。

## 新 Mac 接入

1. 在新 Mac 上生成密钥，提供 `.pub` 公钥整行；私钥留在新 Mac。
2. ECS 管理员校验公钥后，追加到 `/etc/ssh/authorized_keys/muse-tunnel`，保留已有条目。
3. 运行仓库的 Mac 安装脚本，使用专用账号并加上 `--no-authorize`，因为该账号不能自行修改公钥文件。
4. 如果提供的是已有密钥，必须通过 `--identity-file` 指向与该公钥对应的私钥。

```bash
# 公钥授权完成后执行；把地址和私钥路径换成实际值。
bash deploy/mac/setup.sh setup \
  --ecs-host ECS_PUBLIC_IP --ecs-user muse-tunnel \
  --identity-file /absolute/path/to/private-key \
  --no-authorize
```

如果公钥来自安装脚本默认生成的 worker 密钥，则省略 `--identity-file` 即可。
首次连接仍需核对 SSH 主机指纹，当前 ECS 的 Ed25519 指纹为：

```text
SHA256:x9amYoLGyoVucoB9GICkZnGGSvsbwh+TmXRuUZiHYDA
```

强制命令允许的健康检查与 `deploy/mac/worker.py` 保持一致，不使用 `eval`：

```text
true
curl --noproxy '*' --fail --silent --show-error --max-time 8 http://127.0.0.1:19000/api/v1/health
```

## 部署和校验

`configure_account.py` 要求 root、预检查得到的 sshd_config SHA-256 和全新的 staging 目录。
它先构造候选配置，检查语法及管理员完整生效配置不变，再创建普通账号、权限文件并热加载 sshd。
主配置中的规则带有标记，来自 `tunnel-account.conf`；无需依赖 OpenSSH 8.0 的 Include 支持。
发生 SSH 主配置应用错误时会恢复备份。不要在已有 staging 目录覆盖回退备份。

成功部署的备份：

```text
/srv/ordinary-muse-h5/ssh-staging/tunnel-20261119-193855-6ac799-r2/sshd_config.before
```

首次安装曾在设置文件属主时中止，当时主配置未切换；修正后使用新目录成功部署。

本地验证：

```bash
python3.11 -m unittest discover -s deploy/ecs/ssh/tests -v
sh -n deploy/ecs/ssh/tunnel-command.sh
```

`smoke_account.py` 只在 ECS 上用临时密钥和回环假服务测试 SSH 策略，结束时清除测试密钥与进程。
已验证指定远程转发和健康检查成功，通用命令、其他远程端口及本地转发被拒绝。
不尝试绑定公网地址；新 Mac 的实际网络连接、模型推理和 H5 分轨路由尚未验证或启用。
