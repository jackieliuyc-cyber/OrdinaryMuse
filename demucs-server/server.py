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
import re
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from separator import separator
from task_store import TaskStore
from config import HOST, PORT, MAX_FILE_SIZE_MB, ALLOWED_EXTENSIONS, MODEL_NAME, AUTO_CLEANUP_HOURS

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("demucs-server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tasks
    tasks = TaskStore(OUTPUT_BASE / "tasks.sqlite3", AUTO_CLEANUP_HOURS)
    tasks.recover_interrupted()
    cleanup_expired_tasks()
    logger.info("Loading Demucs model: %s", MODEL_NAME)
    separator.load_model()
    logger.info("Demucs model loaded. Device=%s sources=%s sample_rate=%s",
                separator.device, separator.source_names, separator.sample_rate)
    cleanup_task = asyncio.create_task(periodic_cleanup())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task

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

OUTPUT_BASE = Path(__file__).resolve().parent / "output"
OUTPUT_BASE.mkdir(exist_ok=True)
tasks: TaskStore = None
gpu_semaphore = asyncio.Semaphore(1)
# Queue browser/App async jobs instead of running several models at once.
inference_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="demucs")
WEB_DIR = Path(__file__).resolve().parent / "web"


@app.get("/", include_in_schema=False)
async def web_index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/style.css", include_in_schema=False)
async def web_style():
    return FileResponse(WEB_DIR / "style.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
async def web_script():
    return FileResponse(WEB_DIR / "app.js", media_type="application/javascript")


@app.get("/api/v1/web-config", tags=["System"])
async def web_config():
    return {"max_file_size_mb": MAX_FILE_SIZE_MB,
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
            "retention_hours": AUTO_CLEANUP_HOURS}


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="web-assets")


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
async def separate_async(
    file: UploadFile = File(...),
    x_request_id: str = Header(default="", alias="X-Request-ID"),
):
    validate_file(file)

    task_id = x_request_id.strip() or str(uuid.uuid4())[:8]
    if len(task_id) > 64 or any(not (char.isalnum() or char in "-_") for char in task_id):
        raise HTTPException(status_code=400, detail="Invalid X-Request-ID")

    if not tasks.create(task_id, file.filename):
        logger.info("Reusing idempotent task: task=%s status=%s", task_id, tasks[task_id]["status"])
        return {"task_id": task_id, "status": tasks[task_id]["status"], "reused": True}

    task_dir = OUTPUT_BASE / task_id
    logger.info("Async separation request: task=%s filename=%s", task_id, file.filename)

    input_path = task_dir / file.filename
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        logger.info("Async input saved: task=%s path=%s size=%d", task_id, input_path, len(content))
        with open(input_path, "wb") as f:
            f.write(content)
        tasks.update(task_id, status="queued")
    except Exception as error:
        tasks.finish(task_id, "failed", error=f"{type(error).__name__}: {error}")
        raise

    asyncio.get_event_loop().run_in_executor(
        inference_executor, _process_task, task_id, str(input_path), str(task_dir / "stems")
    )

    return {"task_id": task_id, "status": tasks[task_id]["status"]}


def _process_task(task_id: str, input_path: str, output_dir: str):
    try:
        tasks.update(task_id, status="processing")
        logger.info("Task started: task=%s input=%s output=%s", task_id, input_path, output_dir)
        start = time.time()
        stems = separator.separate(input_path, output_dir)
        aac_stems = separator.encode_aac_stems(stems)
        elapsed = time.time() - start
        tasks.finish(task_id, "completed",
                     stems={name: os.path.basename(path) for name, path in stems.items()},
                     aac_stems={name: os.path.basename(path) for name, path in aac_stems.items()},
                     processing_time=f"{elapsed:.2f}s")
        logger.info(
            "Task completed: task=%s elapsed=%.2fs stems=%s download_format=m4a",
            task_id,
            elapsed,
            list(stems.keys()),
        )
    except Exception as e:
        logger.exception("Task failed: task=%s input=%s output=%s", task_id, input_path, output_dir)
        tasks.finish(task_id, "failed", error=f"{type(e).__name__}: {e}",
                     error_detail=traceback.format_exc())


def available_task(task_id):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    if tasks.is_expired(task):
        raise HTTPException(status_code=410, detail="结果已过期并停止提供下载，请重新上传音频。")
    return task


@app.get("/api/v1/task/{task_id}", summary="Query task status", tags=["Separation"])
async def get_task_status(task_id: str):
    return available_task(task_id)


@app.get("/api/v1/task/{task_id}/download", summary="Download all stems", tags=["Separation"])
async def download_stems(task_id: str):
    task = available_task(task_id)
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Task status: {task['status']}")

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
async def download_single_stem(
    task_id: str,
    stem_name: str,
    format: str = Query(default="wav", pattern="^(wav|m4a)$"),
    range_header: str = Header(default="", alias="Range"),
):
    task = available_task(task_id)
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Task status: {task['status']}")

    extension = "m4a" if format == "m4a" else "wav"
    if stem_name not in tasks[task_id]["stems"]:
        raise HTTPException(status_code=404, detail="Unknown stem")
    stem_path = OUTPUT_BASE / task_id / "stems" / f"{stem_name}.{extension}"
    if not stem_path.exists():
        available = list(tasks[task_id]["stems"].keys())
        raise HTTPException(status_code=404,
                            detail=f"Stem '{stem_name}' not found. Available: {available}")

    media_type = "audio/mp4" if extension == "m4a" else "audio/wav"
    # The pinned Starlette version does not implement byte ranges in FileResponse.
    # Browser audio players (especially mobile Safari) need these to seek/play.
    size = stem_path.stat().st_size
    headers = {"Accept-Ranges": "bytes"}
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or not any(match.groups()):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            start, end = max(0, size - int(last)), size - 1
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

        def chunks():
            with open(stem_path, "rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    data = stream.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers.update({"Content-Range": f"bytes {start}-{end}/{size}",
                        "Content-Length": str(end - start + 1)})
        return StreamingResponse(chunks(), status_code=206, media_type=media_type, headers=headers)
    return FileResponse(stem_path, media_type=media_type, filename=f"{stem_name}.{extension}", headers=headers)


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
    snapshot = tasks.snapshot()
    for task in snapshot.values():
        if tasks.is_expired(task):
            task["status"] = "expired"
    return {"tasks": snapshot, "count": len(snapshot)}


def validate_file(file: UploadFile):
    if not file.filename or "/" in file.filename or "\\" in file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported format '{ext}'. Allowed: {ALLOWED_EXTENSIONS}")
    if file.size and file.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400,
                            detail=f"File too large. Max: {MAX_FILE_SIZE_MB}MB")


def cleanup_expired_tasks(now=None):
    for task_id, task in tasks.snapshot().items():
        if not tasks.is_expired(task, now):
            continue
        try:
            task_dir = OUTPUT_BASE / task_id
            if task_dir.exists():
                shutil.rmtree(task_dir)
            if task["status"] != "expired":
                tasks.update(task_id, status="expired", stems={}, aac_stems={})
                logger.info("Cleaned up expired task: %s", task_id)
        except OSError:
            # Keep metadata so a temporary disk error is retried next cycle/startup.
            logger.exception("Could not clean up task: %s", task_id)


async def periodic_cleanup():
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(cleanup_expired_tasks)
        except Exception:
            logger.exception("Cleanup cycle failed; will retry in one minute")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, workers=1)
