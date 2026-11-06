"""HTTP integration tests; substitute only the expensive model implementation."""
import io
import json
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_store import TaskStore, utcnow


class FakeSeparator:
    device = "cpu"
    source_names = ["vocals", "drums", "bass", "other"]
    sample_rate = 44100
    active = 0
    peak = 0

    def load_model(self):
        pass

    def separate(self, input_path, output_dir):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            time.sleep(.15)
            if Path(input_path).name == "broken.wav":
                raise ValueError("Invalid test audio")
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            result = {}
            for stem in self.source_names:
                target = output / f"{stem}.wav"
                with wave.open(str(target), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(44100)
                    audio.writeframes(b"\0\0" * 44100)
                result[stem] = str(target)
            return result
        finally:
            self.active -= 1

    def encode_aac_stems(self, stems):
        return {}


fake = FakeSeparator()
sys.modules["separator"] = types.SimpleNamespace(separator=fake)
import server  # noqa: E402
import uvicorn  # noqa: E402


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        server.OUTPUT_BASE = Path(cls.temp.name)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        cls.http = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
        cls.thread = threading.Thread(target=cls.http.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.http.started:
                return
            time.sleep(.05)
        raise RuntimeError("Test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.http.should_exit = True
        cls.thread.join(5)
        cls.temp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.status, response.headers, response.read()

    def upload(self, task_id, filename="song.wav"):
        body = (f'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                'Content-Type: audio/wav\r\n\r\nfixture\r\n--test-boundary--\r\n').encode()
        request = urllib.request.Request(self.base + "/api/v1/separate/async", data=body, headers={
            "Content-Type": "multipart/form-data; boundary=test-boundary", "X-Request-ID": task_id,
        })
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def wait_task(self, task_id):
        for _ in range(100):
            data = json.loads(self.get("/api/v1/task/" + task_id)[2])
            if data["status"] in {"completed", "failed"}:
                return data
            time.sleep(.03)
        self.fail("Task timed out")

    def test_static_page_and_config(self):
        status, headers, content = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Ordinary Muse", content)
        self.assertIn(b'href="./style.css"', content)
        self.assertIn(b'src="./app.js"', content)
        self.assertIn("text/css", self.get("/style.css")[1]["Content-Type"])
        self.assertIn("javascript", self.get("/app.js")[1]["Content-Type"])
        self.assertEqual(self.get("/assets/app.js")[0], 200)
        self.assertEqual(self.get("/assets/style.css")[0], 200)
        config = json.loads(self.get("/api/v1/web-config")[2])
        self.assertIn(".wav", config["allowed_extensions"])
        self.assertEqual(config["max_file_size_mb"], server.MAX_FILE_SIZE_MB)

    def test_upload_poll_download_and_idempotency(self):
        task_id = "web-test-success"
        self.assertEqual(self.upload(task_id)["task_id"], task_id)
        self.assertTrue(self.upload(task_id)["reused"])
        task = self.wait_task(task_id)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(len(task["stems"]), 4)
        status, _, content = self.get(f"/api/v1/task/{task_id}/stem/vocals?format=wav")
        self.assertEqual(status, 200)
        self.assertTrue(content.startswith(b"RIFF"))
        media_url = self.base + f"/api/v1/task/{task_id}/stem/vocals?format=wav"
        for byte_range, expected in [("bytes=0-3", content[:4]), ("bytes=-10", content[-10:])]:
            with urllib.request.urlopen(urllib.request.Request(media_url, headers={"Range": byte_range})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), expected)
                self.assertEqual(response.headers["Accept-Ranges"], "bytes")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(media_url, headers={"Range": "bytes=999999999-"}))
        self.assertEqual(error.exception.code, 416)
        import zipfile
        archive = zipfile.ZipFile(io.BytesIO(self.get(f"/api/v1/task/{task_id}/download")[2]))
        self.assertEqual(len(archive.namelist()), 4)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get(f"/api/v1/task/{task_id}/stem/nonexistent")
        self.assertEqual(error.exception.code, 404)

    def test_async_jobs_are_serial(self):
        for index in range(3):
            self.upload(f"serial-{index}")
        for index in range(3):
            self.assertEqual(self.wait_task(f"serial-{index}")["status"], "completed")
        self.assertEqual(fake.peak, 1)

    def test_invalid_input_and_missing_task(self):
        for filename in ["song.exe", "../song.wav"]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.upload("invalid", filename)
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get("/api/v1/task/does-not-exist")
        self.assertEqual(error.exception.code, 404)

    def test_failure_is_queryable(self):
        self.upload("failure", "broken.wav")
        task = self.wait_task("failure")
        self.assertEqual(task["status"], "failed")
        self.assertIn("Invalid test audio", task["error"])
        self.assertIn("failed_at", task)
        self.assertIn("expires_at", task)

    def test_cleanup_retries_disk_failure_and_keeps_active_jobs(self):
        self.upload("cleanup-retry")
        self.wait_task("cleanup-retry")
        server.tasks.update("cleanup-retry", expires_at=(utcnow() - timedelta(seconds=1)).isoformat())
        server.tasks.create("cleanup-active", "pending.wav")
        server.tasks.update("cleanup-active", status="queued",
                            created_at=(utcnow() - timedelta(days=7)).isoformat())
        with patch.object(server.shutil, "rmtree", side_effect=PermissionError("test disk error")):
            server.cleanup_expired_tasks()
        self.assertTrue((server.OUTPUT_BASE / "cleanup-retry").exists())
        self.assertEqual(server.tasks["cleanup-retry"]["status"], "completed")
        server.cleanup_expired_tasks()
        self.assertFalse((server.OUTPUT_BASE / "cleanup-retry").exists())
        self.assertEqual(server.tasks["cleanup-retry"]["status"], "expired")
        self.assertEqual(server.tasks["cleanup-active"]["status"], "queued")

    def test_restart_restores_results_and_cleans_expired_files(self):
        from datetime import datetime
        self.upload("restart-survivor")
        original = self.wait_task("restart-survivor")
        self.assertAlmostEqual((datetime.fromisoformat(original["expires_at"]) -
                                datetime.fromisoformat(original["completed_at"])).total_seconds(),
                               server.AUTO_CLEANUP_HOURS * 3600)
        self.upload("restart-expired")
        self.wait_task("restart-expired")
        server.tasks.update("restart-expired", expires_at=(utcnow() - timedelta(seconds=1)).isoformat())
        # Results must be unavailable at the exact deadline, before physical cleanup.
        for suffix in ["", "/download", "/stem/vocals"]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get("/api/v1/task/restart-expired" + suffix)
            self.assertEqual(error.exception.code, 410)
        self.assertTrue((server.OUTPUT_BASE / "restart-expired").exists())
        server.tasks.create("restart-interrupted", "unfinished.wav")
        server.tasks.update("restart-interrupted", status="processing")
        cls = type(self)
        cls.http.should_exit = True
        cls.thread.join(5)
        self.assertFalse(cls.thread.is_alive())
        # Discard all server metadata references and restart the application lifespan.
        server.tasks = None
        cls.http = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1",
                                                port=int(cls.base.rsplit(":", 1)[1]), log_level="error"))
        cls.thread = threading.Thread(target=cls.http.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.http.started:
                break
            time.sleep(.05)
        self.assertTrue(cls.http.started)
        restored = json.loads(self.get("/api/v1/task/restart-survivor")[2])
        self.assertEqual(restored, original)
        self.assertTrue(self.get("/api/v1/task/restart-survivor/stem/vocals")[2].startswith(b"RIFF"))
        self.assertFalse((server.OUTPUT_BASE / "restart-expired").exists())
        self.assertEqual(server.tasks["restart-expired"]["status"], "expired")
        interrupted = json.loads(self.get("/api/v1/task/restart-interrupted")[2])
        self.assertEqual(interrupted["status"], "failed")
        self.assertIn("重启", interrupted["error"])


class TaskStoreTests(unittest.TestCase):
    def test_retention_starts_at_completion_and_survives_new_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite3"
            store = TaskStore(path, 24)
            store.create("long-job", "song.wav")
            finished = utcnow()
            store.update("long-job", created_at=(finished - timedelta(days=3)).isoformat(), status="processing")
            self.assertFalse(store.is_expired(store["long-job"], finished))
            task = store.finish("long-job", "completed", now=finished, stems={"vocals": "vocals.wav"})
            reopened = TaskStore(path, 1)  # Config changes must not shorten old deadlines.
            self.assertEqual(reopened["long-job"], task)
            self.assertFalse(reopened.is_expired(task, finished + timedelta(hours=24, microseconds=-1)))
            self.assertTrue(reopened.is_expired(task, finished + timedelta(hours=24)))
            self.assertFalse(reopened.create("long-job", "duplicate.wav"))

    def test_all_interrupted_states_are_recovered_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.sqlite3", 24)
            for state in ["uploading", "queued", "processing"]:
                store.create(state, "song.wav")
                store.update(state, status=state)
            store.recover_interrupted()
            original = store.snapshot()
            store.recover_interrupted()
            self.assertEqual(store.snapshot(), original)
            self.assertTrue(all(t["status"] == "failed" and t.get("expires_at") for t in original.values()))


if __name__ == "__main__":
    if "--preview" in sys.argv:
        with tempfile.TemporaryDirectory() as directory:
            server.OUTPUT_BASE = Path(directory)
            uvicorn.run(server.app, host="127.0.0.1", port=8765, log_level="warning")
    else:
        unittest.main()
