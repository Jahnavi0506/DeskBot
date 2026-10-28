"""Install a small CPU model into Ollama.

The official Ollama registry often fails on this network (EOF). Prefer a Hugging Face
GGUF (~1GB, Qwen2.5-Coder 1.5B) imported as `drl-coder`.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import config
import ollama_client

GGUF_NAME = "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
GGUF_URL = (
    "https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/"
    + GGUF_NAME
)
LOCAL_NAME = "drl-coder"


def main() -> int:
    if not ollama_client.is_up():
        print("Starting Ollama if it is installed...")
        _try_start_ollama()
        for _ in range(20):
            if ollama_client.is_up():
                break
            time.sleep(1)
    if not ollama_client.is_up():
        print("Ollama API is not up on", config.OLLAMA_HOST)
        print("Open the Ollama app from the Start menu, then run this again.")
        return 1

    names = ollama_client.list_models()
    if any(LOCAL_NAME in n for n in names):
        print("Model already available:", LOCAL_NAME)
        return 0

    models_dir = config.PROJECT_ROOT / "models"
    models_dir.mkdir(exist_ok=True)
    gguf = models_dir / GGUF_NAME
    if not gguf.exists() or gguf.stat().st_size < 500_000_000:
        print("Downloading", GGUF_NAME, "from Hugging Face (~1GB)...")
        code = subprocess.call(
            [
                "curl.exe",
                "-L",
                "--retry",
                "10",
                "--retry-all-errors",
                "-C",
                "-",
                "-o",
                str(gguf),
                GGUF_URL,
            ]
        )
        if code != 0 or not gguf.exists():
            print("Download failed. Retry: python setup_model.py")
            return 1

    print("Importing into Ollama as", LOCAL_NAME, "...")
    ollama = _ollama_exe()
    result = subprocess.run(
        [str(ollama), "create", LOCAL_NAME, "-f", str(config.PROJECT_ROOT / "Modelfile")],
        cwd=str(config.PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        return 1
    print("Ready. Run:  python agent.py")
    return 0


def _ollama_exe() -> Path:
    candidates = [
        Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
    ]
    for exe in candidates:
        if exe.exists():
            return exe
    return Path("ollama")


def _try_start_ollama() -> None:
    exe = _ollama_exe()
    if exe.exists() or exe.name == "ollama":
        subprocess.Popen([str(exe), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
