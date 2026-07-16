#!/usr/bin/env python3
"""Manager per i lock a livello filesystem usati da agent-registry.

Ogni lock è rappresentato da un file in ~/Desktop/agent-registry/locks/.
Il file contiene "session_id|timestamp_epoch".
L'accesso al file è protetto da fcntl per garantire atomicità.
Se un agente crasha, il timeout/heartbeat rende il lock stale.

Nota su macOS: fcntl non permette a un processo di acquisire due lock
(esclusivi o condivisi) sullo stesso file. Per questo motivo le operazioni
di lettura/heartbeat non acquisiscono lock; si affidano al fatto che solo
l'owner scrive nel file. Il lock esclusivo viene usato solo in fase di
acquisizione per garantire atomicità tra processi diversi.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT = 120  # secondi
LOCK_DIR = Path.home() / "Desktop" / "agent-registry" / "locks"

# fd aperti per lock acquisiti nel processo corrente (path -> fd).
# Serve per poter rilasciare il lock fcntl senza dover riacquisire il file.
_OPEN_LOCK_FDS: dict[str, int] = {}


def _lock_file(path: str) -> Path:
    """Restituisce il file di lock per un dato path."""
    abs_path = os.path.abspath(path)
    h = hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:16]
    return LOCK_DIR / f"{h}.lock"


def _ensure_lock_dir() -> None:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)


def _read_info(lock_file: Path) -> dict:
    try:
        with open(lock_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if "|" not in content:
            return {}
        session_id, ts = content.split("|", 1)
        return {"session_id": session_id, "timestamp": float(ts)}
    except Exception:
        return {}


def _write_info(fd: int, session_id: str) -> None:
    payload = f"{session_id}|{time.time()}".encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload)
    os.fsync(fd)


def _is_stale(info: dict, timeout: int) -> bool:
    return info and (time.time() - info.get("timestamp", 0)) > timeout


def is_locked(path: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Verifica se un path è locked. Ritorna {locked, session_id?, age?, stale_owner?}."""
    lock_file = _lock_file(path)
    if not lock_file.exists():
        return {"locked": False}
    info = _read_info(lock_file)
    if _is_stale(info, timeout):
        try:
            lock_file.unlink()
        except FileNotFoundError:
            pass
        return {"locked": False, "stale_owner": info.get("session_id")}
    return {
        "locked": True,
        "session_id": info.get("session_id"),
        "age": time.time() - info.get("timestamp", 0),
    }


def acquire_lock(
    path: str, session_id: str, timeout: int = DEFAULT_TIMEOUT
) -> dict:
    """Prova ad acquisire il lock su path per session_id.

    Ritorna:
      - {'locked': True, 'owner': session_id} se acquisito
      - {'locked': False, 'session_id': ..., 'age': ...} se già occupato
      - {'locked': False, 'error': ...} per altri errori
    """
    _ensure_lock_dir()
    lock_file = _lock_file(path)

    # Stesso processo: fcntl non blocca se riacquiriamo lo stesso file.
    # Usiamo la cache interna per sapere se il path è già locked.
    if path in _OPEN_LOCK_FDS:
        info = _read_info(lock_file)
        owner = info.get("session_id")
        if owner == session_id:
            return {"locked": True, "owner": session_id, "note": "already locked by you"}
        return {
            "locked": False,
            "session_id": owner,
            "age": time.time() - info.get("timestamp", 0),
        }

    fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            os.close(fd)
            status = is_locked(path, timeout)
            return {
                "locked": False,
                "session_id": status.get("session_id"),
                "age": status.get("age"),
            }
        # Lock acquisito; controlla se il contenuto è stale e sovrascrivilo
        info = _read_info(lock_file)
        stale_owner = info.get("session_id") if _is_stale(info, timeout) else None
        _write_info(fd, session_id)
        _OPEN_LOCK_FDS[path] = fd
        result = {"locked": True, "owner": session_id}
        if stale_owner:
            result["stale_owner"] = stale_owner
        return result
    except Exception as e:
        try:
            os.close(fd)
        except Exception:
            pass
        return {"locked": False, "error": str(e)}


