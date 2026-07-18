import torch
import torchaudio
import logging
import numpy as np
import shutil
import subprocess
import time
import wave
from demucs.pretrained import get_model
from demucs.apply import apply_model
from pathlib import Path
from config import MODEL_NAME, DEVICE, AAC_BITRATE

logger = logging.getLogger("demucs-server")


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
        total_start = time.perf_counter()
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        load_start = time.perf_counter()
        logger.info("Loading audio: %s", input_path)
        wav, sr = self._load_audio(input_path, out.parent)
        logger.info(
            "Audio loaded: sample_rate=%s channels=%s frames=%s duration=%.2fs elapsed=%.2fs",
            sr,
            wav.shape[0],
            wav.shape[-1],
            wav.shape[-1] / sr if sr else 0,
            time.perf_counter() - load_start,
        )

        if sr != self.model.samplerate:
            resample_start = time.perf_counter()
            logger.info("Resampling audio: from=%s to=%s", sr, self.model.samplerate)
            wav = torchaudio.functional.resample(wav, sr, self.model.samplerate)
            logger.info("Resampling completed: elapsed=%.2fs", time.perf_counter() - resample_start)

        if wav.shape[0] == 1:
            wav = wav.repeat(2, 1)
        elif wav.shape[0] > 2:
            wav = wav[:2]

        ref = wav.mean(0)
        mean = ref.mean()
        std = ref.std()
        wav = (wav - mean) / (std + 1e-8)

        wav_input = wav.unsqueeze(0).to(self.device)
        inference_start = time.perf_counter()
        logger.info("Running Demucs inference: device=%s sources=%s", self.device, list(self.model.sources))
        with torch.no_grad():
            sources = apply_model(self.model, wav_input, device=self.device)
        logger.info("Demucs inference completed: elapsed=%.2fs", time.perf_counter() - inference_start)

        sources = sources * std + mean

        save_start = time.perf_counter()
        result = {}
        for i, name in enumerate(self.model.sources):
            stem_path = str(out / f"{name}.wav")
            stem_start = time.perf_counter()
            logger.info("Saving stem: name=%s path=%s", name, stem_path)
            self._save_wav(sources[0, i].cpu(), stem_path, self.model.samplerate)
            logger.info("Stem saved: name=%s elapsed=%.2fs", name, time.perf_counter() - stem_start)
            result[name] = stem_path
        logger.info("All stems saved: elapsed=%.2fs", time.perf_counter() - save_start)

        if self.device == "cuda":
            torch.cuda.empty_cache()

        logger.info("Separation pipeline completed: total_elapsed=%.2fs", time.perf_counter() - total_start)
        return result

    def _load_audio(self, input_path: str, work_dir: Path):
        direct_start = time.perf_counter()
        try:
            result = torchaudio.load(input_path)
            logger.info("torchaudio direct load completed: elapsed=%.2fs", time.perf_counter() - direct_start)
            return result
        except Exception as error:
            logger.warning(
                "torchaudio failed to load input directly: elapsed=%.2fs error=%s",
                time.perf_counter() - direct_start,
                error,
            )

            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError(
                    "无法读取该音频格式，且未找到 ffmpeg。请先安装 ffmpeg：brew install ffmpeg"
                ) from error

            converted_path = work_dir / f"{Path(input_path).stem}-demucs-input.wav"
            command = [
                ffmpeg,
                "-y",
                "-i", input_path,
                "-vn",
                "-ac", "2",
                "-ar", str(self.model.samplerate),
                "-sample_fmt", "s16",
                str(converted_path),
            ]
            convert_start = time.perf_counter()
            logger.info("Converting input with ffmpeg: %s", " ".join(command))
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode != 0:
                logger.error("ffmpeg conversion failed: %s", completed.stderr)
                raise RuntimeError(
                    f"ffmpeg 转码失败: {completed.stderr.strip() or completed.returncode}"
                ) from error

            logger.info("Converted input saved: path=%s elapsed=%.2fs", converted_path, time.perf_counter() - convert_start)
            converted_load_start = time.perf_counter()
            try:
                result = torchaudio.load(str(converted_path))
                logger.info("torchaudio converted WAV load completed: elapsed=%.2fs", time.perf_counter() - converted_load_start)
                return result
            except Exception as converted_error:
                logger.warning(
                    "torchaudio failed to load converted WAV: elapsed=%.2fs error=%s",
                    time.perf_counter() - converted_load_start,
                    converted_error,
                )
                logger.info("Falling back to ffmpeg PCM decoding")
                return self._load_audio_with_ffmpeg(input_path)

    def _load_audio_with_ffmpeg(self, input_path: str):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("无法读取音频，且未找到 ffmpeg。请先安装 ffmpeg：brew install ffmpeg")

        sample_rate = self.model.samplerate
        command = [
            ffmpeg,
            "-v", "error",
            "-i", input_path,
            "-vn",
            "-ac", "2",
            "-ar", str(sample_rate),
            "-f", "f32le",
            "-acodec", "pcm_f32le",
            "pipe:1",
        ]
        decode_start = time.perf_counter()
        logger.info("Decoding input to PCM with ffmpeg: %s", " ".join(command))
        completed = subprocess.run(command, capture_output=True)
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            logger.error("ffmpeg PCM decoding failed: %s", stderr)
            raise RuntimeError(f"ffmpeg PCM 解码失败: {stderr or completed.returncode}")

        audio = np.frombuffer(completed.stdout, dtype=np.float32)
        if audio.size == 0:
            raise RuntimeError("ffmpeg PCM 解码结果为空")
        if audio.size % 2 != 0:
            audio = audio[:-1]

        audio = audio.reshape(-1, 2).T.copy()
        wav = torch.from_numpy(audio)
        logger.info("ffmpeg PCM decoding completed: frames=%s elapsed=%.2fs", wav.shape[-1], time.perf_counter() - decode_start)
        return wav, sample_rate

    def _save_wav(self, wav: torch.Tensor, path: str, samplerate: int):
        audio = wav.detach().cpu().float()
        if audio.dim() == 1:
            audio = audio.unsqueeze(0)
        if audio.shape[0] > 2:
            audio = audio[:2]

        audio = torch.clamp(audio, -1.0, 1.0)
        pcm = (audio.numpy().T * 32767.0).astype(np.int16)

        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(audio.shape[0])
            wav_file.setsampwidth(2)
            wav_file.setframerate(samplerate)
            wav_file.writeframes(pcm.tobytes())

    def encode_aac_stems(self, stems: dict) -> dict:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("AAC 音轨编码需要 ffmpeg。请先安装：brew install ffmpeg")

        encoded = {}
        for name, wav_path in stems.items():
            m4a_path = str(Path(wav_path).with_suffix(".m4a"))
            command = [
                ffmpeg,
                "-y",
                "-v", "error",
                "-i", wav_path,
                "-vn",
                "-c:a", "aac",
                "-b:a", AAC_BITRATE,
                "-movflags", "+faststart",
                m4a_path,
            ]
            start = time.perf_counter()
            logger.info("Encoding AAC stem: name=%s bitrate=%s", name, AAC_BITRATE)
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"AAC 编码失败 ({name}): {completed.stderr.strip() or completed.returncode}"
                )
            logger.info(
                "AAC stem encoded: name=%s path=%s size=%d elapsed=%.2fs",
                name,
                m4a_path,
                os.path.getsize(m4a_path),
                time.perf_counter() - start,
            )
            encoded[name] = m4a_path
        return encoded

    @property
    def source_names(self) -> list:
        return list(self.model.sources)

    @property
    def sample_rate(self) -> int:
        return self.model.samplerate


separator = StemSeparator()
