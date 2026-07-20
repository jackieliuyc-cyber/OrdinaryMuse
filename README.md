# OrdinaryMuse

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
