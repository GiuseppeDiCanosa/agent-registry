#!/usr/bin/env python3
# GENERATED FROM SPEC — DO NOT EDIT DIRECTLY
# Source: openspec/specs/file-locking/spec.md
"""Lock su file/aree fra agenti AI CLI indipendenti.

Il modello d'esecuzione è: comandi one-shot che acquisiscono e muoiono subito.
Da qui la separazione che regge tutto il modulo:

- **Lo stato** (chi possiede il path e da quando) sta nel *contenuto* del lock
  file, che sopravvive alla morte del processo. È l'unica cosa che decide se
  un path è occupato.
- **La mutua esclusione durante l'aggiornamento** di quello stato è data da
  `flock`, tenuto per la sola sezione critica read-modify-write, interamente
  dentro un processo vivo.

Chiedere a `flock` di essere *lo stato* — come faceva la 0.1.0 — significa che
il kernel lo rilascia all'uscita del processo e il comando successivo trova
campo libero: due agenti owner dello stesso file.

Vincolo che rende corretto il tutto: **il lock file non viene mai cancellato
né sostituito**. Rilasciare azzera il contenuto. Se il file venisse unlinkato
o rimpiazzato via rename, due processi terrebbero fd su inode diversi e flock
non escluderebbe più nulla.

I lock sono **advisory**: proteggono gli agenti che li consultano, non i
write() di chi li ignora.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_TIMEOUT = 120  # secondi
DEFAULT_LOCK_DIR = Path.home() / "Desktop" / "agent-registry" / "locks"


def get_lock_dir() -> Path:
    """Directory dei lock, sovrascrivibile via AGENT_REGISTRY_LOCK_DIR.

    Risolta a ogni chiamata e non all'import: risolverla una volta sola
    renderebbe il modulo configurabile solo via monkeypatch, cioè solo
    in-process — che è ciò che ha costretto la 0.1.0 a test in-process,
    ciechi per costruzione ai difetti fra processi.
    """
    env = os.environ.get("AGENT_REGISTRY_LOCK_DIR")
    return Path(env) if env else DEFAULT_LOCK_DIR


def _lock_file(path: str) -> Path:
    """Lock file associato a un path.

    L'identità è il path *reale* risolto: così un path relativo, uno assoluto
    e un symlink allo stesso file contendono lo stesso lock, mentre file
    omonimi in progetti diversi restano distinti.
    """
    real = os.path.realpath(path)
    h = hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]
    return get_lock_dir() / f"{h}.lock"


def _ensure_lock_dir() -> None:
    get_lock_dir().mkdir(parents=True, exist_ok=True)


def _serialize(session_id: str, real_path: str) -> str:
    # Il path reale sta nel file per rendere `locks/` ispezionabile a mano:
    # altrimenti servirebbe invertire un hash per sapere cosa è bloccato.
    return f"{session_id}|{time.time()}|{real_path}"


def _deserialize(content: str) -> dict:
    content = content.strip()
    if not content:
        return {}  # contenuto vuoto = lock rilasciato
    parts = content.split("|", 2)
    if len(parts) < 2:
        return {}
    try:
        ts = float(parts[1])
    except ValueError:
        return {}
    info = {"session_id": parts[0], "timestamp": ts}
    if len(parts) > 2:
        info["path"] = parts[2]
    return info


@contextmanager
def _critical(path: str, shared: bool = False):
    """Apre il lock file e ne serializza l'accesso con flock.

    Il file viene creato se assente e **mai** cancellato: è ciò che garantisce
    che tutti i processi flocchino lo stesso inode. `shared=True` per i
    lettori, così non osservano mai una scrittura a metà.
    """
    _ensure_lock_dir()
    lock_file = _lock_file(path)
    fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read_fd(fd: int) -> dict:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
    return _deserialize(b"".join(chunks).decode("utf-8", errors="replace"))


def _write_fd(fd: int, session_id: str, real_path: str) -> None:
    # Scrittura in-place: sostituire il file via rename cambierebbe l'inode
    # e renderebbe inutile il flock di chi lo tiene aperto.
    payload = _serialize(session_id, real_path).encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload)
    os.fsync(fd)


def _clear_fd(fd: int) -> None:
    os.ftruncate(fd, 0)
    os.fsync(fd)


def _is_stale(info: dict, timeout: float) -> bool:
    if not info:
        return False
    return (time.time() - info.get("timestamp", 0)) > timeout


def is_locked(path: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Stato del lock su path. Lettore puro: non modifica nulla.

    La 0.1.0 qui cancellava i lock stale, il che permetteva a due agenti che
    li osservavano insieme di acquisirli entrambi. Osservare non modifica.
    """
    if not _lock_file(path).exists():
        return {"locked": False}
    with _critical(path, shared=True) as fd:
        info = _read_fd(fd)
    if not info:
        return {"locked": False}
    if _is_stale(info, timeout):
        return {"locked": False, "stale_owner": info.get("session_id")}
    return {
        "locked": True,
        "session_id": info.get("session_id"),
        "age": time.time() - info.get("timestamp", 0),
    }


