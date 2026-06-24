# iOS 音频分轨 App 完整方案

> 基于 Demucs v4 本地部署，面向音乐练习/学习场景的音频分轨解决方案

---

## 一、行业调研

### 1.1 音频分轨技术概述

音频分轨（Audio Source Separation / Stem Splitting）是指将混合音频（如完整歌曲）分解为独立的音轨（人声、鼓、贝斯、吉他等），核心应用于音乐练习、翻唱制作、DJ 混音、音频后期等场景。

### 1.2 主流开源工具对比

| 工具 | 开发者 | 架构 | SDR (dB) | 轨道数 | 速度（3min歌曲） | 模型大小 | 开源 |
|------|--------|------|---------|--------|---------------|---------|------|
| **Demucs v4** | Meta | Hybrid Transformer | **9.0** | 4/6 轨 | GPU 25-40s / CPU 3-8min | ~80MB | MIT |
| Spleeter | Deezer | U-Net CNN | 5.9 | 2/4/5 轨 | GPU 5s / CPU 30s | 25-75MB | MIT |
| UVR5 | 社区 | 多引擎集成 | 依赖模型 | 多轨 | 依赖模型 | - | 开源 |
| Open-Unmix | 社区 | LSTM | ~5.5 | 2 轨 | 45s | ~30MB | MIT |

> SDR（Signal-to-Distortion Ratio）越高越好，表示分离后与原始轨道的相似度。Demucs v4 是当前开源 SOTA。

### 1.3 主流商业工具

| 工具 | 特点 | 定价 |
|------|------|------|
| LALAL.AI | 自研 Phoenix/Orion 引擎，支持多轨（含吉他/钢琴/合成器） | 按分钟付费 |
| iZotope RX 12 | 专业音频后期修复套件，工作室级别 | $399+ |
| Moises.ai | 在线分轨 + 调速调音高，UI 友好 | 订阅制 |
| PhonicMind | 高保真多轨分离 | 按曲付费 |

### 1.4 各轨道分离难度

| 轨道 | 难度 | 原因 |
|------|------|------|
| 鼓 | 低 | 瞬态冲击特征显著，时域容易识别 |
| 人声 | 中低 | 独特的共振峰结构和连续基频轨迹 |
| 贝斯 | 中 | 能量集中在低频区（40-300Hz），频域相对独立 |
| 钢琴 | 中高 | 频谱与吉他、合成器重叠 |
| 吉他 | 高 | 音色多样（清音/过载/失真）、频谱与多种乐器重叠、常被"埋"在混音中 |

### 1.5 核心技术原理

**技术演进路线：**

```
传统信号处理（频谱减法/Wiener滤波）
    → 频域深度学习（U-Net + 频谱掩码，Spleeter 代表）
    → 混合域深度学习（Hybrid Transformer，Demucs v4 代表，当前 SOTA）
    → 生成式重建（Suno 等新方向）
```

**Demucs v4 Hybrid Transformer 架构：**

```
                        输入混合音频 (waveform)
                              │
                 ┌────────────┴────────────┐
                 │                         │
           ┌─────▼─────┐           ┌───────▼───────┐
           │  时域编码器  │           │  STFT 频域编码器 │
           │ (Conv U-Net │           │ (Conv U-Net    │
           │  Encoder)   │           │  Encoder)      │
           └─────┬─────┘           └───────┬───────┘
                 │                         │
           ┌─────▼─────────────────────────▼─────┐
           │     Cross-domain Transformer         │
           │  ┌─────────────┐  ┌──────────────┐  │
           │  │ Self-Attn   │  │ Cross-Attn   │  │
           │  │ (域内注意力) │  │ (跨域注意力)  │  │
           │  └─────────────┘  └──────────────┘  │
           └─────┬─────────────────────────┬─────┘
                 │                         │
           ┌─────▼─────┐           ┌───────▼───────┐
           │  时域解码器  │           │  频域解码器     │
           └─────┬─────┘           └───────┬───────┘
                 │                    ┌────▼────┐
                 │                    │  ISTFT  │
                 │                    └────┬────┘
                 └────────────┬────────────┘
                              │ (加权融合)
                              ▼
                   4 个分离后的音轨
              (vocals / drums / bass / other)
```

