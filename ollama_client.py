"""Talk to local Ollama with a small payload (CPU-friendly)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import config


class OllamaError(RuntimeError):
    pass


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": config.KEEP_ALIVE_DURING_TASK,
        "options": {
            "temperature": config.TEMPERATURE,
            "num_ctx": config.NUM_CTX,
            "num_predict": config.NUM_PREDICT,
            "num_thread": config.NUM_THREAD,
            "num_gpu": config.NUM_GPU,
        },
    }
    if tools:
        payload["tools"] = tools

    data = _post("/api/chat", payload, timeout=300)
    message = data.get("message") or {}
    return message


def unload() -> None:
    """Drop the model from RAM so the laptop can cool down."""
    try:
        _post(
            "/api/generate",
            {"model": config.MODEL, "keep_alive": config.KEEP_ALIVE_AFTER_TASK},
            timeout=30,
        )
    except OllamaError:
        pass


def list_models() -> list[str]:
    data = _get("/api/tags")
    return [m.get("name", "") for m in data.get("models", [])]


def pull_model(name: str) -> None:
    body = json.dumps({"name": name, "stream": True}).encode("utf-8")
    req = urllib.request.Request(
        config.OLLAMA_HOST + "/api/pull",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            last = ""
            while True:
                line = resp.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    raise OllamaError(str(data["error"]))
                status = data.get("status", "")
                completed = data.get("completed")
                total = data.get("total")
                if total:
                    pct = 100 * int(completed or 0) / int(total)
                    msg = f"{status} {pct:.0f}%"
                else:
                    msg = status
                if msg and msg != last:
                    print(msg, flush=True)
                    last = msg
    except urllib.error.URLError as exc:
        raise OllamaError(f"Pull failed: {exc}") from exc


def is_up() -> bool:
    try:
        _get("/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _get(path: str, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(config.OLLAMA_HOST + path, method="GET")
    return _send(req, timeout)


def _post(path: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.OLLAMA_HOST + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    return _send(req, timeout)


def _send(req: urllib.request.Request, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise OllamaError(
            f"Ollama returned HTTP {exc.code} {exc.reason}: {body.strip() or str(exc)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Cannot reach Ollama at {config.OLLAMA_HOST}. Is Ollama running? {exc}"
        ) from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Ollama returned invalid JSON: {raw[:400]}") from exc
