import os
import time
import uuid
import shutil
import asyncio
import zipfile
import io
import tempfile
import logging
import traceback
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from separator import separator
from config import HOST, PORT, MAX_FILE_SIZE_MB, ALLOWED_EXTENSIONS, MODEL_NAME, AUTO_CLEANUP_HOURS

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("demucs-server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading Demucs model: %s", MODEL_NAME)
    separator.load_model()
    logger.info("Demucs model loaded. Device=%s sources=%s sample_rate=%s",
                separator.device, separator.source_names, separator.sample_rate)
    cleanup_task = asyncio.create_task(periodic_cleanup())
    yield
    cleanup_task.cancel()

app = FastAPI(
    title="Demucs Stem Separation API",
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


@app.post("/api/v1/separate", summary="Sync separation", tags=["Separation"])
async def separate_sync(
    file: UploadFile = File(...),
    model: str = Query(default="htdemucs_ft")
):
    validate_file(file)
    logger.info("Sync separation request: filename=%s model=%s", file.filename, model)
    async with gpu_semaphore:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, file.filename)
            content = await file.read()
            logger.info("Sync separation input saved: path=%s size=%d", input_path, len(content))
            with open(input_path, "wb") as f:
                f.write(content)

            try:
                start_time = time.time()
                output_dir = os.path.join(tmpdir, "stems")
                stems = separator.separate(input_path, output_dir)
                elapsed = time.time() - start_time
            except Exception as e:
                logger.exception("Sync separation failed: filename=%s input_path=%s", file.filename, input_path)
                raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

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


@app.post("/api/v1/separate/async", summary="Async separation", tags=["Separation"])
async def separate_async(file: UploadFile = File(...)):
    validate_file(file)

    task_id = str(uuid.uuid4())[:8]
    task_dir = OUTPUT_BASE / task_id
    task_dir.mkdir(parents=True)
    logger.info("Async separation request: task=%s filename=%s", task_id, file.filename)

    input_path = task_dir / file.filename
    content = await file.read()
    logger.info("Async input saved: task=%s path=%s size=%d", task_id, input_path, len(content))
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
        logger.info("Task started: task=%s input=%s output=%s", task_id, input_path, output_dir)
        start = time.time()
        stems = separator.separate(input_path, output_dir)
        elapsed = time.time() - start
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["stems"] = {name: os.path.basename(path) for name, path in stems.items()}
        tasks[task_id]["processing_time"] = f"{elapsed:.2f}s"
        logger.info("Task completed: task=%s elapsed=%.2fs stems=%s", task_id, elapsed, list(stems.keys()))
    except Exception as e:
        logger.exception("Task failed: task=%s input=%s output=%s", task_id, input_path, output_dir)
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = f"{type(e).__name__}: {e}"
        tasks[task_id]["error_detail"] = traceback.format_exc()


@app.get("/api/v1/task/{task_id}", summary="Query task status", tags=["Separation"])
async def get_task_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]


@app.get("/api/v1/task/{task_id}/download", summary="Download all stems", tags=["Separation"])
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


@app.get("/api/v1/task/{task_id}/stem/{stem_name}", summary="Download single stem", tags=["Separation"])
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


@app.get("/api/v1/health", summary="Health check", tags=["System"])
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


@app.get("/api/v1/tasks", summary="List all tasks", tags=["System"])
async def list_tasks():
    return {"tasks": tasks, "count": len(tasks)}


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