**为什么双域比单域好：**
- 时域擅长捕捉瞬态信号（鼓的 attack、拨弦瞬间）
- 频域擅长捕捉稳态谐波（人声音高、贝斯基频和泛音）
- Cross-domain Transformer 让两个域的信息互补融合

---

## 二、技术选型

### 2.1 模型选择：Demucs v4（htdemucs_ft）

| 维度 | 选择 | 理由 |
|------|------|------|
| 模型 | htdemucs_ft | SDR 9.0 dB，开源最高质量 |
| 轨道 | 4 轨（vocals/drums/bass/other） | 覆盖音乐练习核心需求 |
| 备选 | htdemucs_6s（6 轨） | 额外支持吉他/钢琴单独分离 |

**Demucs 模型变体对比：**

| 模型名 | 轨道数 | SDR (dB) | 大小 | 说明 |
|--------|-------|---------|------|------|
| htdemucs_ft | 4 | **9.0** | ~80MB | Fine-tuned 版本，质量最高 |
| htdemucs | 4 | 7.5 | ~80MB | 基础 Hybrid Transformer |
| htdemucs_6s | 6 | ~8.5 | ~85MB | 额外分出吉他和钢琴 |
| hdemucs_mmi | 4 | 7.7 | ~80MB | v3 Hybrid（无 Transformer） |

### 2.2 部署方式：本地 HTTP 服务

三种部署方案对比后，选择本地部署：

| 维度 | 本地部署（选定） | 云端（Modal Labs） | 端侧推理（CoreML） |
|------|:---:|:---:|:---:|
| 分离质量 | Demucs v4 SOTA | 同左 | Spleeter，较差 |
| 服务器成本 | 仅电费 | ~$0.02/首 | 0 |
| 网络依赖 | 局域网 | 公网 | 无 |
| 隐私 | 数据不出本地 | 数据上传云端 | 最好 |
| 运维复杂度 | 低 | 极低 | 中高（模型转换） |

### 2.3 运行环境：Apple M3 Pro 18GB

| 指标 | M3 Pro 18GB |
|------|-------------|
| 推理后端 | MPS（Metal Performance Shaders） |
| 预估速度 | 1-2 分钟 / 3 分钟歌曲 |
| 内存占用 | ~4-6GB（统一内存，18GB 足够） |
| 是否需要分段推理 | 不需要 |

---

## 三、整体架构

```
┌──────────────────────────────────────────────────┐
│                 iOS App (SwiftUI)                 │
│                                                  │
│  ┌──────────┐   ┌──────────────┐   ┌──────────┐ │
│  │  歌曲导入  │ → │  上传到本地服务 │ → │ 分轨播放  │ │
│  │  (Files)  │   │ (URLSession) │   │(AVAudioEngine)│
│  └──────────┘   └──────┬───────┘   └──────────┘ │
└─────────────────────────┼────────────────────────┘
                          │ HTTP (局域网)
                 ┌────────▼────────┐
                 │  本地 Mac 服务器  │
                 │  FastAPI + Demucs │
                 │  M3 Pro (MPS)    │
                 └─────────────────┘
```

### iOS 端项目结构

```
StemSeparator/
├── Models/
│   ├── Song.swift                // 歌曲实体（id, title, status, stems[]）
│   ├── Stem.swift                // 分轨实体（name, localPath, volume, isMuted）
│   └── ProcessingStatus.swift    // enum: idle/uploading/processing/ready/failed
├── Services/
│   ├── SeparationService.swift   // 上传→轮询→下载，连接本地后端
│   ├── AudioFileImporter.swift   // 封装 UIDocumentPicker，格式转换
│   └── SongLibrary.swift         // GRDB 持久化层
├── AudioEngine/
│   ├── StemPlayer.swift          // AVAudioEngine 图，4 节点同步播放
│   └── PlaybackCoordinator.swift // 播放控制协调器
└── Views/
    ├── LibraryView.swift         // 歌曲列表，处理状态徽标
    ├── SongDetailView.swift      // 导入/处理触发，进度显示
    └── PlayerView.swift          // 练习主界面：4轨道 fader/mute/solo + 传输栏
```

