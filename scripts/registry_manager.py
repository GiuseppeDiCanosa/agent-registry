#!/usr/bin/env python3
"""Manager per il registry condiviso agent-registry.

Home globale di default: ~/.agent-registry/ (sovrascrivibile via env
AGENT_REGISTRY_HOME). Struttura:

    ~/.agent-registry/
    ├── registry.md            # vista renderizzata (frontmatter + tabella)
    ├── sessions/<id>.yaml     # fonte di verità, un file per sessione
    ├── contexts/
    ├── locks/
    └── wiki/

Ogni sessione vive in `sessions/<session_id>.yaml`; `registry.md` è una vista
(YAML frontmatter con agents + tabella markdown) rigenerata interamente dai
file sessione a ogni scrittura.

Concorrenza: il ciclo read-modify-write (lettura sessioni, scrittura file
sessione, rigenerazione della vista) avviene dentro un flock tenuto su
`registry.lock`, un file **dedicato e mai rinominato** accanto alla vista.
Lockare il file che poi si sostituisce via rename non esclude nessuno: dopo
il rename il path punta a un inode nuovo e il writer successivo locka un
oggetto diverso. Lock su un file, scrittura atomica su un altro.

Alias deprecato: AGENT_REGISTRY_PATH (punta al file registry.md, come nella
versione precedente); la home viene derivata dalla sua directory genitore.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import warnings
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REGISTRY_HOME = Path.home() / ".agent-registry"
LEGACY_REGISTRY_PATH = Path.home() / "Desktop" / "agent-registry" / "registry.md"
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "registry-template.md"
SUBDIRS = ("sessions", "contexts", "locks", "wiki")

DEPRECATION_HEADER = (
    "> **DEPRECATO** — migrato a `~/.agent-registry` — "
    "questo file non viene più aggiornato.\n\n"
)

# Istruzioni per qualunque agente apra il registry. Gli agenti dei vari
# provider non condividono alcun sistema di skill: SKILL.md istruisce solo chi
# la carica. Il registry invece lo aprono tutti, quindi le regole viaggiano
# con lo stato che descrivono. Rigenerato a ogni scrittura: un blocco che si
# può perdere con un update è un blocco su cui non si può contare.
PROTOCOL_BLOCK = """<!-- PROTOCOL:START — rigenerato a ogni scrittura, non modificare a mano -->
# AGENT REGISTRY — PROTOCOLLO OBBLIGATORIO

