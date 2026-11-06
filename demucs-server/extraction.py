"""Extract the first audio stream from a local video using FFmpeg."""
import json
import subprocess
from pathlib import Path

EXPORT_FORMATS = {
    "m4a": ("aac", "audio/mp4"),
    "wav": ("pcm_s24le", "audio/wav"),
    "aiff": ("pcm_s24be", "audio/aiff"),
    "caf": ("pcm_f32le", "audio/x-caf"),
}


def extract_audio(input_path, output_dir, export_format):
    if export_format not in EXPORT_FORMATS:
        raise ValueError("不支持的导出格式")
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
             "-select_streams", "a:0", "-show_entries", "stream=channels,sample_rate",
             "-of", "json", str(input_path)], capture_output=True, timeout=30, check=True)
        streams = json.loads(probe.stdout).get("streams", [])
        if not streams:
            raise ValueError("视频中没有可提取的音轨，请选择包含声音的视频。")
        channels = min(2, max(1, int(streams[0].get("channels", 2))))
        rate = int(streams[0].get("sample_rate", 44100))
        if export_format == "m4a":
            rate = 44100 if rate <= 44100 else 48000
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"audio.{export_format}"
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
                   "-i", str(input_path), "-map", "0:a:0", "-vn", "-map_metadata", "-1",
                   "-ac", str(channels), "-ar", str(rate), "-c:a", EXPORT_FORMATS[export_format][0]]
        if export_format == "m4a":
            command += ["-b:a", "160k" if channels == 1 else "320k", "-movflags", "+faststart"]
        subprocess.run(command + [str(target)], capture_output=True, timeout=600, check=True)
        preview = target
        if export_format != "m4a":
            preview = output_dir / "preview.m4a"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(target),
                            "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                            "-movflags", "+faststart", str(preview)],
                           capture_output=True, timeout=600, check=True)
        return {"audio_file": target.name, "preview_file": preview.name,
                "export_format": export_format}
    except subprocess.TimeoutExpired as error:
        raise ValueError("视频提音超时，请尝试较短的视频。") from error
    except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise ValueError("无法解码此视频，请确认文件完整并使用常见视频格式。") from error