### iOS 技术栈

| 层 | 选型 | 原因 |
|---|---|---|
| 语言 | Swift | 原生开发 |
| UI | SwiftUI | 快速迭代 |
| 音频引擎 | AVAudioEngine | 原生多轨混音，无第三方依赖 |
| 导入 | UIDocumentPickerViewController | 支持 Files、音乐库、AirDrop |
| 网络 | URLSession async/await | 原生异步 |
| 存储 | FileManager + GRDB.swift | 轻量 SQLite |
| 最低版本 | iOS 16 | AVAudioEngine API 稳定 |

---

## 四、后端服务实现

### 4.1 项目结构

```
demucs-server/
├── server.py           # FastAPI 主服务
├── separator.py        # Demucs 分轨核心逻辑
├── config.py           # 配置项
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 部署（可选）
├── docker-compose.yml  # Docker Compose（可选）
├── start.sh            # 启动脚本
└── output/             # 分轨结果临时目录
```

### 4.2 环境安装

```bash
# 1. 创建 Python 虚拟环境
conda create -n demucs-server python=3.11
conda activate demucs-server

# 2. 安装 PyTorch（macOS Apple Silicon）
pip install torch==2.1.0 torchaudio==2.1.0

# 其他平台：
# CUDA 11.8: pip install torch==2.1.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cu118
# CUDA 12.1: pip install torch==2.1.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cu121
# CPU:       pip install torch==2.1.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cpu

# 3. 安装 Demucs 和 Web 框架
pip install demucs==4.0.1 fastapi uvicorn python-multipart

# 4. 安装 ffmpeg
brew install ffmpeg    # macOS

# 5. 预下载模型权重（~80MB，避免首次请求等待）
python -c "from demucs.pretrained import get_model; get_model('htdemucs_ft')"

# 6. 验证
python -c "import torch; print(f'PyTorch: {torch.__version__}, MPS: {torch.backends.mps.is_available()}')"
```

### 4.3 配置文件 `config.py`

```python
import os

# 模型配置
MODEL_NAME = os.getenv("DEMUCS_MODEL", "htdemucs_ft")
DEVICE = os.getenv("DEMUCS_DEVICE", "auto")        # auto / cuda / mps / cpu

# 服务配置
HOST = os.getenv("SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("SERVER_PORT", "8000"))

# 文件配置
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}

# 清理配置
AUTO_CLEANUP_HOURS = int(os.getenv("AUTO_CLEANUP_HOURS", "24"))
```

### 4.4 分轨核心逻辑 `separator.py`

```python
import torch
import torchaudio
from demucs.pretrained import get_model
from demucs.apply import apply_model
from demucs.audio import save_audio
from pathlib import Path
from config import MODEL_NAME, DEVICE


class StemSeparator:
    def __init__(self):
        self.model = None
        self.device = None

    def load_model(self):
        if DEVICE == "auto":
            if torch.cuda.is_available():
                self.device = "cuda"
            elif torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"
        else:
            self.device = DEVICE

        self.model = get_model(MODEL_NAME)
        self.model.eval()
        self.model.to(self.device)

        print(f"Model '{MODEL_NAME}' loaded on {self.device}")
        if self.device == "cuda":
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_mem / 1024**3
            print(f"GPU: {gpu_name} ({gpu_mem:.1f} GB)")
        elif self.device == "mps":
            print("Using Apple Silicon MPS acceleration")

    def separate(self, input_path: str, output_dir: str) -> dict:
        wav, sr = torchaudio.load(input_path)

        if sr != self.model.samplerate:
            wav = torchaudio.functional.resample(wav, sr, self.model.samplerate)

        if wav.shape[0] == 1:
            wav = wav.repeat(2, 1)
        elif wav.shape[0] > 2:
            wav = wav[:2]

        ref = wav.mean(0)
        mean = ref.mean()
        std = ref.std()
        wav = (wav - mean) / (std + 1e-8)

        wav_input = wav.unsqueeze(0).to(self.device)
        with torch.no_grad():
            sources = apply_model(self.model, wav_input, device=self.device)

        sources = sources * std + mean

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        result = {}
        for i, name in enumerate(self.model.sources):
            stem_path = str(out / f"{name}.wav")
            save_audio(sources[0, i].cpu(), stem_path,
                       samplerate=self.model.samplerate)
            result[name] = stem_path

        if self.device == "cuda":
            torch.cuda.empty_cache()

        return result

    @property
    def source_names(self) -> list:
        return list(self.model.sources)

    @property
    def sample_rate(self) -> int:
        return self.model.samplerate


separator = StemSeparator()
```

