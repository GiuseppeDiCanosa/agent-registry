#!/usr/bin/env python3
"""Web-app FastAPI per il monitoraggio e la gestione del registry agent-registry.

Dashboard operativa (D10): vista giornaliera di default, filtri
data/provider/status, azioni (kill, force-release lock, cleanup) e vista wiki.
La UI filtra lato client sul payload SSE (che contiene tutte le sessioni);
gli endpoint REST con filtri server-side restano disponibili per la CLI/curl.

Endpoint:
    GET  /api/registry                 → retrocompat: tutte le sessioni
    GET  /api/registry/stream          → SSE: payload completo ogni 5s
    GET  /api/sessions                 → filtri date/provider/status/all
    GET  /api/locks                    → lock attivi (con flag stale)
    GET  /api/sync                     → stato git-sync
    GET  /api/wiki                     → ricerca FTS (q vuoto → ultime entry)
    GET  /api/wiki/{entry_id}          → dettaglio entry
    POST /api/sessions/{id}/kill       → kill reale via PID o stop logico (D6)
    POST /api/locks/force-release      → rilascio forzato lock (409 se non stale)
    POST /api/cleanup                  → cleanup sessioni zombie
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Permette di importare i manager dalla directory scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))
import lock_manager
import registry_manager
import sync_manager
import wiki_manager
from registry_manager import get_registry_path, load_agents

app = FastAPI(title="Agent Registry Dashboard")

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _today_rome() -> str:
    """Data odierna (YYYY-MM-DD) in timezone Roma."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")
    except Exception:
        from datetime import datetime

        return datetime.now().astimezone().strftime("%Y-%m-%d")


