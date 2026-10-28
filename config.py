"""CPU-first settings for a laptop: small model, short context, unload after use."""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
PROJECT_ROOT = Path(__file__).resolve().parent

# Load local .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

# Email / SMTP Configuration
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


def _windows_desktop() -> Path:
    override = os.environ.get("DRL_DESKTOP")
    if override:
        return Path(override)
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 16, None, 0, buf)
        p = Path(buf.value)
        if p.exists():
            return p
    except Exception:
        pass
    return HOME / "Desktop"


DESKTOP = _windows_desktop()
DOWNLOADS = HOME / "Downloads"
if (HOME / "OneDrive" / "Documents").exists():
    DOCUMENTS = HOME / "OneDrive" / "Documents"
elif (HOME / "Documents").exists():
    DOCUMENTS = HOME / "Documents"
else:
    DOCUMENTS = HOME / "Documents"

if (HOME / "OneDrive" / "Pictures").exists():
    PICTURES = HOME / "OneDrive" / "Pictures"
elif (HOME / "Pictures").exists():
    PICTURES = HOME / "Pictures"
else:
    PICTURES = HOME / "Pictures"


# Default is a local ~1GB Qwen2.5-Coder import (`drl-coder`). llama3.1 8B is too hot/large.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("DRL_MODEL", "drl-coder")

# Leave some cores free so the laptop stays usable and cooler.
CPU_COUNT = os.cpu_count() or 4
NUM_THREAD = max(2, min(6, CPU_COUNT - 2))

# Generous context for multi-step agent tasks with code files and outputs
NUM_CTX = 8192
NUM_PREDICT = 1024
TEMPERATURE = 0.1

# Force CPU even if a GPU exists.
# Stay loaded during a multi-step task (reloading every tool call overheats the laptop).
# Unload after the task finishes.
NUM_GPU = 0
KEEP_ALIVE_DURING_TASK = "10m"
KEEP_ALIVE_AFTER_TASK = "0s"

MAX_STEPS = 12
COMMAND_TIMEOUT_SEC = 60
PYTHON_TIMEOUT_SEC = 30

def _allowed_roots() -> tuple[Path, ...]:
    roots = [DESKTOP, DOWNLOADS, DOCUMENTS, DESKTOP / "remote_downloads"]
    for extra in (
        HOME / "Desktop",
        HOME / "OneDrive" / "Desktop",
        HOME / "Downloads",
        HOME / "Documents",
        HOME / "OneDrive" / "Documents",
        PROJECT_ROOT,
        HOME,
        DESKTOP / "remote_downloads",
    ):
        if extra.exists():
            roots.append(extra)

    seen: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.append(resolved)
    return tuple(seen)


ALLOWED_ROOTS = _allowed_roots()

CONFIRM_PIP = os.environ.get("DRL_CONFIRM_PIP", "0").lower() in {"1", "true"}
CONFIRM_SHELL = os.environ.get("DRL_CONFIRM_SHELL", "1").lower() in {"1", "true"}