### 4.5 HTTP 服务 `server.py`

```python
import os
import time
import uuid
import shutil
import asyncio
import zipfile
import io
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from separator import separator
from config import HOST, PORT, MAX_FILE_SIZE_MB, ALLOWED_EXTENSIONS, MODEL_NAME, AUTO_CLEANUP_HOURS


@asynccontextmanager
async def lifespan(app: FastAPI):
    separator.load_model()
    cleanup_task = asyncio.create_task(periodic_cleanup())
    yield
    cleanup_task.cancel()

app = FastAPI(
    title="Demucs Stem Separation API",
    description="本地部署的音频分轨服务，基于 Meta Demucs v4",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_BASE = Path("./output")
OUTPUT_BASE.mkdir(exist_ok=True)
tasks: dict = {}
gpu_semaphore = asyncio.Semaphore(1)


# ── 同步分轨（短音频，直接返回 zip） ─────────────
@app.post("/api/v1/separate", summary="同步分轨", tags=["分轨"])
async def separate_sync(
    file: UploadFile = File(...),
    model: str = Query(default="htdemucs_ft")
):
    validate_file(file)
    async with gpu_semaphore:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, file.filename)
            content = await file.read()
            with open(input_path, "wb") as f:
                f.write(content)

            start_time = time.time()
            output_dir = os.path.join(tmpdir, "stems")
            stems = separator.separate(input_path, output_dir)
            elapsed = time.time() - start_time

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for name, path in stems.items():
                    zf.write(path, f"{name}.wav")
            zip_buffer.seek(0)

            song_name = Path(file.filename).stem
            return StreamingResponse(
                zip_buffer,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{song_name}_stems.zip"',
                    "X-Processing-Time": f"{elapsed:.2f}s",
                    "X-Stems": ",".join(stems.keys()),
                }
            )


# ── 异步分轨（长音频 / iOS App 轮询） ─────────────
@app.post("/api/v1/separate/async", summary="异步分轨", tags=["分轨"])
async def separate_async(file: UploadFile = File(...)):
    validate_file(file)

    task_id = str(uuid.uuid4())[:8]
    task_dir = OUTPUT_BASE / task_id
    task_dir.mkdir(parents=True)

    input_path = task_dir / file.filename
    content = await file.read()
    with open(input_path, "wb") as f:
        f.write(content)

    tasks[task_id] = {
        "status": "processing",
        "filename": file.filename,
        "created_at": datetime.now().isoformat(),
        "stems": {},
    }

    asyncio.get_event_loop().run_in_executor(
        None, _process_task, task_id, str(input_path), str(task_dir / "stems")
    )

    return {"task_id": task_id, "status": "processing"}


def _process_task(task_id: str, input_path: str, output_dir: str):
    try:
        start = time.time()
        stems = separator.separate(input_path, output_dir)
        elapsed = time.time() - start
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["stems"] = {name: os.path.basename(path) for name, path in stems.items()}
        tasks[task_id]["processing_time"] = f"{elapsed:.2f}s"
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)


@app.get("/api/v1/task/{task_id}", summary="查询任务状态", tags=["分轨"])
async def get_task_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]


@app.get("/api/v1/task/{task_id}/download", summary="下载全部分轨", tags=["分轨"])
async def download_stems(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    if tasks[task_id]["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Task status: {tasks[task_id]['status']}")

    stems_dir = OUTPUT_BASE / task_id / "stems"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for wav_file in stems_dir.glob("*.wav"):
            zf.write(wav_file, wav_file.name)
    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{task_id}_stems.zip"'}
    )


@app.get("/api/v1/task/{task_id}/stem/{stem_name}", summary="下载单个轨道", tags=["分轨"])
async def download_single_stem(task_id: str, stem_name: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    if tasks[task_id]["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Task status: {tasks[task_id]['status']}")

    stem_path = OUTPUT_BASE / task_id / "stems" / f"{stem_name}.wav"
    if not stem_path.exists():
        available = list(tasks[task_id]["stems"].keys())
        raise HTTPException(status_code=404,
                            detail=f"Stem '{stem_name}' not found. Available: {available}")

    return FileResponse(stem_path, media_type="audio/wav", filename=f"{stem_name}.wav")


# ── 系统接口 ─────────────────────────────────────
@app.get("/api/v1/health", summary="健康检查", tags=["系统"])
async def health():
    import torch
    info = {
        "status": "healthy",
        "model": MODEL_NAME,
        "device": separator.device,
        "sources": separator.source_names,
        "sample_rate": separator.sample_rate,
    }
    if separator.device == "cuda":
        info["gpu"] = torch.cuda.get_device_name(0)
        info["gpu_memory_total"] = f"{torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GB"
    return info


@app.get("/api/v1/tasks", summary="所有任务", tags=["系统"])
async def list_tasks():
    return {"tasks": tasks, "count": len(tasks)}


# ── 工具函数 ─────────────────────────────────────
def validate_file(file: UploadFile):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported format '{ext}'. Allowed: {ALLOWED_EXTENSIONS}")
    if file.size and file.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400,
                            detail=f"File too large. Max: {MAX_FILE_SIZE_MB}MB")


async def periodic_cleanup():
    while True:
        await asyncio.sleep(3600)
        cutoff = datetime.now() - timedelta(hours=AUTO_CLEANUP_HOURS)
        expired = [tid for tid, info in tasks.items()
                   if datetime.fromisoformat(info["created_at"]) < cutoff]
        for tid in expired:
            task_dir = OUTPUT_BASE / tid
            if task_dir.exists():
                shutil.rmtree(task_dir)
            del tasks[tid]
        if expired:
            print(f"Cleaned up {len(expired)} expired tasks")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, workers=1)
```

