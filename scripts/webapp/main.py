#!/usr/bin/env python3
"""Web-app FastAPI per il monitoraggio del registry agent-registry."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Permette di importare registry_manager dalla directory scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))
from registry_manager import get_registry_path, load_agents

app = FastAPI(title="Agent Registry Dashboard")

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve la pagina principale."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<html><body><h1>Agent Registry Dashboard</h1><p>static/index.html not found</p></body></html>"


@app.get("/api/registry")
async def get_registry() -> dict:
    """Restituisce lo stato corrente del registry come JSON."""
    return {
        "registry_path": str(get_registry_path()),
        "agents": load_agents(),
    }


@app.get("/api/registry/stream")
async def stream_registry() -> StreamingResponse:
    """Server-Sent Events: aggiorna il client quando il registry cambia."""

    async def event_generator() -> asyncio.AsyncIterator[str]:
        last_payload: str | None = None
        while True:
            try:
                data = {
                    "registry_path": str(get_registry_path()),
                    "agents": load_agents(),
                }
                payload = json.dumps(data, ensure_ascii=False)
                if payload != last_payload:
                    last_payload = payload
                    yield f"data: {payload}\n\n"
                await asyncio.sleep(5)
            except Exception as exc:
                error_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
                yield f"data: {error_payload}\n\n"
                await asyncio.sleep(5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