def acquire_lock(path: str, session_id: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Acquisisce il lock su path per session_id.

    Ritorna {'locked': True, 'owner': ...} se acquisito, altrimenti
    {'locked': False, 'session_id': <owner corrente>, 'age': ...}.
    """
    real = os.path.realpath(path)
    with _critical(path) as fd:
        info = _read_fd(fd)

        if not info:  # libero o rilasciato
            _write_fd(fd, session_id, real)
            return {"locked": True, "owner": session_id}

        if info.get("session_id") == session_id:  # riacquisizione idempotente
            _write_fd(fd, session_id, real)
            return {"locked": True, "owner": session_id, "note": "already locked by you"}

        if _is_stale(info, timeout):
            # Takeover dentro la sezione critica: due taker sono serializzati
            # dal flock e il secondo rilegge trovando il primo come owner fresco.
            stale_owner = info.get("session_id")
            _write_fd(fd, session_id, real)
            return {"locked": True, "owner": session_id, "stale_owner": stale_owner}

        return {
            "locked": False,
            "session_id": info.get("session_id"),
            "age": time.time() - info.get("timestamp", 0),
        }


def release_lock(path: str, session_id: str) -> dict:
    """Rilascia il lock se session_id ne è l'owner."""
    if not _lock_file(path).exists():
        return {"released": True, "note": "lock file not found"}

    with _critical(path) as fd:
        info = _read_fd(fd)
        if not info:
            return {"released": True, "note": "already released"}
        if info.get("session_id") != session_id:
            return {
                "released": False,
                "error": "not owner",
                "current_owner": info.get("session_id"),
            }
        # Azzerare invece di cancellare: l'unlink romperebbe il flock dei
        # processi che tengono aperto lo stesso path.
        _clear_fd(fd)
        return {"released": True}


def heartbeat(path: str, session_id: str) -> dict:
    """Rinnova la scadenza del lock. Solo l'owner può farlo."""
    if not _lock_file(path).exists():
        return {"ok": False, "error": "lock not found"}

    real = os.path.realpath(path)
    with _critical(path) as fd:
        info = _read_fd(fd)
        if not info:
            return {"ok": False, "error": "lock not found"}
        if info.get("session_id") != session_id:
            return {
                "ok": False,
                "error": "not owner",
                "current_owner": info.get("session_id"),
            }
        _write_fd(fd, session_id, real)
        return {"ok": True}


def heartbeat_loop(path: str, session_id: str, interval: float = 30) -> None:
    """Rinnova il lock a intervalli finché non fallisce."""
    while True:
        result = heartbeat(path, session_id)
        if not result.get("ok"):
            print(f"Heartbeat failed for {path}: {result}", file=sys.stderr)
            break
        time.sleep(interval)


def check_and_warn(path: str, session_id: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Avvisa se path è occupato da altri, senza acquisire nulla."""
    status = is_locked(path, timeout)
    if status.get("locked"):
        owner = status.get("session_id")
        if owner == session_id:
            return {"ok": True, "note": "already locked by you"}
        return {
            "ok": False,
            "warning": (
                f"⚠️  Il file/area '{path}' è locked da {owner} "
                f"(age: {status.get('age', 0):.0f}s)."
            ),
            "owner": owner,
        }
    return {"ok": True, "note": "not locked"}


def guarded_acquire(path: str, session_id: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Acquisisce il lock, riportando il blocco in forma esplicita."""
    result = acquire_lock(path, session_id, timeout)
    if result.get("locked"):
        return {"ok": True, **result}
    return {
        "ok": False,
        "blocked": True,
        "owner": result.get("session_id"),
        "message": (
            f"🚫 Bloccato: '{path}' è in uso da {result.get('session_id')}. "
            "Non modificare."
        ),
    }


USAGE = """Uso: lock_manager.py acquire <path> <session_id>
     lock_manager.py release <path> <session_id>
     lock_manager.py check <path> [session_id]
     lock_manager.py heartbeat <path> <session_id>
     lock_manager.py heartbeat-loop <path> <session_id> [interval_sec]

Exit code: 0 se l'operazione riesce, 1 se fallisce o e' bloccata."""


def main(argv: list[str]) -> int:
    """CLI. L'exit code è l'unico esito su cui un agente in bash può contare."""
    cmd = argv[1] if len(argv) > 1 else "help"
    try:
        if cmd == "acquire":
            result = acquire_lock(argv[2], argv[3])
            print(result)
            return 0 if result.get("locked") else 1
        if cmd == "release":
            result = release_lock(argv[2], argv[3])
            print(result)
            return 0 if result.get("released") else 1
        if cmd == "check":
            sid = argv[3] if len(argv) > 3 else ""
            result = check_and_warn(argv[2], sid)
            print(result)
            return 0 if result.get("ok") else 1
        if cmd == "heartbeat":
            result = heartbeat(argv[2], argv[3])
            print(result)
            return 0 if result.get("ok") else 1
        if cmd == "heartbeat-loop":
            interval = float(argv[4]) if len(argv) > 4 else 30
            heartbeat_loop(argv[2], argv[3], interval)
            return 0
    except IndexError:
        # Argomenti mancanti: l'uso corretto è più utile di un traceback.
        print(f"Errore: argomenti mancanti per '{cmd}'.\n\n{USAGE}", file=sys.stderr)
        return 1

    print(USAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