def _filter_sessions(
    date: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    all_sessions: bool = False,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Filtra le sessioni per data/provider/status.

    Default (nessun parametro): data odierna (Roma) + status OnWorking.
    Con `status` ma senza `date`: tutte le date per quello status.
    Con `all_sessions=True`: nessun default di data/status.

    Restituisce (sessioni, data_effettiva, status_effettivo).
    """
    agents = load_agents()
    effective_date = date
    effective_status = status
    if effective_date is None and not all_sessions and effective_status is None:
        effective_date = _today_rome()
        effective_status = "OnWorking"
    if effective_date:
        agents = [
            a for a in agents if str(a.get("started_at") or "").startswith(effective_date)
        ]
    if provider:
        agents = [a for a in agents if a.get("provider") == provider]
    if effective_status:
        agents = [a for a in agents if a.get("status") == effective_status]
    return agents, effective_date, effective_status


def _dashboard_payload() -> dict[str, Any]:
    """Payload completo per la UI: sessioni, lock attivi, stato sync."""
    return {
        "registry_path": str(get_registry_path()),
        "agents": load_agents(),
        "locks": _list_locks(),
        "sync": _sync_status(),
    }


def _list_locks() -> list[dict[str, Any]]:
    """Lock attivi: scansione della dir locks/ con risoluzione del path.

    Il file `<hash>.lock` contiene solo `session_id|timestamp`: il path viene
    risolto (best-effort) incrociando l'hash con i `do_not_touch` delle
    sessioni note. Restituisce dict con path, session_id, age, stale.
    """
    locks_dir = registry_manager.get_registry_home() / "locks"
    locks: list[dict[str, Any]] = []
    if not locks_dir.is_dir():
        return locks
    # Mappa hash → path dai do_not_touch/space delle sessioni note
    hash_to_path: dict[str, str] = {}
    for agent in load_agents():
        for path in (agent.get("do_not_touch") or []) + (agent.get("space") or []):
            p = str(path)
            h = hashlib.sha256(os.path.abspath(p).encode("utf-8")).hexdigest()[:16]
            hash_to_path.setdefault(h, p)

    for lock_file in sorted(locks_dir.glob("*.lock")):
        info = lock_manager._read_info(lock_file)
        if not info:
            continue
        age = time.time() - info.get("timestamp", 0)
        locks.append(
            {
                "path": hash_to_path.get(lock_file.stem, f"(hash {lock_file.stem})"),
                "session_id": info.get("session_id"),
                "age": round(age, 1),
                "stale": age > lock_manager.DEFAULT_TIMEOUT,
            }
        )
    return locks


def _sync_status() -> dict[str, Any]:
    """Stato del git-sync (best-effort, mai bloccante)."""
    try:
        return sync_manager.get_sync_status(registry_manager.get_registry_home())
    except Exception as exc:
        return {"enabled": False, "error": str(exc)}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve la pagina principale."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<html><body><h1>Agent Registry Dashboard</h1><p>static/index.html not found</p></body></html>"


@app.get("/api/registry")
async def get_registry() -> dict:
    """Restituisce lo stato corrente del registry come JSON (retrocompatibilità)."""
    return {
        "registry_path": str(get_registry_path()),
        "agents": load_agents(),
    }


@app.get("/api/sessions")
async def get_sessions(
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD (Roma)"),
    provider: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    all: bool = Query(default=False, description="bypassa il default data/status"),
) -> dict:
    """Sessioni filtrate. Default: data odierna (Roma) + status OnWorking."""
    sessions, effective_date, effective_status = _filter_sessions(
        date=date, provider=provider, status=status, all_sessions=all
    )
    return {
        "registry_path": str(get_registry_path()),
        "date": effective_date,
        "provider": provider,
        "status": effective_status,
        "count": len(sessions),
        "sessions": sessions,
    }


@app.get("/api/locks")
async def get_locks() -> dict:
    """Elenca i lock attivi con flag stale."""
    locks = _list_locks()
    return {"count": len(locks), "locks": locks}


@app.get("/api/sync")
async def get_sync() -> dict:
    """Stato del git-sync della home del registry."""
    return _sync_status()


class KillRequest(BaseModel):
    """Body opzionale del kill: force salta la verifica cmdline anti-riuso (D6)."""

    force: bool = False


@app.post("/api/sessions/{session_id}/kill")
async def post_kill(session_id: str, body: Optional[KillRequest] = None) -> JSONResponse:
    """Termina una sessione: kill reale via PID se possibile, altrimenti stop logico.

    La risposta indica quale dei due è avvenuto (`terminated`: true = processo
    reale terminato, false = stop logico) oltre alla nota esplicativa.
    """
    force = body.force if body else False
    result = registry_manager.kill_session(session_id, force=force)
    if not result.get("killed"):
        return JSONResponse(status_code=404, content=result)
    result["action"] = "process_terminated" if result.get("terminated") else "logical_stop"
    return JSONResponse(content=result)


class ForceReleaseRequest(BaseModel):
    """Body del force-release: confirm obbligatorio per lock non stale."""

    path: str
    session_id: str = ""
    confirm: bool = False


@app.post("/api/locks/force-release")
async def post_force_release(body: ForceReleaseRequest) -> JSONResponse:
    """Rilascia forzatamente un lock.

    Lock stale (heartbeat scaduto): rilascio immediato e pulizia del
    `do_not_touch` della sessione owner. Lock non stale: richiede
    `confirm: true`, altrimenti 409 con avviso (la UI chiede conferma e
    riprova). Se il body specifica un session_id diverso dall'owner reale,
    risponde 409 (protezione contro rilasci sul lock sbagliato).
    """
    status = lock_manager.is_locked(body.path)
    if status.get("locked"):
        owner = str(status.get("session_id") or "")
        if body.session_id and body.session_id != owner:
            return JSONResponse(
                status_code=409,
                content={
                    "released": False,
                    "error": "owner mismatch",
                    "owner": owner,
                },
            )
        if not body.confirm:
            return JSONResponse(
                status_code=409,
                content={
                    "released": False,
                    "warning": (
                        f"Lock attivo (heartbeat fresco, age "
                        f"{status.get('age', 0):.0f}s) di {owner}: "
                        "rilasciare comunque?"
                    ),
                    "owner": owner,
                    "stale": False,
                },
            )
        result = lock_manager.release_lock(body.path, owner)
        result["owner"] = owner
        result["stale"] = False
        return JSONResponse(content=result)

    stale_owner = status.get("stale_owner")
    if stale_owner:
        # is_locked ha già rimosso il file stale; release ripulisce do_not_touch
        result = lock_manager.release_lock(body.path, str(stale_owner))
        result["owner"] = stale_owner
        result["stale"] = True
        return JSONResponse(content=result)

    return JSONResponse(
        status_code=404,
        content={"released": False, "error": "lock non trovato", "path": body.path},
    )


@app.post("/api/cleanup")
async def post_cleanup() -> dict:
    """Cleanup delle sessioni zombie: marca Stop e rilascia i lock residui."""
    cleaned = registry_manager.cleanup_sessions()
    return {"stopped": cleaned, "count": len(cleaned)}


@app.get("/api/wiki")
async def get_wiki(
    q: str = Query(default="", description="testo ricerca FTS"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Ricerca wiki: FTS su router/cosa_fatto/bug_trovati; q vuoto → ultime entry."""
    q = q.strip()
    if q:
        entries = wiki_manager.search(q, limit=limit)
    else:
        entries = wiki_manager.list_entries(limit=limit)
    return {"query": q, "count": len(entries), "entries": entries}


@app.get("/api/wiki/{entry_id}")
async def get_wiki_entry(entry_id: int) -> JSONResponse:
    """Dettaglio completo di un wiki entry (tutti i campi + context_md)."""
    entry = wiki_manager.get_entry(entry_id)
    if entry is None:
        return JSONResponse(
            status_code=404, content={"error": f"entry {entry_id} non trovato"}
        )
    return JSONResponse(content=entry)


@app.get("/api/registry/stream")
async def stream_registry() -> StreamingResponse:
    """Server-Sent Events: payload completo (sessioni, lock, sync) ogni 5s.

    La UI applica i filtri lato client su questo payload.
    """

    async def event_generator() -> asyncio.AsyncIterator[str]:
        last_payload: str | None = None
        while True:
            try:
                payload = json.dumps(_dashboard_payload(), ensure_ascii=False)
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