> **Se stai leggendo questo file come agente AI, queste regole valgono per te.**
>
> 1. **Leggi prima di toccare.** Non modificare alcun file elencato nella
>    colonna `Do Not Touch` di un agente con status `OnWorking`: c'è un altro
>    agente che ci sta lavorando in questo momento.
> 2. **Registrati prima di lavorare:**
>    `python <skill>/scripts/registry_manager.py register <session_id> <provider> <versione> "<cosa stai facendo>" --space "<file,toccati>" --todo-present "<todo,correnti>"`
> 3. **Acquisisci il lock prima di ogni modifica:**
>    `python <skill>/scripts/lock_manager.py acquire <path> <session_id>`
>    Exit code 0 = il lock è tuo; diverso da 0 = è di un altro, **fermati**.
> 4. **Tieni vivo il lock** se il lavoro supera i 120s: `heartbeat-loop`.
> 5. **A fine sessione:** `registry_manager.py end <session_id>` (distilla la
>    sessione in un wiki entry) oppure `finish <session_id>`: entrambi
>    rilasciano anche i lock della sessione.
>
> **I lock sono advisory.** Nessuno impedisce fisicamente la scrittura su un
> file bloccato: la protezione funziona solo se ogni agente rispetta questo
> protocollo. Se lo ignori, il coordinamento salta per tutti.
<!-- PROTOCOL:END -->"""


def get_protocol_block() -> str:
    """Blocco di protocollo canonico.

    Esposto come funzione perché è la sola definizione autorevole: chi ne
    vuole una copia (il template su disco, un test) deve leggerla da qui,
    non riscriverla.
    """
    return PROTOCOL_BLOCK


def get_registry_home() -> Path:
    """Restituisce la home del registry.

    Priorità: env AGENT_REGISTRY_HOME, poi alias deprecato AGENT_REGISTRY_PATH
    (che punta al file registry.md: la home è la sua directory genitore),
    infine il default ~/.agent-registry/.
    """
    env_home = os.environ.get("AGENT_REGISTRY_HOME")
    if env_home:
        return Path(env_home)
    env_path = os.environ.get("AGENT_REGISTRY_PATH")
    if env_path:
        warnings.warn(
            "AGENT_REGISTRY_PATH è deprecato: usa AGENT_REGISTRY_HOME",
            DeprecationWarning,
            stacklevel=2,
        )
        return Path(env_path).parent
    return DEFAULT_REGISTRY_HOME


def get_registry_path() -> Path:
    """Restituisce il path del file registry.md (vista) nella home corrente."""
    return get_registry_home() / "registry.md"


def _legacy_registry_path() -> Path:
    """Path del registry legacy su Desktop (valutato a runtime per i test)."""
    return LEGACY_REGISTRY_PATH


def _ensure_structure(home: Path) -> None:
    """Crea la struttura di directory della home se mancante."""
    home.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (home / sub).mkdir(exist_ok=True)


def migrate_from_legacy(
    home: Path | None = None, legacy_path: Path | None = None
) -> bool:
    """Migra il registry legacy (~/Desktop/agent-registry/) nel nuovo formato.

    Esegue la migrazione solo se il file legacy esiste e la home nuova non
    esiste ancora. Scrive i file sessione, rigenera la vista, stampa un
    avviso e marca il vecchio file con un header DEPRECATO.

    Restituisce True se la migrazione è stata eseguita.
    """
    home = home or get_registry_home()
    legacy = legacy_path or _legacy_registry_path()
    if home.exists() or not legacy.exists():
        return False

    _ensure_structure(home)
    try:
        frontmatter, _ = parse_registry(legacy)
    except Exception:
        # Legacy non parsabile: niente migrazione, ma non bloccare le operazioni.
        return False
    agents = frontmatter.get("agents") or []
    for agent in agents:
        if isinstance(agent, dict) and agent.get("session_id"):
            _write_session(agent, home)
    _render_view(home)

    old_content = legacy.read_text(encoding="utf-8")
    if not old_content.startswith(DEPRECATION_HEADER):
        legacy.write_text(DEPRECATION_HEADER + old_content, encoding="utf-8")

    print(
        f"[agent-registry] Registry migrato da {legacy} a {home} "
        f"({len(agents)} sessioni). Il vecchio file è deprecato."
    )
    return True


def _prepare_home(registry_path: Path | None = None) -> Path:
    """Risolve la home, esegue la migrazione se serve e crea la struttura."""
    if registry_path is not None:
        # Compatibilità: il chiamante passa il path del file registry.md.
        home = Path(registry_path).parent
        _ensure_structure(home)
    else:
        home = get_registry_home()
        migrate_from_legacy(home)
        _ensure_structure(home)
    view = home / "registry.md"
    if not view.exists():
        view.write_text(_empty_registry(), encoding="utf-8")
    return home


def ensure_registry(registry_path: Path | None = None) -> Path:
    """Garantisce home, struttura e file registry.md; restituisce il path della vista."""
    home = _prepare_home(registry_path)
    return home / "registry.md"


def _registry_lock_path(home: Path) -> Path:
    """File di lock dedicato, accanto alla vista.

    Non è mai rinominato né cancellato: è ciò che permette a processi diversi
    di flockare lo stesso inode mentre `registry.md` e i file sessione vengono
    sostituiti via rename.
    """
    return home / "registry.lock"


@contextmanager
def _registry_critical(home: Path):
    """Serializza l'intero read-modify-write del registry fra processi.

    Non tenere mai questo lock mentre si acquisiscono/rilasciano lock file di
    lock_manager (che hanno il proprio flock): l'ordine di acquisizione
    incrociato è un deadlock.
    """
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_registry_lock_path(home)), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _empty_registry() -> str:
    return f"""---
version: "1.0"
last_updated: ""
agents: []
---

{PROTOCOL_BLOCK}

| Session ID | Provider | AI Version | Started At (Rome) | Working On | To Do Past | To Do Present | To Do Future | Space | Do Not Touch | Status | Issues | Handoff |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
"""


def parse_registry(registry_path: Path | None = None) -> tuple[dict[str, Any], str]:
    """Parsa il registry (vista) restituendo (frontmatter, body markdown)."""
    if registry_path is None:
        path = ensure_registry()
    else:
        path = Path(registry_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            if TEMPLATE_PATH.exists():
                shutil.copy(TEMPLATE_PATH, path)
            else:
                path.write_text(_empty_registry(), encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"Formato registry non valido: {path}")
    frontmatter = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)
    return frontmatter, body


def _session_filename(session_id: str) -> str:
    """Nome file sicuro per una sessione."""
    safe = re.sub(r"[^\w.-]", "_", session_id)
    return f"{safe}.yaml"


def _session_path(home: Path, session_id: str) -> Path:
    return home / "sessions" / _session_filename(session_id)


def _load_agents_from(home: Path) -> list[dict[str, Any]]:
    """Legge tutte le sessioni dai file YAML (fonte di verità)."""
    sessions_dir = home / "sessions"
    agents: list[dict[str, Any]] = []
    if sessions_dir.is_dir():
        for f in sessions_dir.glob("*.yaml"):
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                agents.append(data)
    agents.sort(key=lambda a: (str(a.get("started_at", "")), str(a.get("session_id", ""))))
    return agents


def load_agents(registry_path: Path | None = None) -> list[dict[str, Any]]:
    """Carica la lista agenti dai file sessione."""
    home = _prepare_home(registry_path)
    return _load_agents_from(home)


def _read_session(home: Path, session_id: str) -> dict[str, Any] | None:
    """Legge una singola sessione dal suo file YAML."""
    path = _session_path(home, session_id)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _atomic_write(path: Path, content: str) -> None:
    """Scrittura atomica via file temporaneo + replace.

    La mutua esclusione fra processi NON sta qui: flockare il file che poi si
    sostituisce via rename è inutile, perché dopo il rename il path punta a un
    inode nuovo. La serializzazione sta in `_registry_critical`, che flocka un
    file dedicato e mai sostituito. Qui conta solo che i lettori non vedano
    mai un file troncato a metà.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}_", suffix=".tmp", text=True
    )
    try:
        os.write(tmp_fd, content.encode("utf-8"))
        os.fsync(tmp_fd)
    finally:
        os.close(tmp_fd)
    shutil.move(tmp_name, path)


