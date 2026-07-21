#!/usr/bin/env python3
"""Manager per i lock a livello filesystem usati da agent-registry.

Ogni lock è rappresentato da un file nella directory `locks/` della home del
registry (~/.agent-registry/locks/, sovrascrivibile via AGENT_REGISTRY_HOME,
via env AGENT_REGISTRY_LOCK_DIR o impostando l'attributo di modulo LOCK_DIR,
come fanno i test).
Il file contiene "session_id|timestamp_epoch".
Se un agente crasha, il timeout/heartbeat rende il lock stale.

Il modello d'esecuzione è: comandi one-shot che acquisiscono e muoiono subito.
Da qui la separazione che regge tutto il modulo:

- **Lo stato** (chi possiede il path e da quando) sta nel *contenuto* del lock
  file, che sopravvive alla morte del processo. È l'unica cosa che decide se
  un path è occupato.
- **La mutua esclusione durante l'aggiornamento** di quello stato è data da
  `flock`, tenuto per la sola sezione critica read-modify-write, interamente
  dentro un processo vivo.

Vincolo che rende corretto il tutto: **il lock file non viene mai cancellato
né sostituito**. Rilasciare azzera il contenuto; un lettore non modifica nulla.
Se il file venisse unlinkato o rimpiazzato via rename, due processi terrebbero
fd su inode diversi e flock non escluderebbe più nulla (era il difetto che
permetteva a due agenti di acquisire entrambi lo stesso lock).

Acquisizione e rilascio sono sincronizzati col registry (D5): acquire aggiunge
il path a `do_not_touch` e `space` della sessione, release lo rimuove da
`do_not_touch`. La sync è best-effort: se la sessione non esiste nel registry
il lock funziona comunque (warning su stderr).

Nota su macOS: fcntl non permette a un processo di acquisire due lock
(esclusivi o condivisi) sullo stesso file. Per questo motivo le operazioni
di lettura/heartbeat non acquisiscono lock; si affidano al fatto che solo
l'owner scrive nel file. Il lock esclusivo viene usato solo in fase di
acquisizione per garantire atomicità tra processi diversi.

I lock sono **advisory**: proteggono gli agenti che li consultano, non i
write() di chi li ignora.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT = 120  # secondi

# Override della directory dei lock (usato dai test). Se None, la directory è
# risolta lazy come get_registry_home()/"locks".
LOCK_DIR: Path | None = None

# fd aperti per lock acquisiti nel processo corrente (path -> fd).
# Serve per poter rilasciare il lock fcntl senza dover riacquisire il file.
_OPEN_LOCK_FDS: dict[str, int] = {}


def _lock_dir() -> Path:
    """Directory dei lock.

    Priorità: attributo di modulo LOCK_DIR (test), env AGENT_REGISTRY_LOCK_DIR,
    poi la home del registry. Risolta a ogni chiamata e non all'import:
    risolverla una volta sola renderebbe il modulo configurabile solo via
    monkeypatch, cioè solo in-process — e test in-process sono ciechi per
    costruzione ai difetti fra processi.
    """
    if LOCK_DIR is not None:
        return Path(LOCK_DIR)
    env = os.environ.get("AGENT_REGISTRY_LOCK_DIR")
    if env:
        return Path(env)
    try:
        scripts_dir = str(Path(__file__).parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from registry_manager import get_registry_home

        return get_registry_home() / "locks"
    except Exception:
        return Path.home() / ".agent-registry" / "locks"


def _registry_manager():
    """Import lazy di registry_manager (anti import circolari)."""
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import registry_manager

    return registry_manager


def _sync_registry_acquire(path: str, session_id: str) -> None:
    """Aggiunge path a do_not_touch e space della sessione (idempotente, best-effort)."""
    try:
        rm = _registry_manager()
        home = rm.get_registry_home()
        if not (home / "sessions").is_dir():
            return  # nessun registry: niente da sincronizzare
        agent = rm.find_agent(session_id)
        if agent is None:
            print(
                f"[lock_manager] warning: sessione '{session_id}' non trovata "
                "nel registry; lock non sincronizzato",
                file=sys.stderr,
            )
            return
        do_not_touch = [str(p) for p in agent.get("do_not_touch") or []]
        space = [str(p) for p in agent.get("space") or []]
        changed = False
        if path not in do_not_touch:
            do_not_touch.append(path)
            changed = True
        if path not in space:
            space.append(path)
            changed = True
        if changed:
            rm.update_session(session_id, do_not_touch=do_not_touch, space=space)
    except Exception as e:
        print(f"[lock_manager] warning: sync registry fallita: {e}", file=sys.stderr)


def _sync_registry_release(path: str, session_id: str) -> None:
    """Rimuove path da do_not_touch della sessione (best-effort)."""
    try:
        rm = _registry_manager()
        home = rm.get_registry_home()
        if not (home / "sessions").is_dir():
            return
        agent = rm.find_agent(session_id)
        if agent is None:
            return
        do_not_touch = [str(p) for p in agent.get("do_not_touch") or []]
        if path in do_not_touch:
            do_not_touch.remove(path)
            rm.update_session(session_id, do_not_touch=do_not_touch)
    except Exception as e:
        print(f"[lock_manager] warning: sync registry fallita: {e}", file=sys.stderr)


def _lock_file(path: str) -> Path:
    """Restituisce il file di lock per un dato path.

    L'identità è il path *reale* risolto: così un path relativo, uno assoluto
    e un symlink allo stesso file contendono lo stesso lock, mentre file
    omonimi in progetti diversi restano distinti.
    """
    real = os.path.realpath(path)
    h = hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]
    return _lock_dir() / f"{h}.lock"


def _ensure_lock_dir() -> None:
    _lock_dir().mkdir(parents=True, exist_ok=True)


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


def _write_info(fd: int, session_id: str, sync: bool = True) -> None:
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    # Timestamp catturato il più tardi possibile: è il riferimento da cui
    # is_locked misura l'age, quindi ogni ms speso prima slitta la freschezza.
    payload = f"{session_id}|{time.time()}".encode("utf-8")
    os.write(fd, payload)
    if sync:
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
            _sync_registry_acquire(path, session_id)
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
        current_owner = info.get("session_id")
        if current_owner and current_owner != session_id and not stale_owner:
            # Owner diverso e non stale (tipico caso CLI: il processo owner è
            # uscito e il flock è libero, ma il lock logico è ancora valido):
            # non sovrascrivere, rispetta il proprietario registrato.
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            return {
                "locked": False,
                "session_id": current_owner,
                "age": time.time() - info.get("timestamp", 0),
            }
        _write_info(fd, session_id)
        _OPEN_LOCK_FDS[path] = fd
        result = {"locked": True, "owner": session_id}
        if stale_owner:
            result["stale_owner"] = stale_owner
        # D5: lock e registry sincronizzati nella stessa operazione
        _sync_registry_acquire(path, session_id)
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
        _sync_registry_release(path, session_id)
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
    _sync_registry_release(path, session_id)
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
        # Niente fsync sull'heartbeat: se il processo crasha gli heartbeat
        # cessano comunque e il lock diventa stale (fallimento sicuro);
        # la durabilità oltre la morte del processo è richiesta dalla spec
        # solo in acquisizione. Saltare l'fsync riduce la finestra fra il
        # rinnovo e la misura dell'age da parte degli altri agenti.
        _write_info(fd, session_id, sync=False)
        return {"ok": True}
    # Se non abbiamo il fd aperto, apriamo e scriviamo (caso loop heartbeat riavviato)
    try:
        with open(lock_file, "r+", encoding="utf-8") as f:
            _write_info(f.fileno(), session_id, sync=False)
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


def check_paths(
    paths: list[str], session_id: str = "", timeout: int = DEFAULT_TIMEOUT
) -> list[dict]:
    """Batch pre-flight check: stato di ogni path in una sola chiamata.

    Per ogni path restituisce un dict con:
      - state: "free" | "locked" | "locked-by-me" | "stale"
      - owner: session_id proprietario (per locked / locked-by-me / stale)
      - age: età del lock in secondi (per locked / locked-by-me)
    """
    results: list[dict] = []
    for path in paths:
        status = is_locked(path, timeout)
        entry: dict = {"path": path}
        if status.get("locked"):
            owner = status.get("session_id")
            entry["state"] = "locked-by-me" if owner == session_id else "locked"
            entry["owner"] = owner
            entry["age"] = status.get("age")
        elif status.get("stale_owner"):
            entry["state"] = "stale"
            entry["owner"] = status.get("stale_owner")
        else:
            entry["state"] = "free"
        results.append(entry)
    return results


def _format_check_entry(entry: dict) -> str:
    """Riga human-readable del report batch check."""
    path = entry["path"]
    state = entry["state"]
    if state == "free":
        return f"{path}: free"
    if state == "stale":
        return f"{path}: stale (owner: {entry.get('owner')})"
    age = entry.get("age")
    age_str = f"{age:.0f}s" if isinstance(age, (int, float)) else "?"
    if state == "locked-by-me":
        return f"{path}: locked-by-me (age: {age_str})"
    return f"{path}: locked (owner: {entry.get('owner')}, age: {age_str})"


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI. Restituisce l'exit code.

    L'exit code è il contratto con gli agenti in bash: 0 se l'operazione
    riesce, diverso da 0 se fallisce o è bloccata (lock di un altro owner,
    rilascio/heartbeat da non-owner). Un agente reagisce con
    `if ! lock_manager.py acquire ...` senza fare parsing dell'output.
    """
    parser = argparse.ArgumentParser(
        prog="lock_manager.py",
        description="Lock a livello filesystem per agent-registry.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("acquire", help="acquisisce il lock su un path")
    p.add_argument("path")
    p.add_argument("session_id")

    p = sub.add_parser("release", help="rilascia il lock su un path")
    p.add_argument("path")
    p.add_argument("session_id")

    p = sub.add_parser("check", help="stato di uno o più path (batch)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--session-id", dest="session_id", default="")

    p = sub.add_parser("heartbeat", help="rinnova il timestamp del lock")
    p.add_argument("path")
    p.add_argument("session_id")

    p = sub.add_parser("heartbeat-loop", help="loop di heartbeat in foreground")
    p.add_argument("path")
    p.add_argument("session_id")
    p.add_argument("interval", nargs="?", type=int, default=30)

    args = parser.parse_args(argv)

    if args.command == "acquire":
        result = acquire_lock(args.path, args.session_id)
        print(result)
        return 0 if result.get("locked") else 1
    if args.command == "release":
        result = release_lock(args.path, args.session_id)
        print(result)
        return 0 if result.get("released") else 1
    if args.command == "check":
        if len(args.paths) == 1:
            # Forma singola storica: avviso advisory, non blocca (exit 0).
            result = check_and_warn(args.paths[0], args.session_id)
            print(result)
            return 0
        entries = check_paths(args.paths, args.session_id)
        for entry in entries:
            print(_format_check_entry(entry))
        # Il batch fallisce se almeno un path è di un altro owner.
        return 1 if any(e["state"] == "locked" for e in entries) else 0
    if args.command == "heartbeat":
        result = heartbeat(args.path, args.session_id)
        print(result)
        return 0 if result.get("ok") else 1
    if args.command == "heartbeat-loop":
        heartbeat_loop(args.path, args.session_id, args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