def release_lock(path: str, session_id: str) -> dict:
    """Rilascia il lock su path se session_id è l'owner."""
    lock_file = _lock_file(path)
    if not lock_file.exists():
        # Rimuovi comunque il fd dalla cache se presente
        fd = _OPEN_LOCK_FDS.pop(path, None)
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        return {"released": True, "note": "lock file not found"}

    info = _read_info(lock_file)
    if info and info.get("session_id") != session_id:
        return {
            "released": False,
            "error": "not owner",
            "current_owner": info.get("session_id"),
        }

    fd = _OPEN_LOCK_FDS.pop(path, None)
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass

    try:
        lock_file.unlink()
    except FileNotFoundError:
        pass
    return {"released": True}


def heartbeat(path: str, session_id: str) -> dict:
    """Aggiorna il timestamp del lock (heartbeat)."""
    lock_file = _lock_file(path)
    if not lock_file.exists():
        return {"ok": False, "error": "lock not found"}
    info = _read_info(lock_file)
    if info and info.get("session_id") != session_id:
        return {
            "ok": False,
            "error": "not owner",
            "current_owner": info.get("session_id"),
        }
    fd = _OPEN_LOCK_FDS.get(path)
    if fd is not None:
        _write_info(fd, session_id)
        return {"ok": True}
    # Se non abbiamo il fd aperto, apriamo e scriviamo (caso loop heartbeat riavviato)
    try:
        with open(lock_file, "r+", encoding="utf-8") as f:
            _write_info(f.fileno(), session_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def heartbeat_loop(
    path: str, session_id: str, interval: int = 30
) -> None:
    """Loop di heartbeat da eseguire in background."""
    while True:
        result = heartbeat(path, session_id)
        if not result.get("ok"):
            print(f"Heartbeat failed for {path}: {result}", file=sys.stderr)
            break
        time.sleep(interval)


def check_and_warn(path: str, session_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Controlla se path è libero; se occupato restituisce avviso senza bloccare.

    Questa funzione è usata per implementare l'avviso informativo.
    """
    status = is_locked(path, timeout)
    if status.get("locked"):
        owner = status.get("session_id")
        if owner == session_id:
            return {"ok": True, "note": "already locked by you"}
        return {
            "ok": False,
            "warning": f"⚠️  Il file/area '{path}' è locked da {owner} (age: {status.get('age', 0):.0f}s).",
            "owner": owner,
        }
    return {"ok": True, "note": "not locked"}


def guarded_acquire(
    path: str, session_id: str, timeout: int = DEFAULT_TIMEOUT
) -> dict:
    """Acquisisce il lock e blocca effettivamente se già occupato.

    Se acquisito, restituisce {'ok': True, 'owner': session_id}.
    Se occupato, restituisce {'ok': False, 'blocked': True, 'owner': ...}.
    """
    result = acquire_lock(path, session_id, timeout)
    if result.get("locked"):
        return {"ok": True, "owner": session_id, **result}
    return {
        "ok": False,
        "blocked": True,
        "owner": result.get("session_id"),
        "message": f"🚫 Bloccato: '{path}' è in uso da {result.get('session_id')}. Non modificare.",
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "acquire":
        path, sid = sys.argv[2], sys.argv[3]
        print(acquire_lock(path, sid))
    elif cmd == "release":
        path, sid = sys.argv[2], sys.argv[3]
        print(release_lock(path, sid))
    elif cmd == "check":
        path = sys.argv[2]
        sid = sys.argv[3] if len(sys.argv) > 3 else ""
        print(check_and_warn(path, sid))
    elif cmd == "heartbeat":
        path, sid = sys.argv[2], sys.argv[3]
        print(heartbeat(path, sid))
    elif cmd == "heartbeat-loop":
        path, sid = sys.argv[2], sys.argv[3]
        interval = int(sys.argv[4]) if len(sys.argv) > 4 else 30
        heartbeat_loop(path, sid, interval)
    else:
        print(
            "Uso: lock_manager.py acquire <path> <session_id>\n"
            "     lock_manager.py release <path> <session_id>\n"
            "     lock_manager.py check <path> [session_id]\n"
            "     lock_manager.py heartbeat <path> <session_id>\n"
            "     lock_manager.py heartbeat-loop <path> <session_id> [interval_sec]"
        )
