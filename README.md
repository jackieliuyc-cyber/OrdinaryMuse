# OrdinaryMuse

## H5 音轨工作室

H5 与模型接口由同一个 Mac 服务提供，无需 Node.js 构建或额外云服务器。
按下文启动 `demucs-server/start.sh` 后，在浏览器打开 `http://127.0.0.1:8000/`。
外网使用现有花生壳映射地址的根路径，建议配置 HTTPS 映射到本地 `8000` 端口。
无需再填写接口地址，网页使用同源 `/api/v1/…` 接口。
直接打开 `web/index.html` 可预览外观，但上传和分离需要通过上述服务地址使用。

功能：视频提音（M4A/WAV/AIFF/CAF）、提音后直接分轨、选择/拖放音频、上传进度、异步分离及排队、网络中断后恢复查询、
此浏览器最近 12 条任务、四轨独立试听、M4A/WAV 单轨下载与全部 WAV ZIP 下载。
支持手机和桌面布局，不依赖第三方 CDN。前端位于 `demucs-server/web/`，修改后刷新即可。

使用说明：

- 上传时保持网页打开；上传成功后关闭页面不影响 Mac 上的分离，重新打开会查询最近任务。
- 文件格式、大小上限、结果保留时间从服务端配置读取，默认最大 50 MB、分离完成后保留 24 小时。页面显示每个结果的具体到期时间。
- 切换到“视频提音”，选择 MP4/MOV/M4V/MKV/WebM/AVI 视频和导出格式，点击“开始提取”。提取首条音轨，支持下载 M4A（AAC）、WAV/AIFF（24-bit PCM）和 CAF（32-bit Float PCM），最多双声道。试听统一使用 M4A；“继续分离四条音轨”在服务端复制提音结果，避免再次上传，也不会因提音结果先过期而删除分轨任务的输入。
- 视频上传默认上限 500 MB，可用 `MAX_VIDEO_SIZE_MB` 调整；视频由 Mac 上的 FFmpeg 处理，需完整上传后才能提取。提音任务支持排队、刷新恢复及服务重启查询，完成后保留 24 小时，与分轨任务分别计时。无声或损坏视频会明确报错。
- 播放音轨时会暂停其他音轨；当前版本不包含同步混音或混音导出。
- 浏览器记录仅保存任务 ID、文件名和状态；服务端使用 `demucs-server/output/tasks.sqlite3` 持久化任务。重启后已完成任务仍可查询和下载，清理浏览器数据只会清除本机历史。
- 完成时间在分离及 M4A 编码全部成功后记录，保留时间由 `AUTO_CLEANUP_HOURS` 设置（默认 24）。每个任务的到期时间固定保存，修改配置或重启服务不会延长旧任务的保留时间。
- 到期立即停止查询结果和下载（返回 410）；每分钟以及服务启动时检查并删除过期任务的上传文件和分轨文件。保留轻量过期记录用于明确提示；Mac 关机期间的过期文件在下次启动时清理。
- 服务重启前未完成的上传、排队和处理任务会标记为失败，请重新上传；失败任务的文件从失败时起保留同样时长后清理。
- 此持久化机制适用于升级后的新任务。旧版本没有保存任务元数据，无法可靠恢复已有文件的任务状态和完成时间，不会擅自清理这些旧目录。备份时应同时保留数据库和任务文件夹。
- 此版延续现有无登录接口，适合受控试用；请在隧道入口限制访问。尚未实现多用户鉴权和任务隔离。
- 花生壳访问速度取决于隧道带宽；Mac 和隧道客户端必须保持在线。部署不需要修改 iOS App。

验证（使用模拟分离器，不下载或运行模型）：

```bash
cd demucs-server
.venv/bin/python -m unittest discover -s tests -v
node --check web/app.js
```

测试覆盖页面与静态资源、真实 HTTP 上传/轮询/下载、重复请求、串行排队、文件校验、失败任务、完成后 24 小时边界、重启恢复与清理、删除失败重试；视频测试使用 FFmpeg 生成合成视频，验证四种格式编码、无声视频提示、提音后分轨及源文件过期隔离。
实际模型性能和外网访问需要在 Mac 启动真实服务后验证。

## 内网服务 + 隧道临时外网访问

开发阶段可以把本机 Demucs 服务通过临时隧道暴露给 iPhone 真机访问。适合调试和演示，不建议作为正式生产服务。

### 1. 准备服务端环境

```bash
cd demucs-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
```

### 2. 本地启动 Demucs 服务

```bash
cd "/Users/lyc/Downloads/Ordinary Muse/demucs-server"
source .venv/bin/activate
./start.sh
```

默认服务地址：

```text
http://127.0.0.1:8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/api/v1/health
```

指定端口或推理设备：

```bash
SERVER_PORT=8000 DEMUCS_DEVICE=auto ./start.sh
DEMUCS_DEVICE=cpu ./start.sh
DEMUCS_DEVICE=mps ./start.sh
AAC_BITRATE=192k ./start.sh
AAC_MAX_WORKERS=2 ./start.sh
```

服务端会保留 Demucs 生成的 WAV，并额外编码为 AAC 256 kbps。App 默认下载体积更小的 M4A 音轨；可通过 `AAC_BITRATE` 调整码率。默认同时编码 2 条音轨，可通过 `AAC_MAX_WORKERS` 调整并发数。

### 3. 启动隧道

Cloudflare Tunnel:

```bash
brew install cloudflared
./start_tunnel.sh cloudflared
```

ngrok:


```bash
brew install ngrok/ngrok/ngrok
ngrok config add-authtoken <your-token>
./start_tunnel.sh ngrok
```

花生壳:

```bash
cd /Users/lyc/project/OrdinaryMuse/demucs-server
source .venv/bin/activate
./start_tunnel.sh oray
```

然后在花生壳客户端中创建 HTTP/HTTPS 映射：

```text
内网主机: 127.0.0.1
内网端口: 8000
```

### 4. 在 App 中使用

打开 App：

```text
音轨分离 -> 模型分离 -> 齿轮设置
```

把隧道生成的公网地址填入“服务器地址”，例如：

```text
https://example.trycloudflare.com
https://example.ngrok-free.app
https://example.vicp.fun
```

也可以先复制公网地址，再点击“粘贴隧道”。点击“测试连接”显示连接正常后即可开始模型分离。

### 5. 注意事项

- 隧道 URL 免费版可能会变化，变更后需要重新填入 App。
- 本机必须保持开机，`start_tunnel.sh` 终端不能关闭。
- 音频上传和四轨 M4A 下载仍受隧道带宽限制，免费隧道可能较慢。
- 临时隧道适合开发测试；TestFlight 或正式上线建议使用云服务器 + Nginx。