def _write_session(agent: dict[str, Any], home: Path) -> Path:
    """Scrive atomicamente il file YAML di una sessione."""
    path = _session_path(home, agent["session_id"])
    content = yaml.safe_dump(
        agent, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    _atomic_write(path, content)
    return path


def _now_rome() -> str:
    """Timestamp corrente in timezone Roma."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        # Fallback: usa l'orario locale
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _iso_now() -> str:
    """Timestamp ISO 8601 con timezone per last_updated."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Rome")).isoformat()
    except Exception:
        return datetime.now().astimezone().isoformat()


def _git_branch() -> str:
    """Branch git corrente della cwd; stringa vuota se non è un repo o in caso di errore."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _render_table(agents: list[dict[str, Any]]) -> str:
    """Genera la tabella markdown dagli agenti."""
    header = (
        "| Session ID | Provider | AI Version | Started At (Rome) | "
        "Working On | To Do Past | To Do Present | To Do Future | "
        "Space | Do Not Touch | Status | Issues | Handoff |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for a in agents:
        todo = a.get("todo") or {}
        lines.append(
            f"| {_fmt(a.get('session_id'))} | {_fmt(a.get('provider'))} | "
            f"{_fmt(a.get('ai_version'))} | {_fmt(a.get('started_at'))} | "
            f"{_fmt(a.get('working_on'))} | {_fmt_list(todo.get('past', []))} | "
            f"{_fmt_list(todo.get('present', []))} | {_fmt_list(todo.get('future', []))} | "
            f"{_fmt_list(a.get('space', []))} | {_fmt_list(a.get('do_not_touch', []))} | "
            f"{_fmt(a.get('status'))} | {_fmt(a.get('issues'))} | {_fmt(a.get('handoff'))} |"
        )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _fmt_list(items: list[Any] | None) -> str:
    """Come _fmt, ma per le liste.

    L'escape va fatto anche dentro le liste: un '|' in `space` aprirebbe una
    colonna fantasma e disallineerebbe la tabella.
    """
    if not items:
        return ""
    return ", ".join(_fmt(x) for x in items)


def _dump_registry(agents: list[dict[str, Any]]) -> str:
    """Serializza il registry completo (vista)."""
    frontmatter = {
        "version": "1.0",
        "last_updated": _iso_now(),
        "agents": agents,
    }
    table = _render_table(agents)
    yaml_part = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{yaml_part}---\n\n{PROTOCOL_BLOCK}\n\n{table}\n"


def _render_view(home: Path) -> Path:
    """Rigenera atomicamente registry.md dai file sessione."""
    agents = _load_agents_from(home)
    view = home / "registry.md"
    _atomic_write(view, _dump_registry(agents))
    return view


def _schedule_git_sync(home: Path, message: str) -> None:
    """Schedula un git-sync in background se la home è un repo con remote.

    Best-effort totale: import lazy di sync_manager (anti import circolari)
    e try/except onnicomprensivo — un errore di sync non deve mai propagarsi
    alle operazioni del registry.
    """
    try:
        scripts_dir = str(Path(__file__).parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import sync_manager

        if sync_manager.is_git_enabled(home):
            sync_manager.schedule_sync(home, message)
    except Exception:
        pass


def save_agents(
    agents: list[dict[str, Any]], registry_path: Path | None = None
) -> Path:
    """Persiste la lista agenti come file sessione e rigenera la vista.

    Le sessioni presenti su disco ma assenti dalla lista vengono rimosse
    (semantica di sostituzione completa, come la versione precedente).
    """
    home = _prepare_home(registry_path)
    with _registry_critical(home):
        keep: set[str] = set()
        for agent in agents:
            session_id = agent.get("session_id")
            if not session_id:
                continue
            keep.add(_session_filename(str(session_id)))
            _write_session(agent, home)
        sessions_dir = home / "sessions"
        for f in sessions_dir.glob("*.yaml"):
            if f.name not in keep:
                f.unlink()
        view = _render_view(home)
    _schedule_git_sync(home, "save_agents")
    return view


def register_session(
    session_id: str,
    provider: str,
    ai_version: str,
    working_on: str,
    space: list[str] | None = None,
    todo_present: list[str] | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Registra una nuova sessione agente nel registry.

    Cattura automaticamente pid, cmdline, progetto (directory corrente) e
    branch git (vuoto se non è un repo, senza fallire).
    """
    home = _prepare_home(registry_path)
    agent = {
        "session_id": session_id,
        "provider": provider,
        "ai_version": ai_version,
        "started_at": _now_rome(),
        "working_on": working_on,
        "todo": {
            "past": [],
            "present": todo_present or [],
            "future": [],
        },
        "space": space or [],
        "do_not_touch": [],
        "status": "OnWorking",
        "issues": "",
        "handoff": "",
        "pid": os.getpid(),
        "cmdline": " ".join(sys.argv),
        "project": Path.cwd().name,
        "git_branch": _git_branch(),
    }
    # Scrittura del file sessione e rigenerazione della vista nella stessa
    # sezione critica: senza, due processi possono rendere la vista da
    # snapshot diversi e l'ultimo che scrive nasconde l'altro.
    with _registry_critical(home):
        _write_session(agent, home)
        _render_view(home)
    _schedule_git_sync(home, f"register {session_id}")
    return agent