### 4.6 依赖文件 `requirements.txt`

```
torch==2.1.0
torchaudio==2.1.0
demucs==4.0.1
fastapi==0.104.1
uvicorn==0.24.0
python-multipart==0.0.6
```

### 4.7 启动脚本 `start.sh`

```bash
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

uvicorn server:app --host 0.0.0.0 --port $SERVER_PORT --workers 1 --log-level info
```

---

## 五、API 接口文档

服务启动后访问 `http://localhost:8000/docs` 可查看 Swagger 交互式文档。

### 5.1 接口汇总

| 方法 | 路径 | 说明 | 适用场景 |
|------|------|------|---------|
| POST | `/api/v1/separate` | 同步分轨，返回 zip | 短音频 < 10 分钟 |
| POST | `/api/v1/separate/async` | 异步提交分轨任务 | 长音频 / App 集成 |
| GET | `/api/v1/task/{task_id}` | 查询任务状态 | 轮询处理进度 |
| GET | `/api/v1/task/{task_id}/download` | 下载全部分轨（zip） | 批量下载 |
| GET | `/api/v1/task/{task_id}/stem/{name}` | 下载单个轨道 | 按需下载 |
| GET | `/api/v1/health` | 健康检查 & 设备信息 | 服务监控 |
| GET | `/api/v1/tasks` | 查看所有任务列表 | 管理 |

### 5.2 使用示例

**同步分轨（curl）：**

