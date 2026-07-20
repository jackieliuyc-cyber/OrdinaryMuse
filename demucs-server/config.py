import os

MODEL_NAME = os.getenv("DEMUCS_MODEL", "htdemucs_ft")
DEVICE = os.getenv("DEMUCS_DEVICE", "auto")
AAC_BITRATE = os.getenv("AAC_BITRATE", "256k")
AAC_MAX_WORKERS = max(1, int(os.getenv("AAC_MAX_WORKERS", "2")))

HOST = os.getenv("SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("SERVER_PORT", "8000"))

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}

AUTO_CLEANUP_HOURS = int(os.getenv("AUTO_CLEANUP_HOURS", "24"))