def update_session(
    session_id: str,
    registry_path: Path | None = None,
    **kwargs: Any,
) -> dict[str, Any] | None:
    """Aggiorna i campi di una sessione esistente."""
    home = _prepare_home(registry_path)
    # Lettura e scrittura nella stessa sezione critica: leggere fuori dal lock
    # è ciò che fa perdere gli aggiornamenti concorrenti sulla stessa sessione.
    with _registry_critical(home):
        agent = _read_session(home, session_id)
        if agent is None:
            return None
        for key, value in kwargs.items():
            if key == "todo" and isinstance(value, dict):
                agent.setdefault("todo", {}).update(value)
            else:
                agent[key] = value
        _write_session(agent, home)
        _render_view(home)
    _schedule_git_sync(home, f"update {session_id}")
    return agent


def unregister_session(
    session_id: str, registry_path: Path | None = None
) -> dict[str, Any] | None:
    """Marca una sessione come Finished, svuota do_not_touch e rilascia i lock.

    Rilasciare i lock qui elimina la divergenza fra registry e `locks/`: senza,
    i lock resterebbero appesi fino al timeout chiedendo all'agente di
    rilasciarli a mano.
    """
    home = _prepare_home(registry_path)
    agent = _read_session(home, session_id)
    if agent is None:
        return None
    held = [str(p) for p in agent.get("do_not_touch") or []]
    finished = update_session(
        session_id, registry_path=registry_path, status="Finished", do_not_touch=[]
    )
    # Fuori dalla sezione critica del registry: i lock hanno il proprio flock
    # e release_lock rifiuta i path di cui la sessione non è owner.
    if held:
        _release_session_locks(session_id, held)
    return finished


def find_agent(
    session_id: str, registry_path: Path | None = None
) -> dict[str, Any] | None:
    """Trova un agente per session_id."""
    home = _prepare_home(registry_path)
    return _read_session(home, session_id)


def add_handoff_ref(
    session_id: str, handoff_path: str, registry_path: Path | None = None
) -> dict[str, Any] | None:
    """Aggiunge il riferimento a un handoff salvato."""
    return update_session(
        session_id, registry_path=registry_path, handoff=handoff_path
    )


# --- Gruppo 3: processi, cleanup, kill, status, CLI argparse ---


def _pid_alive(pid: Any) -> bool:
    """True se il pid corrisponde a un processo attivo (non zombie) su questa macchina."""
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Esiste ma non è nostro: comunque attivo.
        return True
    except OSError:
        return False
    # Uno zombie (terminato ma non ancora reapato) risponde ancora a kill(pid, 0):
    # verifica lo stato via ps.
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid_int), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        stat = result.stdout.strip()
        if result.returncode != 0 or not stat or stat.startswith("Z"):
            return False
    except Exception:
        pass
    return True


