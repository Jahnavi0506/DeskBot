"""FastAPI Web Server for DRL Coding Agent.

Provides REST API and Server-Sent Events (SSE) for real-time agent execution,
folder analysis & execution, Desktop file management, and Ollama controls.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import config
import doc_parser
import ollama_client
import tools
from safety import resolve_user_path

app = FastAPI(title="DRL Coding Agent Web UI", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

STATIC_DIR = config.PROJECT_ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

UPLOAD_DIR = config.DESKTOP / "uploaded_files"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory store for active streaming tasks
TASK_QUEUES: dict[str, queue.Queue[dict[str, Any]]] = {}
TASK_RESULTS: dict[str, dict[str, Any]] = {}


class RunRequest(BaseModel):
    prompt: str


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Web UI frontend not found.")
    return FileResponse(
        str(index_file),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    ollama_online = ollama_client.is_up()
    models: list[str] = []
    if ollama_online:
        try:
            models = ollama_client.list_models()
        except Exception:
            pass
    return {
        "ollama_online": ollama_online,
        "models": models,
        "model": config.MODEL,
        "desktop": str(config.DESKTOP),
        "cpu_count": config.CPU_COUNT,
        "num_thread": config.NUM_THREAD,
    }


@app.post("/api/unload")
async def unload_model() -> dict[str, str]:
    ollama_client.unload()
    return {"status": "success", "message": f"Model {config.MODEL} unloaded from RAM."}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """Save an uploaded image or document to Desktop/uploaded_files and extract text."""
    try:
        filename = Path(file.filename or "uploaded_file").name
        dest = UPLOAD_DIR / filename
        content = await file.read()
        dest.write_bytes(content)

        extracted_text = doc_parser.extract_text_from_file(dest)
        return {
            "status": "success",
            "filename": filename,
            "path": str(dest),
            "size": len(content),
            "extracted_text": extracted_text,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")


@app.post("/api/run")
async def start_run(req: RunRequest) -> dict[str, str]:
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    full_prompt = prompt

    task_id = str(uuid.uuid4())
    q: queue.Queue[dict[str, Any]] = queue.Queue()
    TASK_QUEUES[task_id] = q

    def worker() -> None:
        def callback(event: dict[str, Any]) -> None:
            q.put(event)

        try:
            import importlib, sys
            if 'tools' in sys.modules:
                importlib.reload(sys.modules['tools'])
            if 'agent' in sys.modules:
                importlib.reload(sys.modules['agent'])
            summary = agent.run_task(full_prompt, event_callback=callback)
            TASK_RESULTS[task_id] = {"status": "done", "summary": summary}
        except Exception as exc:
            q.put({"type": "error", "message": str(exc)})
            TASK_RESULTS[task_id] = {"status": "error", "summary": str(exc)}
        finally:
            q.put({"type": "stream_end"})
            ollama_client.unload()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return {"task_id": task_id}


@app.get("/api/stream/{task_id}")
async def stream_task(task_id: str) -> StreamingResponse:
    if task_id not in TASK_QUEUES:
        raise HTTPException(status_code=404, detail="Task ID not found.")

    q = TASK_QUEUES[task_id]

    async def event_generator():
        while True:
            try:
                event = await asyncio.to_thread(q.get, timeout=0.5)
                if event.get("type") == "stream_end":
                    yield f"event: end\ndata: {json.dumps({'status': 'finished'})}\n\n"
                    break
                event_type = event.get("type", "message")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
                await asyncio.sleep(0.1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/files")
async def list_desktop_files() -> list[dict[str, Any]]:
    desktop = config.DESKTOP
    if not desktop.exists():
        return []
    items: list[dict[str, Any]] = []
    for p in desktop.iterdir():
        if p.name.startswith(".") or p.name.startswith("drl_agent_venv"):
            continue
        try:
            stat = p.stat()
            items.append(
                {
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size": stat.st_size if p.is_file() else 0,
                    "modified": stat.st_mtime,
                    "path": str(p),
                }
            )
        except Exception:
            continue
    items.sort(key=lambda x: x["modified"], reverse=True)
    return items[:50]


@app.get("/api/file-content")
async def get_file_content(path: str = Query(..., description="File path")) -> dict[str, str]:
    try:
        target = resolve_user_path(path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"name": target.name, "path": str(target), "content": text}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _find_port(start_port: int = 8000) -> int:
    import socket
    for port in range(start_port, start_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def main() -> None:
    port = _find_port(8000)
    print(f"Starting DRL Coding Agent Web Server on http://127.0.0.1:{port} ...")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