```bash
curl -X POST http://localhost:8000/api/v1/separate \
     -F "file=@song.mp3" \
     --output stems.zip

unzip stems.zip -d stems/
# stems/vocals.wav  stems/drums.wav  stems/bass.wav  stems/other.wav
```

**异步分轨（curl）：**

```bash
# 提交任务
curl -X POST http://localhost:8000/api/v1/separate/async -F "file=@song.mp3"
# {"task_id": "a1b2c3d4", "status": "processing"}

# 轮询状态
curl http://localhost:8000/api/v1/task/a1b2c3d4
# {"status": "completed", "stems": {...}, "processing_time": "85.3s"}

# 下载结果
curl http://localhost:8000/api/v1/task/a1b2c3d4/download --output stems.zip
# 或单独下载某轨道
curl http://localhost:8000/api/v1/task/a1b2c3d4/stem/vocals --output vocals.wav
```

---

## 六、iOS 端核心实现

### 6.1 SeparationService.swift（网络层）

```swift
import Foundation

enum SeparationError: Error {
    case uploadFailed, processingFailed(String), downloadFailed
}

class SeparationService {
    private let baseURL: String

    init(baseURL: String = "http://192.168.1.100:8000") {
        self.baseURL = baseURL
    }

    func healthCheck() async throws -> Bool {
        let url = URL(string: "\(baseURL)/api/v1/health")!
        let (_, response) = try await URLSession.shared.data(from: url)
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    func separate(audioData: Data, filename: String,
                  onProgress: @escaping (String) -> Void) async throws -> [String: Data] {
        onProgress("上传中...")
        let taskId = try await submitTask(audioData: audioData, filename: filename)

        onProgress("处理中...")
        try await pollUntilComplete(taskId: taskId, onProgress: onProgress)

        onProgress("下载中...")
        return try await downloadStems(taskId: taskId)
    }

    private func submitTask(audioData: Data, filename: String) async throws -> String {
        let url = URL(string: "\(baseURL)/api/v1/separate/async")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        body.append("Content-Type: application/octet-stream\r\n\r\n")
        body.append(audioData)
        body.append("\r\n--\(boundary)--\r\n")

        let (data, _) = try await URLSession.shared.upload(for: request, from: body)
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let taskId = json?["task_id"] as? String else {
            throw SeparationError.uploadFailed
        }
        return taskId
    }

    private func pollUntilComplete(taskId: String,
                                   onProgress: @escaping (String) -> Void) async throws {
        let url = URL(string: "\(baseURL)/api/v1/task/\(taskId)")!
        while true {
            try await Task.sleep(nanoseconds: 5_000_000_000)
            let (data, _) = try await URLSession.shared.data(from: url)
            let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            let status = json?["status"] as? String ?? "unknown"
            switch status {
            case "completed":
                if let time = json?["processing_time"] as? String {
                    onProgress("处理完成（\(time)）")
                }
                return
            case "failed":
                throw SeparationError.processingFailed(json?["error"] as? String ?? "")
            default:
                onProgress("处理中...")
            }
        }
    }

    private func downloadStems(taskId: String) async throws -> [String: Data] {
        var result: [String: Data] = [:]
        for name in ["vocals", "drums", "bass", "other"] {
            let url = URL(string: "\(baseURL)/api/v1/task/\(taskId)/stem/\(name)")!
            let (data, response) = try await URLSession.shared.data(from: url)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                throw SeparationError.downloadFailed
            }
            result[name] = data
        }
        return result
    }
}

private extension Data {
    mutating func append(_ string: String) {
        if let data = string.data(using: .utf8) { append(data) }
    }
}
```

### 6.2 StemPlayer.swift（音频播放引擎）