def _process_cmdline(pid: int) -> str:
    """Cmdline corrente del processo (macOS: ps -p <pid> -o command=)."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _cmdline_compatible(registered: str, current: str, session_id: str) -> bool:
    """Verifica anti-riuso PID (D6): la cmdline attuale deve essere riconducibile
    alla sessione (contiene il session_id, o combacia con quella registrata)."""
    if not current:
        return False
    if session_id and session_id in current:
        return True
    reg = (registered or "").strip()
    if reg and (reg in current or current in reg):
        return True
    reg_tokens = reg.split()
    cur_tokens = current.split()
    if reg_tokens and cur_tokens:
        return os.path.basename(reg_tokens[0]) == os.path.basename(cur_tokens[0])
    return False


def _terminate_pid(pid: Any, grace_seconds: float = 5.0) -> bool:
    """SIGTERM, attesa fino a grace_seconds, poi SIGKILL. True se il processo è morto."""
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid_int, signal.SIGTERM)
    except OSError:
        return False
    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not _pid_alive(pid_int):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid_int, signal.SIGKILL)
    except OSError:
        return not _pid_alive(pid_int)
    for _ in range(20):
        if not _pid_alive(pid_int):
            return True
        time.sleep(0.1)
    return False


def _lock_manager_module() -> Any:
    """Import lazy di lock_manager (stessa directory scripts/, anti import circolari)."""
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import lock_manager

    return lock_manager


def _release_session_locks(session_id: str, paths: list[str]) -> None:
    """Rilascia best-effort tutti i lock di una sessione via lock_manager."""
    try:
        lm = _lock_manager_module()
    except Exception:
        return
    for path in paths:
        try:
            lm.release_lock(path, session_id)
        except Exception:
            pass


def _has_fresh_lock(agent: dict[str, Any]) -> bool:
    """True se la sessione ha almeno un lock non stale (viva qui o su un'altra macchina)."""
    try:
        lm = _lock_manager_module()
    except Exception:
        return False
    for path in agent.get("do_not_touch") or []:
        try:
            if lm.is_locked(str(path)).get("locked"):
                return True
        except Exception:
            continue
    return False


def cleanup_sessions(registry_path: Path | None = None) -> list[str]:
    """Marca Stop le sessioni OnWorking zombie e rilascia i lock residui.

    Una sessione è zombie se il suo PID non è più attivo oppure se tutti i suoi
    lock sono stale/assenti. Non viene mai toccata una sessione con almeno un
    lock non stale: potrebbe essere viva su questa o su un'altra macchina
    (in quel caso il PID non è significativo localmente).
    """
    home = _prepare_home(registry_path)
    cleaned: list[str] = []
    for agent in _load_agents_from(home):
        if agent.get("status") != "OnWorking":
            continue
        session_id = str(agent.get("session_id") or "")
        if not session_id:
            continue
        if _has_fresh_lock(agent):
            continue
        locks = [str(p) for p in agent.get("do_not_touch") or []]
        _release_session_locks(session_id, locks)
        update_session(
            session_id, registry_path=registry_path, status="Stop", do_not_touch=[]
        )
        cleaned.append(session_id)
    return cleaned


def kill_session(
    session_id: str,
    force: bool = False,
    registry_path: Path | None = None,
    grace_seconds: float = 5.0,
) -> dict[str, Any]:
    """Termina una sessione: kill reale via PID se verificabile, altrimenti stop logico.

    Anti-riuso PID (D6): prima di SIGTERM la cmdline attuale del processo deve
    essere compatibile con quella registrata (o contenere il session_id);
    altrimenti il processo non viene toccato e la sessione è marcata Killed
    comunque (stop logico, caso cross-macchina o PID riusato). Con force=True
    la verifica cmdline è saltata. In ogni caso i lock vengono rilasciati.
    """
    home = _prepare_home(registry_path)
    agent = _read_session(home, session_id)
    if agent is None:
        return {"session_id": session_id, "killed": False, "error": "sessione non trovata"}

    pid = agent.get("pid")
    terminated = False
    note = ""
    if not force and _pid_alive(pid):
        current = _process_cmdline(int(pid))
        if _cmdline_compatible(str(agent.get("cmdline") or ""), current, session_id):
            terminated = _terminate_pid(pid, grace_seconds)
            if not terminated:
                note = "processo non terminato (SIGTERM/SIGKILL non riusciti)"
        else:
            note = "processo non terminato (PID non locale o riusato)"
    elif force and _pid_alive(pid):
        terminated = _terminate_pid(pid, grace_seconds)
        if not terminated:
            note = "processo non terminato (SIGTERM/SIGKILL non riusciti)"
    else:
        note = "processo non terminato (PID non locale o riusato)"

    locks = [str(p) for p in agent.get("do_not_touch") or []]
    _release_session_locks(session_id, locks)
    update_session(
        session_id, registry_path=registry_path, status="Killed", do_not_touch=[]
    )
    return {
        "session_id": session_id,
        "killed": True,
        "terminated": terminated,
        "note": note,
        "locks_released": locks,
    }


# --- Gruppo 4: context file, wiki entry, flusso end ---


def _context_filename(session_id: str) -> str:
    """Nome file sicuro del context di una sessione."""
    safe = re.sub(r"[^\w.-]", "_", session_id)
    return f"{safe}-context.md"


def _context_path(home: Path, session_id: str) -> Path:
    return home / "contexts" / _context_filename(session_id)


def _wiki_manager_module() -> Any:
    """Import lazy di wiki_manager (stessa directory scripts/, anti import circolari)."""
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import wiki_manager

    return wiki_manager


def _wiki_ingest_module() -> Any:
    """Import lazy di wiki_ingest (stessa directory scripts/, anti import circolari)."""
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import wiki_ingest

    return wiki_ingest


def log_context(
    session_id: str, entry: str, registry_path: Path | None = None
) -> Path:
    """Appende una riga al context file `contexts/<session_id>-context.md`.

    Append atomico (flock esclusivo + fsync) con timestamp Roma. Se il file
    non esiste viene creato con header e metadati della sessione (provider,
    modello, progetto, avvio). Restituisce il path del context file.
    """
    home = _prepare_home(registry_path)
    path = _context_path(home, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        agent = _read_session(home, session_id) or {}
        header = (
            f"# Context sessione {session_id}\n\n"
            f"- provider: {agent.get('provider', '')}\n"
            f"- modello: {agent.get('ai_version', '')}\n"
            f"- progetto: {agent.get('project', '')}\n"
            f"- avvio: {agent.get('started_at', '')}\n"
        )
        path.write_text(header, encoding="utf-8")
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(f"\n- **{_now_rome()}** — {entry}\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    _schedule_git_sync(home, f"context log {session_id}")
    return path


def _git_push_log(since: str) -> list[str]:
    """Commit del repo corrente da `since` in poi (best-effort; [] su errore)."""
    if not since:
        return []
    try:
        result = subprocess.run(
            ["git", "log", "--format=%h %s", f"--since={since}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return [line for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    return []


def end_session(
    session_id: str,
    router: str = "",
    cosa: str | None = None,
    come: str | None = None,
    risolto: str | None = None,
    bug: list[str] | None = None,
    skill_tool: list[str] | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Chiude una sessione distillando il context in un wiki entry.

    Flusso: legge il context file, produce `wiki/<session_id>.md` (fonte di
    verità) compilando i campi dai dati di sessione (file_toccati da space,
    handoff dalla sessione, git_push via git log best-effort) e dai flag
    narrativi; upsert nel DB wiki; marca la sessione Finished e rilascia i
    lock residui. I campi narrativi non passati diventano "non documentato"
    e vengono segnalati nel campo issues della sessione.
    """
    home = _prepare_home(registry_path)
    agent = _read_session(home, session_id)
    if agent is None:
        return {"session_id": session_id, "ended": False, "error": "sessione non trovata"}

    context_path = _context_path(home, session_id)
    body = ""
    context_rel = ""
    if context_path.exists():
        body = context_path.read_text(encoding="utf-8")
        context_rel = f"contexts/{_context_filename(session_id)}"

    non_documentato = "non documentato"
    narrative: dict[str, Any] = {
        "cosa_fatto": cosa,
        "come_fatto": come,
        "problema_risolto": risolto,
        "bug_trovati": bug,
        "skill_tool_mcp": skill_tool,
    }
    undocumented = [
        name
        for name, value in narrative.items()
        if not value or (isinstance(value, str) and not value.strip())
    ]

    started_at = str(agent.get("started_at") or "")
    handoff_ref = str(agent.get("handoff") or "").strip()
    fields: dict[str, Any] = {
        "session_id": session_id,
        "provider": str(agent.get("provider") or ""),
        "modello": str(agent.get("ai_version") or ""),
        "data": started_at[:10] or _now_rome()[:10],
        "router": router or "",
        "cosa_fatto": (cosa or "").strip() or non_documentato,
        "come_fatto": (come or "").strip() or non_documentato,
        "problema_risolto": (risolto or "").strip() or non_documentato,
        "file_toccati": [str(p) for p in agent.get("space") or []],
        "git_push": _git_push_log(started_at),
        "handoff": [handoff_ref] if handoff_ref else [],
        "bug_trovati": list(bug) if bug else [non_documentato],
        "skill_tool_mcp": list(skill_tool) if skill_tool else [non_documentato],
    }

    wm = _wiki_manager_module()
    fields["id"] = wm.entry_id_for(session_id, home)
    wiki_path = wm.write_wiki_entry(session_id, fields, body, home)
    entry_id = wm.upsert_entry(
        session_id, {**fields, "context_md": context_rel}, home
    )

    locks = [str(p) for p in agent.get("do_not_touch") or []]
    _release_session_locks(session_id, locks)

    issues = str(agent.get("issues") or "").strip()
    if undocumented:
        note = "wiki: campi non documentati: " + ", ".join(undocumented)
        issues = f"{issues} | {note}" if issues else note
    update_session(
        session_id,
        registry_path=registry_path,
        status="Finished",
        do_not_touch=[],
        issues=issues,
    )
    _schedule_git_sync(home, f"end {session_id}")
    return {
        "session_id": session_id,
        "ended": True,
        "entry_id": entry_id,
        "wiki_path": str(wiki_path),
        "undocumented": undocumented,
        "locks_released": locks,
    }


def _age_string(started_at: str) -> str:
    """Età leggibile ('5m', '2h 10m', '3d 1h') calcolata da started_at (formato Roma)."""
    try:
        started = datetime.strptime(started_at, "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return "?"
    now = datetime.now()
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None)
    except Exception:
        pass
    seconds = max(int((now - started).total_seconds()), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _sync_status_line() -> str:
    """Riga di stato del git-sync per il comando status (best-effort, mai bloccante)."""
    try:
        scripts_dir = str(Path(__file__).parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import sync_manager

        status = sync_manager.get_sync_status(get_registry_home())
    except Exception:
        return "git-sync: stato non disponibile"
    if not status.get("enabled"):
        return "git-sync: disabilitato"
    parts = ["git-sync: attivo"]
    if status.get("last_sync_at"):
        parts.append(f"ultimo sync: {status['last_sync_at']}")
    if status.get("pending"):
        parts.append("sync in sospeso")
    if status.get("last_error"):
        parts.append(f"ultimo errore: {status['last_error']}")
    return " | ".join(parts)


def render_status(
    status_filter: str | None = None,
    provider_filter: str | None = None,
    project_filter: str | None = None,
    registry_path: Path | None = None,
) -> str:
    """Tabella human-readable delle sessioni (started_at desc) + stato git-sync."""
    agents = load_agents(registry_path)
    if status_filter:
        agents = [a for a in agents if a.get("status") == status_filter]
    if provider_filter:
        agents = [a for a in agents if a.get("provider") == provider_filter]
    if project_filter:
        agents = [a for a in agents if a.get("project") == project_filter]
    agents.sort(key=lambda a: str(a.get("started_at", "")), reverse=True)

    headers = ["SESSION ID", "PROVIDER", "PROGETTO", "WORKING ON", "STATUS", "ETÀ"]
    rows = [
        [
            str(a.get("session_id", "")),
            str(a.get("provider", "")),
            str(a.get("project") or "-"),
            str(a.get("working_on", "")),
            str(a.get("status", "")),
            _age_string(str(a.get("started_at", ""))),
        ]
        for a in agents
    ]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    if not rows:
        lines.append("(nessuna sessione)")
    lines.append(_sync_status_line())
    return "\n".join(lines)


def _split_csv(value: str | None) -> list[str]:
    """Parsa una lista CSV da CLI ('a, b' -> ['a', 'b'])."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="registry_manager.py",
        description="Gestione del registry condiviso agent-registry.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("register", help="registra una nuova sessione")
    p.add_argument("session_id")
    p.add_argument("provider")
    p.add_argument("ai_version")
    p.add_argument("working_on")
    p.add_argument("--space", help="lista CSV di file/aree di lavoro")
    p.add_argument("--todo-present", dest="todo_present", help="lista CSV todo correnti")

    p = sub.add_parser("update", help="aggiorna i campi di una sessione")
    p.add_argument("session_id")
    p.add_argument("--working-on", dest="working_on")
    p.add_argument("--todo-past", dest="todo_past", help="lista CSV")
    p.add_argument("--todo-present", dest="todo_present", help="lista CSV")
    p.add_argument("--todo-future", dest="todo_future", help="lista CSV")
    p.add_argument("--space", help="lista CSV")
    p.add_argument("--do-not-touch", dest="do_not_touch", help="lista CSV")
    p.add_argument("--issues")
    p.add_argument("--status")

    p = sub.add_parser("finish", help="marca la sessione Finished e svuota i lock")
    p.add_argument("session_id")

    p = sub.add_parser("handoff", help="registra il riferimento a un handoff")
    p.add_argument("session_id")
    p.add_argument("handoff_path")

    sub.add_parser("show", help="mostra le sessioni come dict (una per riga)")

    p = sub.add_parser("status", help="tabella human-readable delle sessioni + git-sync")
    p.add_argument("--status", dest="status_filter")
    p.add_argument("--provider", dest="provider_filter")
    p.add_argument("--project", dest="project_filter")

    sub.add_parser("cleanup", help="marca Stop le sessioni zombie e rilascia i lock")

    p = sub.add_parser("kill", help="termina la sessione (kill reale via PID o stop logico)")
    p.add_argument("session_id")
    p.add_argument(
        "--force",
        action="store_true",
        help="salta la verifica anti-riuso PID sulla cmdline",
    )

    p = sub.add_parser("context", help="gestione del context file di sessione")
    csub = p.add_subparsers(dest="context_command", required=True)
    plog = csub.add_parser("log", help="appende una entry al context file")
    plog.add_argument("session_id")
    plog.add_argument("entry")

    p = sub.add_parser("end", help="distilla il context in wiki entry e chiude la sessione")
    p.add_argument("session_id")
    p.add_argument("--router", default="", help="descrizione breve per il retrieval")
    p.add_argument("--cosa", help="cosa è stato fatto")
    p.add_argument("--come", help="come è stato fatto")
    p.add_argument("--risolto", help="problema risolto")
    p.add_argument("--bug", help="lista CSV di bug trovati")
    p.add_argument("--skill-tool", dest="skill_tool", help="lista CSV skill/tool/MCP usati")

    p = sub.add_parser("wiki", help="gestione del DB wiki")
    wsub = p.add_subparsers(dest="wiki_command", required=True)
    wsub.add_parser("rebuild", help="ricostruisce il DB dai file wiki/*.md")

    pq = wsub.add_parser("query", help="router: il lavoro è già stato svolto in passato?")
    pq.add_argument("domanda")

    ps = wsub.add_parser("show", help="mostra l'entry wiki completa (id o session_id)")
    ps.add_argument("identifier")

    pi = wsub.add_parser("ingest", help="ingestisce un wiki entry via LLM (genera il router)")
    pi.add_argument("session_id")

    wsub.add_parser("ingest-pending", help="ingestisce tutte le entry pending_ingest")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI. Restituisce l'exit code."""
    args = _build_parser().parse_args(argv)

    if args.command == "register":
        agent = register_session(
            session_id=args.session_id,
            provider=args.provider,
            ai_version=args.ai_version,
            working_on=args.working_on,
            space=_split_csv(args.space),
            todo_present=_split_csv(args.todo_present),
        )
        print(f"Registrato: {agent['session_id']}")
    elif args.command == "update":
        kwargs: dict[str, Any] = {}
        if args.working_on is not None:
            kwargs["working_on"] = args.working_on
        if args.issues is not None:
            kwargs["issues"] = args.issues
        if args.status is not None:
            kwargs["status"] = args.status
        if args.space is not None:
            kwargs["space"] = _split_csv(args.space)
        if args.do_not_touch is not None:
            kwargs["do_not_touch"] = _split_csv(args.do_not_touch)
        todo: dict[str, list[str]] = {}
        if args.todo_past is not None:
            todo["past"] = _split_csv(args.todo_past)
        if args.todo_present is not None:
            todo["present"] = _split_csv(args.todo_present)
        if args.todo_future is not None:
            todo["future"] = _split_csv(args.todo_future)
        if todo:
            kwargs["todo"] = todo
        if not kwargs:
            print("Nessun campo da aggiornare.", file=sys.stderr)
            return 1
        if update_session(args.session_id, **kwargs) is None:
            print(f"Sessione '{args.session_id}' non trovata.", file=sys.stderr)
            return 1
        print("Aggiornato.")
    elif args.command == "finish":
        if unregister_session(args.session_id) is None:
            print(f"Sessione '{args.session_id}' non trovata.", file=sys.stderr)
            return 1
        print("Sessione terminata.")
    elif args.command == "handoff":
        if add_handoff_ref(args.session_id, args.handoff_path) is None:
            print(f"Sessione '{args.session_id}' non trovata.", file=sys.stderr)
            return 1
        print("Handoff registrato.")
    elif args.command == "show":
        for agent in load_agents():
            print(agent)
    elif args.command == "status":
        print(
            render_status(
                status_filter=args.status_filter,
                provider_filter=args.provider_filter,
                project_filter=args.project_filter,
            )
        )
    elif args.command == "cleanup":
        cleaned = cleanup_sessions()
        suffix = f": {', '.join(cleaned)}" if cleaned else ""
        print(f"Cleanup: {len(cleaned)} sessioni zombie marcate Stop{suffix}")
    elif args.command == "kill":
        result = kill_session(args.session_id, force=args.force)
        if not result.get("killed"):
            print(result.get("error", "errore"), file=sys.stderr)
            return 1
        if result["terminated"]:
            print(f"Sessione {args.session_id}: processo terminato, stato Killed.")
        else:
            print(f"Sessione {args.session_id}: Killed. {result['note']}")
    elif args.command == "context":
        if args.context_command == "log":
            path = log_context(args.session_id, args.entry)
            print(f"Context aggiornato: {path}")
    elif args.command == "end":
        result = end_session(
            args.session_id,
            router=args.router,
            cosa=args.cosa,
            come=args.come,
            risolto=args.risolto,
            bug=_split_csv(args.bug),
            skill_tool=_split_csv(args.skill_tool),
        )
        if not result.get("ended"):
            print(result.get("error", "errore"), file=sys.stderr)
            return 1
        print(
            f"Sessione {args.session_id}: Finished, wiki entry #{result['entry_id']} "
            f"({result['wiki_path']})."
        )
        if result["undocumented"]:
            print("Campi non documentati: " + ", ".join(result["undocumented"]))
    elif args.command == "wiki":
        if args.wiki_command == "rebuild":
            count = _wiki_manager_module().rebuild()
            print(f"Wiki DB ricostruito: {count} entry indicizzati.")
        elif args.wiki_command == "query":
            result = _wiki_ingest_module().router_query(args.domanda)
            print(result["messaggio"])
        elif args.wiki_command == "show":
            entry = _wiki_ingest_module().show_entry(args.identifier)
            if entry is None:
                print(f"Entry wiki '{args.identifier}' non trovata.", file=sys.stderr)
                return 1
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        elif args.wiki_command == "ingest":
            wi = _wiki_ingest_module()
            result = wi.ingest_entry(args.session_id)
            if not result["ingested"]:
                print(
                    f"Ingestione non riuscita ({result['error']}); "
                    "entry intatta, status pending_ingest.",
                    file=sys.stderr,
                )
                return 1
            print(f"Entry #{result['entry_id']} ingerita. router: {result['router']}")
        elif args.wiki_command == "ingest-pending":
            results = _wiki_ingest_module().ingest_pending()
            failures = sum(1 for r in results if not r["ingested"])
            for r in results:
                if r["ingested"]:
                    print(f"{r['session_id']}: ingerita.")
                else:
                    print(f"{r['session_id']}: {r['error']}", file=sys.stderr)
            if not results:
                print("Nessuna entry pending_ingest.")
            return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