```swift
import AVFoundation

class StemPlayer {
    private let engine = AVAudioEngine()
    private var playerNodes: [AVAudioPlayerNode] = []
    private var audioFiles: [AVAudioFile] = []
    private var trackVolumes: [Float] = [1, 1, 1, 1]
    private var isMuted: [Bool] = [false, false, false, false]
    private var soloedStem: Int? = nil

    let stemNames = ["vocals", "drums", "bass", "other"]

    func loadStems(directory: URL) throws {
        engine.stop()
        playerNodes.forEach { engine.disconnectNodeOutput($0); engine.detach($0) }
        playerNodes.removeAll()
        audioFiles.removeAll()

        for name in stemNames {
            let fileURL = directory.appendingPathComponent("\(name).wav")
            let file = try AVAudioFile(forReading: fileURL)
            let node = AVAudioPlayerNode()

            engine.attach(node)
            engine.connect(node, to: engine.mainMixerNode, format: file.processingFormat)

            playerNodes.append(node)
            audioFiles.append(file)
        }

        engine.prepare()
    }

    func play() throws {
        if !engine.isRunning { try engine.start() }

        let futureTime = AVAudioTime(
            hostTime: mach_absolute_time() + hostTicksFor(seconds: 0.1)
        )
        for (node, file) in zip(playerNodes, audioFiles) {
            node.scheduleFile(file, at: futureTime)
        }
        playerNodes.forEach { $0.play() }
    }

    func pause() {
        playerNodes.forEach { $0.pause() }
    }

    func stop() {
        playerNodes.forEach { $0.stop() }
        engine.stop()
    }

    func seek(to time: TimeInterval) throws {
        guard let sampleRate = audioFiles.first?.processingFormat.sampleRate else { return }
        let startFrame = AVAudioFramePosition(time * sampleRate)

        playerNodes.forEach { $0.stop() }

        let futureTime = AVAudioTime(
            hostTime: mach_absolute_time() + hostTicksFor(seconds: 0.1)
        )
        for (node, file) in zip(playerNodes, audioFiles) {
            let frameCount = AVAudioFrameCount(file.length - startFrame)
            guard frameCount > 0 else { continue }
            node.scheduleSegment(file, startingFrame: startFrame,
                                 frameCount: frameCount, at: futureTime)
        }
        playerNodes.forEach { $0.play() }
    }

    // ── 音量 / Mute / Solo 控制 ──
    func setVolume(_ volume: Float, for stemIndex: Int) {
        trackVolumes[stemIndex] = volume
        applyVolumes()
    }

    func toggleMute(for stemIndex: Int) {
        isMuted[stemIndex].toggle()
        applyVolumes()
    }

    func setSolo(_ stemIndex: Int) {
        soloedStem = (soloedStem == stemIndex) ? nil : stemIndex
        applyVolumes()
    }

    private func applyVolumes() {
        for (i, node) in playerNodes.enumerated() {
            if let solo = soloedStem {
                node.volume = (i == solo) ? trackVolumes[i] : 0
            } else {
                node.volume = isMuted[i] ? 0 : trackVolumes[i]
            }
        }
    }

    // ── 播放进度 ──
    var currentTime: TimeInterval {
        guard let node = playerNodes.first,
              let playerTime = node.playerTime(forNodeTime: node.lastRenderTime ?? AVAudioTime()) else {
            return 0
        }
        return Double(playerTime.sampleTime) / playerTime.sampleRate
    }

    var duration: TimeInterval {
        guard let file = audioFiles.first else { return 0 }
        return Double(file.length) / file.processingFormat.sampleRate
    }

    private func hostTicksFor(seconds: Double) -> UInt64 {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        let nanos = seconds * 1_000_000_000
        return UInt64(nanos * Double(info.denom) / Double(info.numer))
    }
}
```

**AVAudioEngine 信号链：**

```
AVAudioPlayerNode (Vocals) ──┐
AVAudioPlayerNode (Drums)  ──┤── AVAudioMixerNode ── AVAudioOutputNode
AVAudioPlayerNode (Bass)   ──┤      (主混音器)
AVAudioPlayerNode (Other)  ──┘
```

---

## 七、用户交互流程

```
1. LibraryView（歌曲列表）
   └─ 点击 "+" 导入
       └─ UIDocumentPicker 选择文件
           └─ 复制到 Documents/[songId]/

2. SongDetailView（歌曲详情）
   └─ 点击 "分离音轨"
       └─ 上传到本地后端
           └─ 进度指示器（每 5 秒轮询）
               └─ 完成：4 个 WAV 下载到本地

3. PlayerView（练习主界面）
   ┌─────────────────────────────────────┐
   │  Vocals  [M] [S]  ═══════●══════   │
   │  Drums   [M] [S]  ═══●══════════   │
   │  Bass    [M] [S]  ═══════════●═══   │
   │  Other   [M] [S]  ════●═════════   │
   │                                     │
   │  ◄◄   ▶ / ⏸   ►►                   │
   │  ──────────●────────────── 2:34     │
   └─────────────────────────────────────┘
   [M] = Mute    [S] = Solo    滑块 = 音量
```

---

## 八、性能优化

### 8.1 Apple Silicon MPS 加速

`config.py` 中 `DEVICE=auto` 会自动检测 MPS 后端。M3 Pro 18GB 预估性能：

| 歌曲时长 | MPS 推理时间 | CPU 推理时间 |
|---------|-------------|-------------|
| 3 分钟 | ~1-2 分钟 | ~3-5 分钟 |
| 5 分钟 | ~2-3 分钟 | ~5-8 分钟 |
| 10 分钟 | ~4-6 分钟 | ~10-15 分钟 |

### 8.2 分段推理（长音频 / 低内存）

```python
sources = apply_model(
    self.model, wav_input, device=self.device,
    segment=7.8,   # 每次处理 7.8 秒片段
    overlap=0.25,  # 25% 重叠避免拼接伪影
)
```

### 8.3 并发控制

服务已内置 `asyncio.Semaphore(1)` 保证 GPU 推理串行执行，避免 OOM。

---

## 九、Docker 部署（可选）

### Dockerfile

```dockerfile
FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs_ft')"
COPY . .
RUN mkdir -p /app/output
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

### docker-compose.yml

```yaml
version: '3.8'
services:
  demucs-server:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./output:/app/output
    environment:
      - DEMUCS_MODEL=htdemucs_ft
      - DEMUCS_DEVICE=auto
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
```

---

## 十、分阶段实施计划

### Phase 1：MVP（6 周）

| 周 | 任务 | 产出 |
|---|---|---|
| Week 1 | Xcode 项目 + SwiftUI 脚手架 + 音频导入 | 可导入歌曲到 App |
| Week 2 | 后端部署 + SeparationService 网络层 | 可上传/下载分轨 |
| Week 3 | 处理 UI（进度、后台下载） | 完整处理流程 |
| Week 4 | StemPlayer（AVAudioEngine 4 轨同步） | 可播放分轨 |
| Week 5 | PlayerView（mute/solo/volume 控件） | 练习主界面 |
| Week 6 | LibraryView + 错误处理 + TestFlight | 可测试 Beta |

### Phase 2：体验优化（3-4 周）

- A-B 循环（`scheduleSegment` 实现循环练习区间）
- 变速不变调（`AVAudioUnitTimePitch`，`rate=0.75` 慢速练习）
- App Store 上架

### Phase 3：扩展（可选）

- 6 轨分离（切换 htdemucs_6s 模型，额外支持吉他/钢琴）
- 端侧推理（Spleeter + ONNX Runtime for iOS，离线可用）
- 云端部署（Modal Labs，支持多用户）

---

## 十一、关键风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| AVAudioEngine 多轨同步漂移 | 鼓和贝斯错位 | 所有节点用同一 `AVAudioTime` 调度；Seek 时原子性停止/重调度 |
| MPS 后端算子不支持 | 推理报错 | 回退到 CPU（`DEMUCS_DEVICE=cpu`），M3 Pro CPU 也可在 3-5 分钟内完成 |
| 长音频内存不足 | 进程崩溃 | 启用分段推理（`segment=7.8`） |
| 不支持的音频格式 | 导入失败 | 服务端通过 ffmpeg 自动转码；iOS 端用 `AVAssetExportSession` 预转换 |
| 局域网 iOS 访问限制 | 无法连接 | iOS 16+ 允许局域网 HTTP（需在 Info.plist 配置 ATS 例外） |

---

